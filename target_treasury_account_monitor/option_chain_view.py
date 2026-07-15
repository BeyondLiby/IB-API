from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from treasury_fop_chain import (
    build_treasury_fop_universe,
    discover_future_months,
    filter_contracts_by_moneyness,
    get_future_prices_for_months,
    load_universe,
    save_universe,
    snapshot_in_batches,
)

try:
    from .utils import clean_number, is_valid_number
except ImportError:
    from utils import clean_number, is_valid_number


def chain_cache_path(root: str, months: list[str], cache_dir: str | Path = "data") -> Path:
    """Return the cache path for a qualified futures-option universe."""
    month_key = "_".join(str(month) for month in months) if months else "none"
    return Path(cache_dir) / f"{root.upper()}_FOP_Universe_{month_key}.csv"


def month_list(value: Any) -> list[str]:
    """Normalize manual future-month input into YYYYMM strings."""
    if value is None:
        return []
    if isinstance(value, str):
        parts = re.split(r"[\s,;]+", value.strip())
    else:
        parts = [str(item).strip() for item in value]
    return [part for part in parts if re.fullmatch(r"\d{6}", part)]


def find_cached_universe(root: str, *, cache_dir: str | Path = "data", min_month: str | None = None) -> Path | None:
    """Find the newest local universe cache for a root in data/ or repo root."""
    root = root.upper()
    candidates: list[Path] = []
    for base in [Path(cache_dir), Path(".")]:
        candidates.extend(base.glob(f"{root}_FOP_Universe_*.csv"))
    candidates = [path for path in candidates if not path.stem.endswith("_chain_summary")]
    if not candidates:
        return None

    def score(path: Path) -> tuple[int, float]:
        months = [part for part in path.stem.split("_") if re.fullmatch(r"\d{6}", part)]
        if min_month:
            months = [month for month in months if month >= str(min_month)]
        latest_month = max([int(month) for month in months], default=0)
        return latest_month, path.stat().st_mtime

    return sorted(candidates, key=score, reverse=True)[0]


def months_from_metadata(metadata: pd.DataFrame, *, min_month: str | None = None, max_count: int | None = None) -> list[str]:
    """Read underlying months from a universe metadata frame."""
    if metadata.empty or "underlyingMonth" not in metadata.columns:
        return []
    months = sorted({str(month) for month in metadata["underlyingMonth"].dropna().astype(str) if re.fullmatch(r"\d{6}", str(month))})
    if min_month:
        months = [month for month in months if month >= str(min_month)]
    return months[:max_count] if max_count else months


def safe_future_prices(
    ib: Any,
    root: str,
    months: list[str],
    *,
    market_data_type: int | None = None,
) -> pd.DataFrame:
    """Fetch future prices without failing the whole chain snapshot on one bad month."""
    try:
        return get_future_prices_for_months(
            ib,
            root,
            months,
            market_data_type=market_data_type,
            wait_seconds=2.0,
            raise_on_missing=False,
        )
    except Exception as exc:
        return pd.DataFrame(
            [
                {
                    "root": root,
                    "month": month,
                    "price": math.nan,
                    "priceSource": "",
                    "error": str(exc),
                }
                for month in months
            ]
        )


def price_from_row(row: pd.Series) -> tuple[float, str]:
    """Pick a usable option price from a snapshot row."""
    bid = clean_number(row.get("bid", math.nan))
    ask = clean_number(row.get("ask", math.nan))
    mid = (bid + ask) / 2.0 if is_valid_number(bid) and is_valid_number(ask) else math.nan
    candidates = [
        ("mid", mid),
        ("last", row.get("last", math.nan)),
        ("mark", row.get("markPrice", math.nan)),
        ("close", row.get("close", math.nan)),
        ("model", row.get("optPrice", math.nan)),
        ("bid", row.get("bid", math.nan)),
        ("ask", row.get("ask", math.nan)),
    ]
    for source, value in candidates:
        number = clean_number(value)
        if is_valid_number(number, allow_zero=False):
            return number, source
    return math.nan, ""


