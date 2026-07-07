from __future__ import annotations

from pathlib import Path
import re

import pandas as pd
from ib_async import Contract, IB

from target_treasury_monitor_clean.carry_dashboard_sync import BARS_COLUMNS
from treasury_fop_chain import qualify_future


MONTH_NUMBER_TO_CODE = {
    "01": "F",
    "02": "G",
    "03": "H",
    "04": "J",
    "05": "K",
    "06": "M",
    "07": "N",
    "08": "Q",
    "09": "U",
    "10": "V",
    "11": "X",
    "12": "Z",
}


def _parse_bar_datetime(value: object, *, default_tz: str = "America/Chicago") -> pd.Timestamp:
    """Parse IB historical bar dates and attach an exchange timezone when missing."""
    if isinstance(value, pd.Timestamp):
        timestamp = value
    else:
        text = str(value).strip()
        text = re.sub(r"\s+", " ", text)
        if re.fullmatch(r"\d{8} \d{2}:\d{2}:\d{2}", text):
            timestamp = pd.to_datetime(text, format="%Y%m%d %H:%M:%S")
        elif re.fullmatch(r"\d{8}", text):
            timestamp = pd.to_datetime(text, format="%Y%m%d")
        else:
            timestamp = pd.to_datetime(text, errors="raise")
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(default_tz, ambiguous="NaT", nonexistent="shift_forward")
    return timestamp


def parse_contract_specs(value: str) -> list[tuple[str, str]]:
    """Parse comma-separated ROOT:YYYYMM futures specs."""
    specs: list[tuple[str, str]] = []
    for part in str(value or "").replace(";", ",").split(","):
        text = part.strip()
        if not text:
            continue
        if ":" not in text:
            raise ValueError(f"Contract spec must be ROOT:YYYYMM, got {text!r}")
        root, month = [item.strip().upper() for item in text.split(":", 1)]
        if not root or not month:
            raise ValueError(f"Contract spec must be ROOT:YYYYMM, got {text!r}")
        specs.append((root, month))
    return specs


def future_local_symbol(root: str, month: str) -> str:
    """Build a CBOT futures localSymbol like ZFU6 from ROOT:YYYYMM."""
    text = str(month).strip()
    if len(text) < 6 or text[4:6] not in MONTH_NUMBER_TO_CODE:
        raise ValueError(f"Future month must be YYYYMM, got {month!r}")
    return f"{root.upper()}{MONTH_NUMBER_TO_CODE[text[4:6]]}{text[3]}"


def local_symbol_future_contract(root: str, month: str, *, exchange: str = "CBOT") -> Contract:
    """Build a futures contract from standard localSymbol without qualifying it first."""
    contract = Contract()
    contract.symbol = root.upper()
    contract.secType = "FUT"
    contract.exchange = exchange
    contract.currency = "USD"
    contract.lastTradeDateOrContractMonth = str(month)
    contract.localSymbol = future_local_symbol(root, month)
    return contract


def cached_future_contract(root: str, month: str, *, search_dir: str | Path = "data") -> Contract | None:
    """Build a futures contract from saved *_future_prices.csv metadata when available."""
    root = root.upper()
    month = str(month)
    candidates = sorted(Path(search_dir).glob("**/*_future_prices.csv"))
    for path in candidates:
        try:
            frame = pd.read_csv(path)
        except Exception:
            continue
        if not {"root", "month", "conId"}.issubset(frame.columns):
            continue
        matches = frame[
            (frame["root"].astype(str).str.upper() == root)
            & (frame["month"].astype(str) == month)
            & pd.to_numeric(frame["conId"], errors="coerce").notna()
        ]
        if matches.empty:
            continue
        row = matches.iloc[0]
        contract = Contract()
        contract.conId = int(row["conId"])
        contract.symbol = root
        contract.secType = "FUT"
        contract.exchange = str(row.get("exchange", "") or "CBOT")
        contract.currency = "USD"
        contract.lastTradeDateOrContractMonth = month
        contract.localSymbol = str(row.get("localSymbol", "") or "")
        return contract
    return None


def fetch_future_bars(
    ib: IB,
    specs: list[tuple[str, str]],
    *,
    bar_size: str = "30 mins",
    duration: str = "1 M",
    what_to_show: str = "TRADES",
    timeout: float = 45.0,
    cache_dir: str | Path = "data",
    strict: bool = True,
    prefer_local_symbol: bool = False,
) -> pd.DataFrame:
    """Fetch OHLCV bars for futures and return the dashboard schema."""
    rows: list[dict[str, object]] = []
    previous_timeout = getattr(ib, "RequestTimeout", None)
    if previous_timeout is not None:
        ib.RequestTimeout = timeout
    try:
        for root, month in specs:
            try:
                contract = cached_future_contract(root, month, search_dir=cache_dir)
                if contract is not None:
                    print(f"using cached future: {root}:{month} conId={contract.conId}", flush=True)
                elif prefer_local_symbol:
                    contract = local_symbol_future_contract(root, month)
                    print(f"using localSymbol future: {root}:{month} {contract.localSymbol}", flush=True)
                else:
                    print(f"qualifying future: {root}:{month}", flush=True)
                    contract = qualify_future(ib, root, month)
                print(f"requesting bars: {root}:{month} {bar_size} {duration}", flush=True)
                bars = ib.reqHistoricalData(
                    contract,
                    endDateTime="",
                    durationStr=duration,
                    barSizeSetting=bar_size,
                    whatToShow=what_to_show,
                    useRTH=False,
                    formatDate=1,
                    keepUpToDate=False,
                    timeout=timeout,
                )
                for bar in bars or []:
                    rows.append(
                        {
                            "symbol": root,
                            "month": month,
                            "localSymbol": getattr(contract, "localSymbol", ""),
                            "date": _parse_bar_datetime(bar.date),
                            "open": float(bar.open),
                            "high": float(bar.high),
                            "low": float(bar.low),
                            "close": float(bar.close),
                            "volume": float(bar.volume),
                        }
                    )
            except Exception as exc:
                if strict:
                    raise
                print(f"bars failed for {root}:{month} ({type(exc).__name__}); continuing", flush=True)
    finally:
        if previous_timeout is not None:
            ib.RequestTimeout = previous_timeout
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"], utc=True).dt.tz_convert("America/Chicago")
    frame["dateChina"] = frame["date"].dt.tz_convert("Asia/Shanghai")
    return frame.sort_values(["symbol", "date"], ignore_index=True)


def save_future_bars(frame: pd.DataFrame, output: str | Path) -> Path:
    """Save future bars as UTF-8 CSV."""
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    if frame.empty:
        frame = pd.DataFrame(columns=BARS_COLUMNS)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return path
