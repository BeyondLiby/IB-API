from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re

import pandas as pd


POSITION_FILENAME = "carry_dashboard_positions.csv"
CHAIN_FILENAME = "carry_dashboard_chain.csv"
BARS_FILENAME = "carry_dashboard_bars.csv"
BARS_COLUMNS = ["symbol", "month", "localSymbol", "date", "open", "high", "low", "close", "volume", "dateChina"]
DEFAULT_AS_OF = "now"
SUPPORTED_PRODUCTS = ("ZF", "ZN", "ZC", "ZT", "TN", "ZB", "UB")
PRODUCT_PATTERN = "|".join(SUPPORTED_PRODUCTS)


class _HtmlTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
            self._in_cell = True

    def handle_data(self, data: str) -> None:
        if self._in_cell and self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell and self._row is not None:
            self._row.append("".join(self._cell or []).strip())
            self._cell = None
            self._in_cell = False
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None


def _safe_read_csv(path: str | Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _ensure_bars_schema(frame: pd.DataFrame | None) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(columns=BARS_COLUMNS)
    out = frame.copy()
    canonical = {str(column).strip().lower(): column for column in out.columns}
    aliases = {
        "date": ["date", "datetime", "time", "timestamp"],
        "open": ["open", "o"],
        "high": ["high", "h"],
        "low": ["low", "l"],
        "close": ["close", "c", "last"],
        "volume": ["volume", "vol"],
    }
    for target, names in aliases.items():
        if target in out.columns:
            continue
        source = next((canonical[name] for name in names if name in canonical), None)
        if source is not None:
            out[target] = out[source]
    for column in BARS_COLUMNS:
        if column not in out.columns:
            out[column] = pd.NA
    return out


def _read_html_table(path: str | Path) -> pd.DataFrame:
    parser = _HtmlTableParser()
    parser.feed(Path(path).read_text(encoding="utf-8", errors="replace"))
    if len(parser.rows) < 2:
        return pd.DataFrame()

    header = parser.rows[0]
    data = parser.rows[1:]
    if header and header[0] == "":
        header = header[1:]
        data = [row[1:] if len(row) == len(header) + 1 else row for row in data]

    normalized: list[list[str]] = []
    for row in data:
        if len(row) < len(header):
            row = row + [""] * (len(header) - len(row))
        elif len(row) > len(header):
            row = row[: len(header)]
        if row and all(value in {"", "..."} for value in row):
            continue
        normalized.append(row)
    return pd.DataFrame(normalized, columns=header)


def _read_tabular_input(path: str | Path) -> pd.DataFrame:
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    if "<table" in text.lower():
        return _read_html_table(path)
    return _safe_read_csv(path)


PRODUCT_RE = re.compile(
    rf"\b({PRODUCT_PATTERN})\b|^({PRODUCT_PATTERN})[-\s_]|^({PRODUCT_PATTERN})[FGHJKMNQUVXZ]\d\b"
)


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _product_from_row(row: pd.Series) -> str:
    for column in ["product", "root", "symbol", "tradingClass"]:
        if column in row.index:
            text = _clean_text(row[column]).upper()
            if text in SUPPORTED_PRODUCTS:
                return text
    for column in ["localSymbol", "optionName", "contract", "description", "name", "conId"]:
        if column not in row.index:
            continue
        text = _clean_text(row[column]).upper()
        if not text:
            continue
        match = PRODUCT_RE.search(text)
        if match:
            return match.group(1) or match.group(2) or match.group(3) or ""
        if text.startswith("OZN"):
            return "ZN"
        if text.startswith("OZC"):
            return "ZC"
    return ""


def _product_series(frame: pd.DataFrame) -> pd.Series:
    if frame.empty:
        return pd.Series(dtype="object")
    return frame.apply(_product_from_row, axis=1).astype("object")


def _infer_product_from_path(path: str | Path) -> str:
    text = Path(path).name.upper()
    match = PRODUCT_RE.search(text)
    return "" if not match else (match.group(1) or match.group(2) or match.group(3) or "")


def _read_csv_inputs(paths: str | Path) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for part in str(paths).replace(";", ",").split(","):
        text = part.strip()
        if not text:
            continue
        frame = _read_tabular_input(text)
        inferred = _infer_product_from_path(text)
        if inferred and not frame.empty:
            row_products = _product_series(frame)
            if "product" not in frame.columns:
                frame["product"] = ""
            frame.loc[row_products.isin(["", inferred]), "product"] = inferred
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _row_count_by_product(frame: pd.DataFrame) -> dict[str, int]:
    products = _product_series(frame)
    if products.empty:
        return {}
    return products.replace({"": "UNKNOWN"}).value_counts().sort_index().to_dict()


def _date_range(frame: pd.DataFrame) -> tuple[str, str]:
    if frame.empty:
        return "", ""
    for date_col in ["dateChina", "date", "time", "datetime", "timestamp"]:
        if date_col not in frame.columns:
            continue
        values = pd.to_datetime(frame[date_col], errors="coerce").dropna()
        if not values.empty:
            return str(values.min()), str(values.max())
    return "", ""


def _date_range_by_product(frame: pd.DataFrame) -> dict[str, dict[str, str]]:
    if frame.empty:
        return {}
    products = _product_series(frame)
    ranges: dict[str, dict[str, str]] = {}
    for product, group in frame.assign(_product=products).groupby("_product", dropna=False):
        start, end = _date_range(group)
        ranges[str(product or "UNKNOWN")] = {"start": start, "end": end}
    return ranges


def _timestamp_range(frame: pd.DataFrame, columns: list[str]) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if frame.empty:
        return None, None
    for date_col in columns:
        if date_col not in frame.columns:
            continue
        values = pd.to_datetime(frame[date_col], errors="coerce", utc=True).dropna()
        if not values.empty:
            return values.min(), values.max()
    return None, None


def _timestamp_range_by_product(frame: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, object]]:
    if frame.empty:
        return {}
    products = _product_series(frame)
    ranges: dict[str, dict[str, object]] = {}
    for product, group in frame.assign(_product=products).groupby("_product", dropna=False):
        start, end = _timestamp_range(group, columns)
        ranges[str(product or "UNKNOWN")] = {
            "start": "" if start is None else str(start),
            "end": "" if end is None else str(end),
            "end_timestamp": end,
        }
    return ranges


