from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

import pandas as pd
from ib_async import Contract, Future, IB


FED_SYMBOL_PATTERNS = (
    "KXFED",
    "KXFEDDE215",
    "KXFEDHIKE",
    "KXFEDMEET",
    "Fed Meeting",
    "Hike 25",
    "No Change",
    "Maintains Rate",
)

EVENT_TYPE_PROBES = (
    ("EC", "KALSHI"),
    ("EC", "FORECASTX"),
    ("EC", ""),
    ("IND", "KALSHI"),
)
ZQ_MONTHS_2026 = tuple(range(7, 13))


@dataclass(frozen=True)
class DiscoverySettings:
    wait_seconds: float = 5.0
    zq_months: tuple[int, ...] = ZQ_MONTHS_2026
    patterns: tuple[str, ...] = FED_SYMBOL_PATTERNS


def run_fed_ib_discovery(ib: IB, settings: DiscoverySettings | None = None) -> dict[str, Any]:
    settings = settings or DiscoverySettings()
    return {
        "zq_quotes": fetch_zq_quotes(ib, settings),
        "matching_symbols": fetch_matching_symbols(ib, settings),
        "fed_underlying_details": probe_known_fed_underlyings(ib),
        "event_contract_type_probes": probe_event_contract_types(ib),
        "conclusion": "",
    }


def fetch_zq_quotes(ib: IB, settings: DiscoverySettings) -> list[dict[str, Any]]:
    contracts = [
        Future(symbol="ZQ", lastTradeDateOrContractMonth=f"2026{month:02d}", exchange="CBOT", currency="USD")
        for month in settings.zq_months
    ]
    qualified: list[Contract] = []
    rows: list[dict[str, Any]] = []
    for contract in contracts:
        try:
            matches = ib.qualifyContracts(contract)
            qualified.append(matches[0] if matches else contract)
        except Exception as exc:
            rows.append(
                {
                    "contract_month": contract.lastTradeDateOrContractMonth,
                    "status": "qualify_error",
                    "error": repr(exc),
                }
            )

    ib.reqMarketDataType(1)
    tickers = []
    for contract in qualified:
        try:
            tickers.append(ib.reqMktData(contract, "", False, False))
        except Exception as exc:
            rows.append(_contract_row(contract) | {"status": "market_data_error", "error": repr(exc)})
            tickers.append(None)
    ib.sleep(max(0.0, settings.wait_seconds))
    for contract, ticker in zip(qualified, tickers):
        if ticker is None:
            continue
        rows.append(
            _contract_row(contract)
            | {
                "status": "ok",
                "bid": _num(getattr(ticker, "bid", math.nan)),
                "ask": _num(getattr(ticker, "ask", math.nan)),
                "bidSize": _num(getattr(ticker, "bidSize", math.nan), allow_zero=True),
                "askSize": _num(getattr(ticker, "askSize", math.nan), allow_zero=True),
                "last": _num(getattr(ticker, "last", math.nan)),
                "close": _num(getattr(ticker, "close", math.nan)),
                "marketDataType": getattr(ticker, "marketDataType", ""),
            }
        )
        try:
            ib.cancelMktData(contract)
        except Exception:
            pass
    return rows


def fetch_matching_symbols(ib: IB, settings: DiscoverySettings) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for pattern in settings.patterns:
        try:
            matches = ib.reqMatchingSymbols(pattern)
        except Exception as exc:
            rows.append({"pattern": pattern, "status": "error", "error": repr(exc)})
            continue
        if not matches:
            rows.append({"pattern": pattern, "status": "no_matches"})
            continue
        for match in matches:
            contract = match.contract
            rows.append(
                {
                    "pattern": pattern,
                    "status": "ok",
                    "derivativeSecTypes": list(getattr(match, "derivativeSecTypes", []) or []),
                }
                | _contract_row(contract)
            )
    return rows