def snapshot_to_monitor_frame(snapshot: pd.DataFrame, *, root: str) -> pd.DataFrame:
    """Normalize option-chain snapshots into the carry-planner schema."""
    if snapshot.empty:
        return pd.DataFrame()
    out = snapshot.copy()
    prices = out.apply(price_from_row, axis=1)
    out["price"] = [item[0] for item in prices]
    out["priceSource"] = [item[1] for item in prices]
    out["expiry"] = out["expiration"].astype(str)
    out["symbol"] = root.upper()
    out["secType"] = "FOP"
    out["position"] = 0.0
    out["dte"] = out["expiry"].map(expiry_days_from_today)
    # The planning schema expresses ZC prices in cents per bushel, so it needs
    # the USD value of one displayed cent (USD 50), not IB's 5,000 bushels.
    if root.upper() == "ZC":
        out["multiplier"] = 50.0
    elif "multiplier" not in out.columns:
        out["multiplier"] = 1000.0
    else:
        out["multiplier"] = pd.to_numeric(out["multiplier"], errors="coerce").fillna(1000.0)

    if "iv" not in out.columns:
        out["iv"] = math.nan
    if "delta" not in out.columns:
        out["delta"] = math.nan
    if "gamma" not in out.columns:
        out["gamma"] = math.nan
    if "theta" not in out.columns:
        out["theta"] = math.nan

    out["greekSource"] = ""
    if "modelGreeks_delta" in out.columns:
        out.loc[pd.to_numeric(out["modelGreeks_delta"], errors="coerce").notna(), "greekSource"] = "modelGreeks"
    out.loc[(out["greekSource"] == "") & pd.to_numeric(out["delta"], errors="coerce").notna(), "greekSource"] = "primary"

    def missing_notes(row: pd.Series) -> str:
        notes: list[str] = []
        if not is_valid_number(row.get("price", math.nan)):
            notes.append("quote unavailable")
        if not is_valid_number(row.get("delta", math.nan)):
            notes.append("greeks unavailable")
        if not is_valid_number(row.get("openInterest", math.nan)):
            notes.append("open interest unavailable")
        return "; ".join(notes)

    out["missingData"] = out.apply(missing_notes, axis=1)
    quality = out.apply(classify_option_chain_quality, axis=1)
    out["qualityLabel"] = [item[0] for item in quality]
    out["qualityReason"] = [item[1] for item in quality]
    return out


def expiry_days_from_today(expiry: Any, *, timezone: str = "America/New_York") -> int | None:
    """Return calendar DTE for an IB YYYYMMDD expiration."""
    exp_dt = pd.to_datetime(str(expiry)[:8], format="%Y%m%d", errors="coerce")
    if pd.isna(exp_dt):
        return None
    today = pd.Timestamp.now(tz=timezone).normalize().tz_localize(None)
    return int((exp_dt - today).days)


def classify_option_chain_quality(row: pd.Series) -> tuple[str, str]:
    """Classify option-chain rows so illiquid quotes are not mixed with bad pulls."""
    bid = clean_number(row.get("bid", math.nan))
    ask = clean_number(row.get("ask", math.nan))
    price = clean_number(row.get("price", math.nan))
    delta = clean_number(row.get("delta", math.nan))
    dte_value = row.get("dte", math.nan)
    try:
        dte = int(dte_value) if not pd.isna(dte_value) else None
    except (TypeError, ValueError):
        dte = None

    strike = clean_number(row.get("strike", math.nan))
    underlying = clean_number(row.get("undPrice", row.get("underlyingPrice", math.nan)))
    distance = abs(strike - underlying) if is_valid_number(strike) and is_valid_number(underlying) else math.nan

    has_bid = is_valid_number(bid, allow_zero=True)
    has_ask = is_valid_number(ask, allow_zero=True)
    has_price = is_valid_number(price)
    has_greeks = is_valid_number(delta)

    if has_bid and has_ask and has_greeks:
        return "ok", "bid/ask and greeks available"
    if not has_bid and has_ask and ask <= 0.01:
        reason = "bid missing; tiny ask <= 0.01"
        if has_greeks:
            reason += "; greeks available"
        else:
            reason += "; greeks unavailable"
        return "ask_only_tiny", reason
    if not has_bid and not has_ask:
        if dte == 0:
            return "no_quote_0dte", "0DTE contract has no bid/ask"
        if is_valid_number(distance) and distance >= 3.0:
            return "no_quote_far_otm", f"no bid/ask; strike distance {distance:.2f}"
        return "no_quote", "no bid/ask"
    if has_price and not has_greeks:
        return "missing_greeks", "quote available but greeks unavailable"
    if has_greeks and not has_price:
        return "greeks_only", "greeks available but quote unavailable"
    return "partial", "partial quote or analytics"


