from __future__ import annotations

import math
from typing import Any

import pandas as pd
from ib_async import Contract, IB, util

from .quotes import contract_from_row, fetch_quote_frame


YES_NO_BY_RIGHT = {"C": "YES", "P": "NO"}


def load_contract_frame(path: str) -> pd.DataFrame:
    frame = pd.read_csv(path)
    if "isError" in frame.columns:
        frame = frame[~frame["isError"].astype(str).str.lower().isin({"true", "1", "yes"})]
    return add_event_columns(frame).reset_index(drop=True)


def add_event_columns(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    if "eventSymbol" not in out.columns:
        out["eventSymbol"] = _column_or_default(out, "symbol", "")
    if "eventName" not in out.columns:
        out["eventName"] = _column_or_default(out, "longName", "").fillna("")
    right = _column_or_default(out, "right", "")
    out["choice"] = right.map(lambda value: YES_NO_BY_RIGHT.get(str(value).upper(), str(value)))
    out["expiry"] = _column_or_default(out, "lastTradeDateOrContractMonth", "")
    return out


def summarize_events(frame: pd.DataFrame) -> pd.DataFrame:
    data = add_event_columns(frame)
    if data.empty:
        return pd.DataFrame()
    grouped = data.groupby(["eventSymbol", "eventName"], dropna=False)
    rows: list[dict[str, Any]] = []
    for (symbol, name), group in grouped:
        expiries = sorted(str(value) for value in group["expiry"].dropna().unique() if str(value))
        choices = sorted(str(value) for value in group["choice"].dropna().unique() if str(value))
        rows.append(
            {
                "eventSymbol": symbol,
                "eventName": name,
                "contractCount": len(group),
                "expiryCount": len(expiries),
                "firstExpiry": expiries[0] if expiries else "",
                "lastExpiry": expiries[-1] if expiries else "",
                "strikeMin": _min_or_nan(group.get("strike")),
                "strikeMax": _max_or_nan(group.get("strike")),
                "choices": ",".join(choices),
                "exchange": _first_nonempty(group.get("exchange")),
                "secType": _first_nonempty(group.get("secType")),
                "minTick": _first_nonempty(group.get("minTick")),
                "orderTypes": _first_nonempty(group.get("orderTypes")),
                "underConId": _first_nonempty(group.get("underConId")),
                "industry": _first_nonempty(group.get("industry")),
                "category": _first_nonempty(group.get("category")),
            }
        )
    return pd.DataFrame(rows).sort_values(["eventSymbol", "eventName"]).reset_index(drop=True)


def event_contract_chain(
    frame: pd.DataFrame,
    event_symbol: str,
    *,
    expiry: str | None = None,
) -> pd.DataFrame:
    data = add_event_columns(frame)
    chain = data[data["eventSymbol"].astype(str).str.upper() == event_symbol.upper()].copy()
    if expiry:
        chain = chain[chain["expiry"].astype(str) == str(expiry)]
    columns = [
        "eventSymbol",
        "eventName",
        "conId",
        "localSymbol",
        "expiry",
        "strike",
        "choice",
        "right",
        "exchange",
        "currency",
        "minTick",
        "orderTypes",
        "tradingHours",
        "liquidHours",
    ]
    columns = [column for column in columns if column in chain.columns]
    return chain[columns].sort_values(["expiry", "strike", "choice"]).reset_index(drop=True)


def contracts_for_event(
    frame: pd.DataFrame,
    event_symbol: str,
    *,
    expiry: str | None = None,
    strikes: list[float] | tuple[float, ...] | None = None,
    choices: list[str] | tuple[str, ...] | None = None,
) -> list[Contract]:
    data = event_contract_chain(frame, event_symbol, expiry=expiry)
    if strikes:
        wanted = {float(value) for value in strikes}
        data = data[data["strike"].astype(float).isin(wanted)]
    if choices:
        wanted_choices = {str(value).upper() for value in choices}
        data = data[data["choice"].astype(str).str.upper().isin(wanted_choices)]
    source = add_event_columns(frame)
    selected_ids = set(data["conId"].astype(int))
    rows = source[source["conId"].astype(int).isin(selected_ids)]
    return [contract_from_row(row) for _, row in rows.iterrows()]


def fetch_event_quote_frame(
    ib: IB,
    frame: pd.DataFrame,
    event_symbol: str,
    *,
    expiry: str | None = None,
    strikes: list[float] | tuple[float, ...] | None = None,
    choices: list[str] | tuple[str, ...] | None = None,
    wait_seconds: float = 20.0,
    batch_size: int = 10,
) -> pd.DataFrame:
    contracts = contracts_for_event(
        frame,
        event_symbol,
        expiry=expiry,
        strikes=strikes,
        choices=choices,
    )
    quotes = fetch_quote_frame(
        ib,
        contracts,
        wait_seconds=wait_seconds,
        batch_size=batch_size,
    )
    return add_event_columns(quotes)


def fetch_historical_frame(
    ib: IB,
    contracts: list[Contract],
    *,
    duration: str = "1 M",
    bar_size: str = "1 day",
    what_to_show: str = "TRADES",
    use_rth: bool = False,
    timeout: float = 60.0,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for contract in contracts:
        try:
            bars = ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=duration,
                barSizeSetting=bar_size,
                whatToShow=what_to_show,
                useRTH=use_rth,
                formatDate=1,
                keepUpToDate=False,
                timeout=timeout,
            )
        except Exception as exc:
            rows.append(_history_error_row(contract, exc))
            continue
        frame = util.df(bars)
        if frame is None or frame.empty:
            rows.append(_history_empty_row(contract))
            continue
        for _, bar in frame.iterrows():
            rows.append(
                {
                    "conId": int(getattr(contract, "conId", 0) or 0),
                    "localSymbol": getattr(contract, "localSymbol", ""),
                    "symbol": getattr(contract, "symbol", ""),
                    "date": bar.get("date"),
                    "open": bar.get("open"),
                    "high": bar.get("high"),
                    "low": bar.get("low"),
                    "close": bar.get("close"),
                    "volume": bar.get("volume"),
                    "barCount": bar.get("barCount"),
                    "average": bar.get("average"),
                    "status": "ok",
                    "error": "",
                }
            )
    return pd.DataFrame(rows)


def _history_error_row(contract: Contract, exc: Exception) -> dict[str, Any]:
    row = _history_empty_row(contract)
    row["status"] = "error"
    row["error"] = str(exc)
    return row


def _history_empty_row(contract: Contract) -> dict[str, Any]:
    return {
        "conId": int(getattr(contract, "conId", 0) or 0),
        "localSymbol": getattr(contract, "localSymbol", ""),
        "symbol": getattr(contract, "symbol", ""),
        "date": "",
        "open": math.nan,
        "high": math.nan,
        "low": math.nan,
        "close": math.nan,
        "volume": math.nan,
        "barCount": math.nan,
        "average": math.nan,
        "status": "empty",
        "error": "",
    }


def _first_nonempty(series: pd.Series | None) -> Any:
    if series is None:
        return ""
    for value in series:
        if pd.notna(value) and str(value) != "":
            return value
    return ""


def _min_or_nan(series: pd.Series | None) -> float:
    if series is None:
        return math.nan
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.min()) if not values.empty else math.nan


def _max_or_nan(series: pd.Series | None) -> float:
    if series is None:
        return math.nan
    values = pd.to_numeric(series, errors="coerce").dropna()
    return float(values.max()) if not values.empty else math.nan


def _column_or_default(frame: pd.DataFrame, column: str, default: Any) -> pd.Series:
    if column in frame.columns:
        return frame[column]
    return pd.Series([default] * len(frame), index=frame.index)
