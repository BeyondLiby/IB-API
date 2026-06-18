from __future__ import annotations

import math
from typing import Any

import pandas as pd
from ib_async import Contract, IB


QUOTE_FIELDS = (
    "bid",
    "ask",
    "last",
    "markPrice",
    "volume",
    "tradeCount",
    "tradeRate",
    "volumeRate",
)


def load_contracts(path: str) -> list[Contract]:
    frame = pd.read_csv(path)
    contracts: list[Contract] = []
    for _, row in frame.iterrows():
        if _clean_bool(row.get("isError", False)):
            continue
        contract = contract_from_row(row)
        if int(getattr(contract, "conId", 0) or 0) or getattr(contract, "localSymbol", ""):
            contracts.append(contract)
    return contracts


def contract_from_row(row: pd.Series) -> Contract:
    return Contract(
        conId=_clean_int(row.get("conId")),
        secType=_clean_text(row.get("secType")),
        symbol=_clean_text(row.get("symbol")),
        localSymbol=_clean_text(row.get("localSymbol")),
        exchange=_clean_text(row.get("exchange")) or _first_exchange(row.get("validExchanges")),
        currency=_clean_text(row.get("currency")) or "USD",
        tradingClass=_clean_text(row.get("tradingClass")),
        lastTradeDateOrContractMonth=_clean_text(row.get("lastTradeDateOrContractMonth")),
        strike=_clean_float(row.get("strike")),
        right=_clean_text(row.get("right")),
        multiplier=_clean_text(row.get("multiplier")),
    )


def fetch_quote_frame(
    ib: IB,
    contracts: list[Contract],
    *,
    wait_seconds: float = 5.0,
    generic_ticks: str = "100,101,104,106,165,233,293,294,295",
    qualify: bool = True,
    batch_size: int = 25,
    poll_interval: float = 0.5,
    snapshot_fallback: bool = True,
) -> pd.DataFrame:
    """Subscribe briefly, collect top-of-book and activity fields, then cancel."""
    if qualify and contracts:
        qualified: list[Contract] = []
        for contract in contracts:
            try:
                matches = ib.qualifyContracts(contract)
                qualified.append(matches[0] if matches else contract)
            except Exception:
                qualified.append(contract)
        contracts = qualified

    rows: list[dict[str, Any]] = []
    batch_size = max(1, int(batch_size or 1))
    for start in range(0, len(contracts), batch_size):
        batch = contracts[start : start + batch_size]
        tickers = []
        for contract in batch:
            try:
                tickers.append(ib.reqMktData(contract, genericTickList=generic_ticks, snapshot=False))
            except Exception:
                tickers.append(None)

        wait_for_batch(tickers, ib=ib, wait_seconds=wait_seconds, poll_interval=poll_interval)
        if snapshot_fallback:
            tickers = add_snapshot_fallbacks(ib, batch, tickers)
        rows.extend(ticker_row(ticker, contract) for ticker, contract in zip(tickers, batch))

        for ticker, contract in zip(tickers, batch):
            if ticker is None:
                continue
            try:
                ib.cancelMktData(contract)
            except Exception:
                pass

    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame = frame.sort_values(["liquidityScore", "localSymbol"], ascending=[False, True]).reset_index(drop=True)
    return frame


def add_snapshot_fallbacks(ib: IB, contracts: list[Contract], tickers: list[Any]) -> list[Any]:
    missing_indexes = [
        index
        for index, ticker in enumerate(tickers)
        if not ticker_has_any_data(ticker)
    ]
    if not missing_indexes:
        return tickers
    missing_contracts = [contracts[index] for index in missing_indexes]
    try:
        snapshots = ib.reqTickers(*missing_contracts)
    except Exception:
        return tickers
    out = list(tickers)
    for index, snapshot in zip(missing_indexes, snapshots):
        out[index] = snapshot
    return out


def wait_for_batch(
    tickers: list[Any],
    *,
    ib: IB,
    wait_seconds: float,
    poll_interval: float = 0.5,
) -> None:
    """Wait until at least one ticker has a usable field, or until timeout."""
    if wait_seconds <= 0:
        return
    elapsed = 0.0
    interval = max(0.1, float(poll_interval or 0.5))
    while elapsed < wait_seconds:
        if any(ticker_has_any_data(ticker) for ticker in tickers):
            return
        sleep_for = min(interval, wait_seconds - elapsed)
        ib.sleep(sleep_for)
        elapsed += sleep_for


def ticker_has_any_data(ticker: Any) -> bool:
    if ticker is None:
        return False
    return any(_is_number(_valid_number(getattr(ticker, field, math.nan), allow_zero=True)) for field in QUOTE_FIELDS)


