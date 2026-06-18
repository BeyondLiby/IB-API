from __future__ import annotations

import math
from typing import Any, Iterable

import pandas as pd
from ib_async import Contract, IB

from .config import EVENT_TEXT_MARKERS, ScanSettings


INVALID_SECURITY_TYPE_MARKER = "valid security type"
INVALID_EXCHANGE_MARKERS = (
    "destination or exchange selected is invalid",
    "invalid exchange",
)


def split_csv(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def read_local_symbols(path: str | None) -> tuple[str, ...]:
    """Read localSymbol seeds from a CSV with localSymbol/local_symbol/symbol."""
    if not path:
        return ()
    frame = pd.read_csv(path)
    for column in ("localSymbol", "local_symbol", "symbol"):
        if column in frame.columns:
            return tuple(str(value).strip() for value in frame[column].dropna() if str(value).strip())
    raise ValueError(f"{path} must contain a localSymbol, local_symbol, or symbol column")


def split_float_csv(value: str | None) -> tuple[float, ...]:
    if not value:
        return ()
    out: list[float] = []
    for part in value.split(","):
        text = part.strip()
        if not text:
            continue
        out.append(float(text))
    return tuple(out)


def contract_templates(settings: ScanSettings) -> Iterable[Contract]:
    """Generate broad reqContractDetails probes from seed symbols and localSymbols."""
    for exchange in settings.exchanges:
        for sec_type in settings.sec_types:
            for local_symbol in settings.local_symbols:
                yield Contract(
                    secType=sec_type,
                    localSymbol=local_symbol,
                    exchange=exchange,
                    currency=settings.currency,
                )
            for symbol in settings.symbols:
                if settings.expirations or settings.strikes or settings.rights:
                    expirations = settings.expirations or ("",)
                    strikes = settings.strikes or (0.0,)
                    rights = settings.rights or ("",)
                    for expiration in expirations:
                        for strike in strikes:
                            for right in rights:
                                yield Contract(
                                    secType=sec_type,
                                    symbol=symbol,
                                    lastTradeDateOrContractMonth=expiration,
                                    strike=strike,
                                    right=right,
                                    exchange=exchange,
                                    currency=settings.currency,
                                )
                else:
                    yield Contract(
                        secType=sec_type,
                        symbol=symbol,
                        exchange=exchange,
                        currency=settings.currency,
                    )


def scan_event_contracts(ib: IB, settings: ScanSettings) -> pd.DataFrame:
    """Probe IB contract details and return likely ForecastEx/event contracts."""
    rows: list[dict[str, Any]] = []
    seen: set[tuple[int, str, str]] = set()
    invalid_sec_types: set[str] = set()
    invalid_exchanges: set[str] = set()

    for template in contract_templates(settings):
        if str(template.secType).upper() in invalid_sec_types:
            continue
        if str(template.exchange).upper() in invalid_exchanges:
            continue
        try:
            details = ib.reqContractDetails(template)
        except Exception as exc:
            rows.append(_error_row(template, exc))
            if is_invalid_security_type_error(exc):
                invalid_sec_types.add(str(template.secType).upper())
            if is_invalid_exchange_error(exc):
                invalid_exchanges.add(str(template.exchange).upper())
            details = []
        if settings.request_pause_seconds > 0:
            ib.sleep(settings.request_pause_seconds)

        for item in details:
            row = contract_detail_row(item, template)
            key = (
                int(row.get("conId") or 0),
                str(row.get("localSymbol") or ""),
                str(row.get("exchange") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            if settings.keep_all_matches or is_event_like(row):
                rows.append(row)

    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    sort_columns = [column for column in ("isError", "exchange", "secType", "symbol", "localSymbol") if column in frame]
    return frame.sort_values(sort_columns).reset_index(drop=True)


def is_invalid_security_type_error(exc: Exception) -> bool:
    return INVALID_SECURITY_TYPE_MARKER in str(exc).lower()


def is_invalid_exchange_error(exc: Exception) -> bool:
    error = str(exc).lower()
    return any(marker in error for marker in INVALID_EXCHANGE_MARKERS)


def contract_detail_row(detail: Any, template: Contract) -> dict[str, Any]:
    contract = detail.contract
    sec_id_list = getattr(detail, "secIdList", None) or []
    sec_ids = ";".join(
        f"{getattr(item, 'tag', '')}={getattr(item, 'value', '')}"
        for item in sec_id_list
        if getattr(item, "tag", "")
    )
    return {
        "isError": False,
        "probeSecType": template.secType,
        "probeSymbol": template.symbol,
        "probeLocalSymbol": template.localSymbol,
        "probeExchange": template.exchange,
        "conId": int(getattr(contract, "conId", 0) or 0),
        "symbol": getattr(contract, "symbol", ""),
        "localSymbol": getattr(contract, "localSymbol", ""),
        "secType": getattr(contract, "secType", ""),
        "exchange": getattr(contract, "exchange", ""),
        "primaryExchange": getattr(contract, "primaryExchange", ""),
        "currency": getattr(contract, "currency", ""),
        "tradingClass": getattr(contract, "tradingClass", ""),
        "lastTradeDateOrContractMonth": getattr(contract, "lastTradeDateOrContractMonth", ""),
        "strike": _clean_number(getattr(contract, "strike", math.nan)),
        "right": getattr(contract, "right", ""),
        "multiplier": getattr(contract, "multiplier", ""),
        "marketName": getattr(detail, "marketName", ""),
        "longName": getattr(detail, "longName", ""),
        "underConId": int(getattr(detail, "underConId", 0) or 0),
        "underSymbol": getattr(detail, "underSymbol", ""),
        "underSecType": getattr(detail, "underSecType", ""),
        "contractMonth": getattr(detail, "contractMonth", ""),
        "industry": getattr(detail, "industry", ""),
        "category": getattr(detail, "category", ""),
        "subcategory": getattr(detail, "subcategory", ""),
        "orderTypes": getattr(detail, "orderTypes", ""),
        "validExchanges": getattr(detail, "validExchanges", ""),
        "tradingHours": getattr(detail, "tradingHours", ""),
        "liquidHours": getattr(detail, "liquidHours", ""),
        "minTick": _clean_number(getattr(detail, "minTick", math.nan)),
        "minSize": _clean_number(getattr(detail, "minSize", math.nan)),
        "sizeIncrement": _clean_number(getattr(detail, "sizeIncrement", math.nan)),
        "suggestedSizeIncrement": _clean_number(getattr(detail, "suggestedSizeIncrement", math.nan)),
        "realExpirationDate": getattr(detail, "realExpirationDate", ""),
        "lastTradeTime": getattr(detail, "lastTradeTime", ""),
        "secIds": sec_ids,
        "error": "",
    }


def is_event_like(row: dict[str, Any]) -> bool:
    fields = (
        "symbol",
        "localSymbol",
        "secType",
        "exchange",
        "primaryExchange",
        "tradingClass",
        "marketName",
        "longName",
        "category",
        "subcategory",
        "validExchanges",
    )
    haystack = " ".join(str(row.get(field, "") or "").upper() for field in fields)
    return any(marker in haystack for marker in EVENT_TEXT_MARKERS)


def _error_row(template: Contract, exc: Exception) -> dict[str, Any]:
    return {
        "isError": True,
        "probeSecType": template.secType,
        "probeSymbol": template.symbol,
        "probeLocalSymbol": template.localSymbol,
        "probeExchange": template.exchange,
        "error": str(exc),
    }


def _clean_number(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return math.nan
    return out if not math.isnan(out) else math.nan