def _as_of_timestamp(value: str | pd.Timestamp | None) -> pd.Timestamp:
    if value is None or str(value).lower() == DEFAULT_AS_OF:
        return pd.Timestamp.now(tz="UTC")
    ts = pd.to_datetime(value, errors="raise", utc=True)
    if isinstance(ts, pd.DatetimeIndex):
        return ts[0]
    return ts


def _split_products(value: str | None) -> list[str]:
    if not value:
        return []
    return sorted({part.strip().upper() for part in str(value).replace(";", ",").split(",") if part.strip()})


def _latest_file(paths: list[Path]) -> Path | None:
    existing = [path for path in paths if path.exists() and path.is_file()]
    if not existing:
        return None
    return max(existing, key=lambda path: (path.stat().st_mtime, path.name))


def discover_latest_carry_dashboard_inputs(
    input_dir: str | Path = "data/clean_verify",
    *,
    output_dir: str | Path = "data",
    products: str | list[str] = "ZF,ZN,ZC",
    positions_path: str | Path | None = None,
    chain_path: str | Path | None = None,
    bars_path: str | Path | None = None,
) -> dict[str, object]:
    """Find the latest notebook output CSVs that can be published to the HTML dashboard."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    wanted = (
        sorted({str(product).upper() for product in products})
        if isinstance(products, list)
        else _split_products(products)
    )

    if positions_path:
        positions = Path(positions_path)
    else:
        positions = _latest_file([input_dir / "dashboard_treasury_positions.csv", input_dir / "treasury_positions.csv"])
        if positions is None:
            positions = _latest_file([output_dir / POSITION_FILENAME])
    if positions is None:
        raise FileNotFoundError(f"No positions CSV found under {input_dir} or {output_dir}")

    if chain_path:
        chain_paths = [Path(part.strip()) for part in str(chain_path).replace(";", ",").split(",") if part.strip()]
    else:
        chain_paths = []
        for product in wanted:
            latest = _latest_file(sorted(input_dir.glob(f"{product}_FOP_Static_*_monitor_frame.csv")))
            if latest is not None:
                chain_paths.append(latest)
        if not chain_paths:
            fallback = _latest_file([input_dir / "chain_monitor.csv"])
            if fallback is None:
                fallback = _latest_file([output_dir / CHAIN_FILENAME])
            if fallback is not None:
                chain_paths.append(fallback)
    if not chain_paths:
        raise FileNotFoundError(f"No option-chain monitor CSV found under {input_dir}")

    if bars_path:
        bars = Path(bars_path)
    else:
        bars = _latest_file([input_dir / BARS_FILENAME])
        if bars is None:
            bars = _latest_file([output_dir / BARS_FILENAME])

    return {
        "positions": positions,
        "chain": chain_paths,
        "chain_arg": ",".join(str(path) for path in chain_paths),
        "bars": bars,
        "bars_arg": "" if bars is None else str(bars),
        "products": wanted,
        "expected_products": ",".join(wanted),
    }


def validate_carry_dashboard_files(
    data_dir: str | Path = "data",
    *,
    expected_products: str | list[str] | None = None,
    min_chain_rows: int = 50,
    min_bars_rows: int = 100,
    max_chain_age_hours: float = 24.0,
    max_bars_age_hours: float = 72.0,
    as_of: str | pd.Timestamp | None = DEFAULT_AS_OF,
) -> dict[str, object]:
    """Summarize the stable CSV files consumed by carry_risk_dashboard.html."""
    data_dir = Path(data_dir)
    paths = {
        "positions": data_dir / POSITION_FILENAME,
        "chain": data_dir / CHAIN_FILENAME,
        "bars": data_dir / BARS_FILENAME,
    }
    frames = {
        name: _safe_read_csv(path) if path.exists() else pd.DataFrame()
        for name, path in paths.items()
    }
    chain = frames["chain"]
    bars = frames["bars"]
    by_product = {name: _row_count_by_product(frame) for name, frame in frames.items()}
    expirations: dict[str, list[str]] = {}
    expiry_col = next((col for col in ["expiry", "expiration", "lastTradeDateOrContractMonth"] if col in chain.columns), "")
    if not chain.empty and expiry_col:
        products = _product_series(chain)
        for product, group in chain.assign(_product=products).groupby("_product", dropna=False):
            values = sorted({str(value) for value in group[expiry_col].dropna().astype(str)})
            expirations[str(product or "UNKNOWN")] = values[:10]
    start, end = _date_range(bars)
    as_of_ts = _as_of_timestamp(as_of)
    chain_time_ranges = _timestamp_range_by_product(chain, ["snapshotTimeUtc", "snapshotTime", "timestamp"])
    bars_time_ranges = _timestamp_range_by_product(bars, ["dateChina", "date", "time", "datetime", "timestamp"])
    expected = (
        sorted({str(product).upper() for product in expected_products})
        if isinstance(expected_products, list)
        else _split_products(expected_products)
    )
    discovered = sorted(
        {
            product
            for counts in by_product.values()
            for product in counts
            if product and product != "UNKNOWN"
        }
    )
    products = sorted(set(expected) | set(discovered))
    product_status: dict[str, dict[str, object]] = {}
    for product in products:
        position_rows = int(by_product["positions"].get(product, 0))
        chain_rows = int(by_product["chain"].get(product, 0))
        bars_rows = int(by_product["bars"].get(product, 0))
        has_chain_rows = chain_rows > 0
        has_bars_rows = bars_rows > 0
        chain_end = chain_time_ranges.get(product, {}).get("end_timestamp")
        bars_end = bars_time_ranges.get(product, {}).get("end_timestamp")
        chain_age_hours = None if chain_end is None else max((as_of_ts - chain_end).total_seconds() / 3600, 0.0)
        bars_age_hours = None if bars_end is None else max((as_of_ts - bars_end).total_seconds() / 3600, 0.0)
        chain_fresh = chain_age_hours is not None and chain_age_hours <= float(max_chain_age_hours)
        bars_fresh = bars_age_hours is not None and bars_age_hours <= float(max_bars_age_hours)
        has_full_chain = chain_rows >= int(min_chain_rows) and chain_fresh
        has_bars = bars_rows >= int(min_bars_rows) and bars_fresh
        product_status[product] = {
            "positions": position_rows,
            "chain": chain_rows,
            "bars": bars_rows,
            "has_positions": position_rows > 0,
            "has_chain_rows": has_chain_rows,
            "chain_fresh": chain_fresh,
            "chain_age_hours": chain_age_hours,
            "has_full_chain": has_full_chain,
            "has_bars_rows": has_bars_rows,
            "bars_fresh": bars_fresh,
            "bars_age_hours": bars_age_hours,
            "has_bars": has_bars,
            "chain_view": "standard_chain" if has_full_chain else ("stale_chain" if has_chain_rows and not chain_fresh else ("partial_chain" if has_chain_rows else ("position_fallback" if position_rows > 0 else "missing"))),
            "chain_time_range": {
                "start": chain_time_ranges.get(product, {}).get("start", ""),
                "end": chain_time_ranges.get(product, {}).get("end", ""),
            },
            "bars_range": _date_range_by_product(bars).get(product, {"start": "", "end": ""}),
        }
    products_with_positions = [product for product, status in product_status.items() if status["has_positions"]]
    missing_chain = [
        product
        for product in products_with_positions
        if not bool(product_status[product]["has_full_chain"])
    ]
    missing_bars = [
        product
        for product in products_with_positions
        if not bool(product_status[product]["has_bars"])
    ]
    ready_for_full_view = not missing_chain and not missing_bars
    return {
        "paths": {name: str(path) for name, path in paths.items()},
        "exists": {name: path.exists() for name, path in paths.items()},
        "rows": {name: len(frame) for name, frame in frames.items()},
        "by_product": by_product,
        "products": products,
        "product_status": product_status,
        "readiness": {
            "html_inputs_exist": all(path.exists() for path in paths.values()),
            "criteria": {
                "min_chain_rows": int(min_chain_rows),
                "min_bars_rows": int(min_bars_rows),
                "max_chain_age_hours": float(max_chain_age_hours),
                "max_bars_age_hours": float(max_bars_age_hours),
                "as_of": str(as_of_ts),
            },
            "products_with_positions": products_with_positions,
            "missing_full_chain": missing_chain,
            "missing_bars": missing_bars,
            "ready_for_full_view": ready_for_full_view,
            "ready_for_full_zf_zn_view": ready_for_full_view,
        },
        "chain_expirations": expirations,
        "bars_range": {"start": start, "end": end},
        "bars_range_by_product": _date_range_by_product(bars),
    }


def sync_carry_dashboard_files(
    positions_path: str | Path,
    chain_path: str | Path,
    *,
    bars_path: str | Path | None = None,
    output_dir: str | Path = "data",
) -> dict[str, Path]:
    """Copy generated position and chain snapshots to stable HTML input files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    positions = _read_csv_inputs(positions_path)
    chain = _read_csv_inputs(chain_path)
    bars = _read_csv_inputs(bars_path) if bars_path else None

    paths = {
        "positions": output_dir / POSITION_FILENAME,
        "chain": output_dir / CHAIN_FILENAME,
    }
    positions.to_csv(paths["positions"], index=False, encoding="utf-8-sig")
    chain.to_csv(paths["chain"], index=False, encoding="utf-8-sig")
    if bars is not None:
        paths["bars"] = output_dir / BARS_FILENAME
        _ensure_bars_schema(bars).to_csv(paths["bars"], index=False, encoding="utf-8-sig")
    else:
        bars_output = output_dir / BARS_FILENAME
        if not bars_output.exists():
            paths["bars"] = bars_output
            _ensure_bars_schema(None).to_csv(bars_output, index=False, encoding="utf-8-sig")
    return paths


def write_carry_dashboard_files(
    positions: pd.DataFrame,
    chain: pd.DataFrame,
    *,
    bars: pd.DataFrame | None = None,
    output_dir: str | Path = "data",
) -> dict[str, Path]:
    """Write in-memory notebook frames to stable HTML input files."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    paths = {
        "positions": output_dir / POSITION_FILENAME,
        "chain": output_dir / CHAIN_FILENAME,
    }
    positions.to_csv(paths["positions"], index=False, encoding="utf-8-sig")
    chain.to_csv(paths["chain"], index=False, encoding="utf-8-sig")
    bars_output = output_dir / BARS_FILENAME
    if bars is not None:
        paths["bars"] = bars_output
        _ensure_bars_schema(bars).to_csv(bars_output, index=False, encoding="utf-8-sig")
    elif not bars_output.exists():
        paths["bars"] = bars_output
        _ensure_bars_schema(None).to_csv(bars_output, index=False, encoding="utf-8-sig")
    return paths
