from __future__ import annotations

from copy import copy
from collections import Counter
import re
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


def year_from_code(year_code: str, current_year: int | None = None) -> int:
    """Infer a four-digit futures year from a one-digit IB local symbol code."""
    if current_year is None:
        from datetime import datetime

        current_year = datetime.now().year
    digit = int(year_code)
    decade = current_year - (current_year % 10)
    candidate = decade + digit
    if candidate < current_year - 5:
        candidate += 10
    if candidate > current_year + 5:
        candidate -= 10
    return candidate


def month_code_to_number(month_code: str) -> int:
    """Convert a futures month code into a calendar month number."""
    month_codes = {
        "F": 1,
        "G": 2,
        "H": 3,
        "J": 4,
        "K": 5,
        "M": 6,
        "N": 7,
        "Q": 8,
        "U": 9,
        "V": 10,
        "X": 11,
        "Z": 12,
    }
    return month_codes[month_code.upper()]


def infer_future_month_from_contract(contract: Any) -> str:
    """Infer the underlying futures month YYYYMM from an option or future contract."""
    sec_type = str(getattr(contract, "secType", "") or "").upper()
    expiry = str(getattr(contract, "lastTradeDateOrContractMonth", "") or "")
    if sec_type == "FUT" and len(expiry) >= 6:
        return expiry[:6]

    local_symbol = str(getattr(contract, "localSymbol", "") or "").upper()
    matches = re.findall(r"([FGHJKMNQUVXZ])(\d)(?=\s|$)", local_symbol)
    if matches:
        month_code, year_code = matches[-1]
        year = year_from_code(year_code)
        month = month_code_to_number(month_code)
        return f"{year}{month:02d}"
    return ""


def infer_primary_future_month(positions: list[Any], root: str = "ZF") -> str:
    """Choose the most common underlying future month from current positions."""
    months: list[str] = []
    for pos in positions:
        contract = getattr(pos, "contract", None)
        if str(getattr(contract, "symbol", "") or "").upper() != root:
            continue
        month = infer_future_month_from_contract(contract)
        if month:
            months.append(month)
    if not months:
        return ""
    return Counter(months).most_common(1)[0][0]


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


def contract_cash_multiplier(contract: Any) -> float:
    """Return the USD multiplier for one displayed unit of an option quote.

    IB reports the full-size Corn (ZC) contract multiplier as 5,000 bushels,
    while its option quotes are in cents per bushel.  One displayed cent is
    therefore worth USD 50 per contract, rather than USD 5,000.
    """
    symbol = str(getattr(contract, "symbol", "") or "").strip().upper()
    if symbol == "ZC":
        return 50.0
    return contract_multiplier(contract)