def filter_universe_by_expiration(
    contracts: list[Any],
    metadata: pd.DataFrame,
    *,
    min_expiration: str | None = None,
    max_expiration: str | None = None,
) -> tuple[list[Any], pd.DataFrame]:
    """Apply an expiration range to a cached or freshly built universe."""
    if metadata.empty or (not min_expiration and not max_expiration):
        return contracts, metadata
    out = metadata.copy()
    expirations = out["lastTradeDateOrContractMonth"].astype(str)
    mask = pd.Series(True, index=out.index)
    if min_expiration:
        mask &= expirations >= str(min_expiration)
    if max_expiration:
        mask &= expirations <= str(max_expiration)
    out = out[mask].copy()
    con_ids = set(pd.to_numeric(out["conId"], errors="coerce").dropna().astype(int).tolist())
    filtered_contracts = [contract for contract in contracts if int(getattr(contract, "conId", 0) or 0) in con_ids]
    return filtered_contracts, out.reset_index(drop=True)


def filter_universe_by_underlying_month(
    contracts: list[Any],
    metadata: pd.DataFrame,
    *,
    months: list[str],
) -> tuple[list[Any], pd.DataFrame]:
    """Keep only contracts whose metadata belongs to requested future months."""
    if metadata.empty or "underlyingMonth" not in metadata.columns or not months:
        return contracts, metadata
    month_set = {str(month) for month in months}
    out = metadata[metadata["underlyingMonth"].astype(str).isin(month_set)].copy()
    con_ids = set(pd.to_numeric(out["conId"], errors="coerce").dropna().astype(int).tolist())
    filtered_contracts = [contract for contract in contracts if int(getattr(contract, "conId", 0) or 0) in con_ids]
    return filtered_contracts, out.reset_index(drop=True)