def probe_known_fed_underlyings(ib: IB) -> list[dict[str, Any]]:
    probes = [
        Contract(secType="IND", symbol="KXFED", exchange="KALSHI", currency="USD"),
        Contract(secType="IND", symbol="KXFEDDE215", exchange="KALSHI", currency="USD"),
        Contract(secType="IND", symbol="KXFEDHIKE", exchange="KALSHI", currency="USD"),
        Contract(secType="IND", symbol="KXFEDMEET", exchange="KALSHI", currency="USD"),
    ]
    rows: list[dict[str, Any]] = []
    for probe in probes:
        try:
            details = ib.reqContractDetails(probe)
        except Exception as exc:
            rows.append(_contract_row(probe) | {"status": "error", "error": repr(exc)})
            continue
        if not details:
            rows.append(_contract_row(probe) | {"status": "no_details"})
            continue
        for detail in details:
            rows.append(_contract_detail_row(detail) | {"status": "ok"})
    return rows


def probe_event_contract_types(ib: IB) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sec_type, exchange in EVENT_TYPE_PROBES:
        probe = Contract(secType=sec_type, symbol="KXFEDDE215", exchange=exchange, currency="USD")
        try:
            details = ib.reqContractDetails(probe)
        except Exception as exc:
            rows.append(
                _contract_row(probe)
                | {
                    "status": "error",
                    "error": repr(exc),
                }
            )
            continue
        if not details:
            rows.append(_contract_row(probe) | {"status": "no_details"})
            continue
        for detail in details:
            rows.append(_contract_detail_row(detail) | {"status": "ok"})
    return rows


def discovery_frames(result: dict[str, Any]) -> dict[str, pd.DataFrame]:
    return {
        key: pd.DataFrame(value)
        for key, value in result.items()
        if isinstance(value, list)
    }


def discovery_summary(result: dict[str, Any]) -> str:
    zq_ok = sum(1 for row in result["zq_quotes"] if row.get("status") == "ok" and _is_number(row.get("bid")) and _is_number(row.get("ask")))
    matches = [row for row in result["matching_symbols"] if row.get("status") == "ok"]
    event_ok = [row for row in result["event_contract_type_probes"] if row.get("status") == "ok" and row.get("secType") != "IND"]
    ind_ok = [row for row in result["fed_underlying_details"] if row.get("status") == "ok"]
    lines = [
        f"ZQ executable top-of-book rows: {zq_ok}",
        f"Fed/KXFED matching-symbol rows: {len(matches)}",
        f"Fed underlying detail rows: {len(ind_ok)}",
        f"Tradable non-IND event detail rows found: {len(event_ok)}",
    ]
    if not event_ok:
        lines.append("Conclusion: current IB native API session exposes KXFED underlyings but does not enumerate a tradable Fed Meeting event leg.")
    return "\n".join(lines)


def _contract_detail_row(detail: Any) -> dict[str, Any]:
    contract = detail.contract
    return _contract_row(contract) | {
        "longName": getattr(detail, "longName", ""),
        "marketName": getattr(detail, "marketName", ""),
        "validExchanges": getattr(detail, "validExchanges", ""),
        "realExpirationDate": getattr(detail, "realExpirationDate", ""),
        "contractMonth": getattr(detail, "contractMonth", ""),
        "minTick": _num(getattr(detail, "minTick", math.nan), allow_zero=True),
    }


def _contract_row(contract: Contract) -> dict[str, Any]:
    return {
        "conId": int(getattr(contract, "conId", 0) or 0),
        "symbol": getattr(contract, "symbol", ""),
        "secType": getattr(contract, "secType", ""),
        "localSymbol": getattr(contract, "localSymbol", ""),
        "exchange": getattr(contract, "exchange", ""),
        "primaryExchange": getattr(contract, "primaryExchange", ""),
        "currency": getattr(contract, "currency", ""),
        "lastTradeDateOrContractMonth": getattr(contract, "lastTradeDateOrContractMonth", ""),
        "strike": _num(getattr(contract, "strike", math.nan), allow_zero=True),
        "right": getattr(contract, "right", ""),
        "tradingClass": getattr(contract, "tradingClass", ""),
        "description": getattr(contract, "description", ""),
    }


def _num(value: Any, *, allow_zero: bool = False) -> float:
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
        return not math.isnan(float(value))
    except (TypeError, ValueError):
        return False
