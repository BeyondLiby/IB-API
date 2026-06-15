from __future__ import annotations

from copy import copy
from typing import Any

try:
    from .config import TREASURY_ROOTS
except ImportError:
    from config import TREASURY_ROOTS


def contract_label(contract: Any) -> str:
    """Build a stable human-readable contract label from IB contract fields."""
    for attr in ("localSymbol", "symbol"):
        value = getattr(contract, attr, "")
        if value:
            return str(value)
    return str(getattr(contract, "conId", ""))


def option_full_name(contract: Any) -> str:
    """Build ticker-expiry-strike-right names for futures option rows."""
    symbol = str(getattr(contract, "symbol", "") or "").strip()
    expiry = str(getattr(contract, "lastTradeDateOrContractMonth", "") or "").strip()
    strike = getattr(contract, "strike", "")
    right = str(getattr(contract, "right", "") or "").strip().upper()
    if not symbol or not expiry or not right:
        return contract_label(contract)
    try:
        strike_text = f"{float(strike):g}"
    except (TypeError, ValueError):
        strike_text = str(strike)
    return f"{symbol}-{expiry}-{strike_text}-{right}"


def is_treasury_contract(contract: Any) -> bool:
    """Keep only CBOT treasury futures and futures options by root symbol."""
    if contract is None:
        return False
    sec_type = str(getattr(contract, "secType", "") or "").upper()
    if sec_type not in {"FUT", "FOP"}:
        return False
    symbol = str(getattr(contract, "symbol", "") or "").upper()
    local_symbol = str(getattr(contract, "localSymbol", "") or "").upper()
    trading_class = str(getattr(contract, "tradingClass", "") or "").upper()
    return (
        symbol in TREASURY_ROOTS
        or trading_class in TREASURY_ROOTS
        or local_symbol[:2] in TREASURY_ROOTS
    )


def contract_exchange(contract: Any) -> str:
    """Choose the exchange used for market-data requests."""
    exchange = str(getattr(contract, "exchange", "") or "").strip()
    if exchange:
        return exchange
    return "CBOT" if is_treasury_contract(contract) else ""


def normalize_market_data_contract(contract: Any) -> Any:
    """Copy a contract and fill exchange/currency fields needed by reqMktData."""
    out = copy(contract)
    exchange = contract_exchange(out)
    if exchange:
        out.exchange = exchange
    if not getattr(out, "currency", ""):
        out.currency = "USD"
    return out


def contract_multiplier(contract: Any) -> float:
    """Read the IB multiplier, falling back to 1 when IB leaves it blank."""
    raw = getattr(contract, "multiplier", None)
    if raw in (None, ""):
        return 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0