def fetch_zf_option_chain_snapshot(
    ib: Any,
    *,
    root: str = "ZF",
    market_data_type: int | None = None,
    future_months: list[str] | str | None = None,
    max_future_months: int = 2,
    min_month: str | None = None,
    min_expiration: str | None = None,
    max_expiration: str | None = None,
    full_chain_snapshot: bool = False,
    dte0_width: float = 2.0,
    non_dte0_width: float = 5.0,
    batch_size: int = 150,
    wait_max_seconds: float = 12.0,
    wait_stable_seconds: float = 2.0,
    request_interval: float = 0.025,
    force_rebuild_universe: bool = False,
    cache_dir: str | Path = "data",
) -> dict[str, Any]:
    """Discover ZF FOP contracts, optionally filter them, and capture a live snapshot."""
    root = root.upper()
    effective_min_expiration = (
        str(min_expiration)
        if min_expiration
        else pd.Timestamp.now(tz="America/New_York").strftime("%Y%m%d")
    )
    if market_data_type is not None:
        ib.reqMarketDataType(int(market_data_type))

    contracts: list[Any] | None = None
    metadata: pd.DataFrame | None = None
    chain_summary = pd.DataFrame()
    cache_path: Path | None = None
    universe_source = ""

    months = month_list(future_months)
    manual_months_requested = bool(months)
    if not months:
        try:
            months = discover_future_months(
                ib,
                root,
                min_month=min_month,
                max_count=int(max_future_months),
            )
        except Exception:
            months = []
    if not months:
        cache_path = find_cached_universe(root, cache_dir=cache_dir, min_month=min_month)
        if cache_path is not None:
            contracts, metadata = load_universe(cache_path)
            months = months_from_metadata(metadata, min_month=min_month, max_count=int(max_future_months))
            universe_source = "cache_discovery_fallback"
    if not months:
        raise RuntimeError(
            f"No {root} future months discovered from IB and no usable local universe cache was found. "
            "Pass future_months=['202609', '202612'] or rebuild the universe after IB connectivity is stable."
        )

    future_prices = safe_future_prices(ib, root, months, market_data_type=market_data_type)
    spot_by_month = {
        str(row["month"]): float(row["price"])
        for row in future_prices.to_dict("records")
        if is_valid_number(row.get("price", math.nan))
    }

    cache_path = cache_path or chain_cache_path(root, months, cache_dir=cache_dir)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    if contracts is not None and metadata is not None:
        pass
    elif cache_path.exists() and not force_rebuild_universe:
        contracts, metadata = load_universe(cache_path)
        chain_summary = pd.DataFrame()
        universe_source = "cache"
    elif not force_rebuild_universe and not manual_months_requested:
        fallback_cache_path = find_cached_universe(root, cache_dir=cache_dir, min_month=min_month)
        if fallback_cache_path is not None:
            contracts, metadata = load_universe(fallback_cache_path)
            cache_path = fallback_cache_path
            chain_summary = pd.DataFrame()
            universe_source = "cache_partial_fallback"
        else:
            contracts = None
            metadata = None
    else:
        contracts = None
        metadata = None
    if contracts is None or metadata is None:
        universe = build_treasury_fop_universe(
            ib,
            root=root,
            future_months=months,
            min_expiration=effective_min_expiration,
            max_expiration=max_expiration,
        )
        save_universe(universe, cache_path)
        contracts = universe.contracts
        metadata = universe.metadata
        chain_summary = universe.chain_summary
        universe_source = "ib"

    assert contracts is not None
    assert metadata is not None
    contracts, metadata = filter_universe_by_underlying_month(
        contracts,
        metadata,
        months=months,
    )
    contracts, metadata = filter_universe_by_expiration(
        contracts,
        metadata,
        min_expiration=effective_min_expiration,
        max_expiration=max_expiration,
    )

    if full_chain_snapshot:
        selected_contracts = contracts
        selected_metadata = metadata.copy()
        snapshot_scope = "full_chain"
    else:
        if spot_by_month:
            selected_contracts, selected_metadata = filter_contracts_by_moneyness(
                contracts,
                metadata,
                spot_by_underlying_month=spot_by_month,
                dte0_width=float(dte0_width),
                non_dte0_width=float(non_dte0_width),
            )
            snapshot_scope = "moneyness_filter"
        else:
            selected_contracts = contracts
            selected_metadata = metadata.copy()
            snapshot_scope = "full_chain_no_future_price_fallback"

    snapshot = snapshot_in_batches(
        ib,
        selected_contracts,
        batch_size=int(batch_size),
        wait_max_seconds=float(wait_max_seconds),
        wait_stable_seconds=float(wait_stable_seconds),
        request_interval=float(request_interval),
    )
    monitor_frame = snapshot_to_monitor_frame(snapshot, root=root)
    return {
        "root": root,
        "months": months,
        "future_prices": future_prices,
        "cache_path": str(cache_path),
        "universe_source": universe_source,
        "chain_summary": chain_summary,
        "metadata": metadata,
        "selected_metadata": selected_metadata,
        "snapshot": snapshot,
        "monitor_frame": monitor_frame,
        "snapshot_scope": snapshot_scope,
        "min_expiration": effective_min_expiration,
        "max_expiration": max_expiration or "",
        "contract_count": len(contracts),
        "snapshot_count": len(selected_contracts),
    }
