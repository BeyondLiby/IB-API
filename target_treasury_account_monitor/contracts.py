from __future__ import annotations

from copy import copy
from typing import Any

try:
    from .config import TREASURY_ROOTS
except ImportError:
    from config import TREASURY_ROOTS


def contract_label(contract: Any) -> str:
    """生成稳定的合约显示名，优先使用 IB 本地代码。"""
    for attr in ("localSymbol", "symbol"):
        value = getattr(contract, attr, "")
        if value:
            return str(value)
    return str(getattr(contract, "conId", ""))


def option_full_name(contract: Any) -> str:
    """生成期权全名：品种-到期日-strike-看涨/看跌。"""
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
    """只保留美债期货和美债期货期权，根代码覆盖 ZT/ZF/ZN/TN/ZB/UB。"""
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
    """选择行情请求使用的交易所，IB 有时会在持仓合约里留空。"""
    exchange = str(getattr(contract, "exchange", "") or "").strip()
    if exchange:
        return exchange
    return "CBOT" if is_treasury_contract(contract) else ""


def normalize_market_data_contract(contract: Any) -> Any:
    """复制合约并补齐行情请求常用字段，避免修改原始持仓对象。"""
    out = copy(contract)
    exchange = contract_exchange(out)
    if exchange:
        out.exchange = exchange
    if not getattr(out, "currency", ""):
        out.currency = "USD"
    return out


def contract_multiplier(contract: Any) -> float:
    """读取合约乘数；IB 留空或异常时按 1 兜底。"""
    raw = getattr(contract, "multiplier", None)
    if raw in (None, ""):
        return 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0