def ticker_row(ticker: Any, contract: Contract) -> dict[str, Any]:
    bid = _valid_number(getattr(ticker, "bid", math.nan)) if ticker is not None else math.nan
    ask = _valid_number(getattr(ticker, "ask", math.nan)) if ticker is not None else math.nan
    bid_size = _valid_number(getattr(ticker, "bidSize", math.nan), allow_zero=True) if ticker is not None else math.nan
    ask_size = _valid_number(getattr(ticker, "askSize", math.nan), allow_zero=True) if ticker is not None else math.nan
    last = _valid_number(getattr(ticker, "last", math.nan)) if ticker is not None else math.nan
    close = _valid_number(getattr(ticker, "close", math.nan)) if ticker is not None else math.nan
    mark = _valid_number(getattr(ticker, "markPrice", math.nan)) if ticker is not None else math.nan
    volume = _valid_number(getattr(ticker, "volume", math.nan), allow_zero=True) if ticker is not None else math.nan
    trade_count = _valid_number(getattr(ticker, "tradeCount", math.nan), allow_zero=True) if ticker is not None else math.nan
    trade_rate = _valid_number(getattr(ticker, "tradeRate", math.nan), allow_zero=True) if ticker is not None else math.nan
    volume_rate = _valid_number(getattr(ticker, "volumeRate", math.nan), allow_zero=True) if ticker is not None else math.nan
    mid = (bid + ask) / 2.0 if _is_number(bid) and _is_number(ask) else math.nan
    spread = ask - bid if _is_number(bid) and _is_number(ask) else math.nan
    spread_pct = spread / mid if _is_number(spread) and _is_number(mid) and mid != 0 else math.nan
    top_size = min(bid_size, ask_size) if _is_number(bid_size) and _is_number(ask_size) else math.nan
    dollar_top = top_size * mid if _is_number(top_size) and _is_number(mid) else math.nan
    liquidity_score = _liquidity_score(spread_pct, top_size, volume)
    has_top_of_book = _is_number(bid) or _is_number(ask)
    has_trade_data = _is_number(last) or _is_number(volume) or _is_number(trade_count)
    has_reference_data = _is_number(mark) or _is_number(close)
    if has_top_of_book:
        quote_status = "top_of_book"
    elif has_trade_data:
        quote_status = "trade_data_only"
    elif has_reference_data:
        quote_status = "reference_only"
    else:
        quote_status = "no_market_data_after_wait"

    return {
        "conId": int(getattr(contract, "conId", 0) or 0),
        "symbol": getattr(contract, "symbol", ""),
        "localSymbol": getattr(contract, "localSymbol", ""),
        "secType": getattr(contract, "secType", ""),
        "exchange": getattr(contract, "exchange", ""),
        "currency": getattr(contract, "currency", ""),
        "tradingClass": getattr(contract, "tradingClass", ""),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "markPrice": mark,
        "close": close,
        "bidSize": bid_size,
        "askSize": ask_size,
        "topSize": top_size,
        "notionalTop": dollar_top,
        "spread": spread,
        "spreadPct": spread_pct,
        "volume": volume,
        "tradeCount": trade_count,
        "tradeRate": trade_rate,
        "volumeRate": volume_rate,
        "halted": _valid_number(getattr(ticker, "halted", math.nan), allow_zero=True) if ticker is not None else math.nan,
        "marketDataType": getattr(ticker, "marketDataType", "") if ticker is not None else "",
        "hasTopOfBook": has_top_of_book,
        "hasTradeData": has_trade_data,
        "hasReferenceData": has_reference_data,
        "quoteStatus": quote_status,
        "liquidityScore": liquidity_score,
    }


def _liquidity_score(spread_pct: float, top_size: float, volume: float) -> float:
    size_score = math.log1p(top_size) if _is_number(top_size) else 0.0
    volume_score = math.log1p(volume) if _is_number(volume) else 0.0
    spread_penalty = max(spread_pct, 0.0) * 10.0 if _is_number(spread_pct) else 5.0
    return size_score + volume_score - spread_penalty


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "nan" else text


def _clean_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y"}


def _clean_int(value: Any) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0
    return int(number) if not math.isnan(number) else 0


def _clean_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if not math.isnan(number) else 0.0


def _first_exchange(value: Any) -> str:
    text = _clean_text(value)
    return text.split(",")[0].strip() if text else ""


def _valid_number(value: Any, *, allow_zero: bool = False) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    if math.isnan(number) or number == -1.0:
        return math.nan
    if number == 0.0 and not allow_zero:
        return math.nan
    return number


def _is_number(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(number)
