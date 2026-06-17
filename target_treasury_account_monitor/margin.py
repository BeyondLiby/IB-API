from __future__ import annotations

import math
from typing import Any

import pandas as pd

try:
    from ib_async import LimitOrder
except ImportError:
    LimitOrder = None

try:
    from .utils import clean_number, summary_value
except ImportError:
    from utils import clean_number, summary_value


MARGIN_FIELDS = [
    "initMarginBefore",
    "initMarginChange",
    "initMarginAfter",
    "maintMarginBefore",
    "maintMarginChange",
    "maintMarginAfter",
    "equityWithLoanBefore",
    "equityWithLoanChange",
    "equityWithLoanAfter",
    "commission",
    "minCommission",
    "maxCommission",
    "warningText",
]


def parse_margin_number(value: Any) -> float:
    """解析 IB OrderState 里的保证金字符串，例如 '1,234.56'。"""
    if value is None:
        return math.nan
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value:
            return math.nan
    return clean_number(value)


def order_state_to_margin_row(order_state: Any) -> dict[str, Any]:
    """把 IB what-if 返回的 OrderState 摊平成一行数据。"""
    row: dict[str, Any] = {}
    for field in MARGIN_FIELDS:
        value = getattr(order_state, field, math.nan)
        if field == "warningText":
            row[field] = value or ""
        else:
            row[field] = parse_margin_number(value)
    return row


def build_limit_order(action: str, quantity: float, limit_price: float, account: str = "") -> Any:
    """创建只用于保证金试算的限价单；whatIf=True 且 transmit=False。"""
    if LimitOrder is None:
        raise ImportError("ib_async LimitOrder is not available in this Python environment")
    order = LimitOrder(action.upper(), quantity, limit_price)
    order.whatIf = True
    order.transmit = False
    if account:
        order.account = account
    return order


def what_if_order_margin(
    ib: Any,
    contract: Any,
    *,
    action: str,
    quantity: float,
    limit_price: float,
    account: str = "",
) -> dict[str, Any]:
    """请求 IB 计算候选订单的保证金影响，不发送真实订单。"""
    order = build_limit_order(action, quantity, limit_price, account)
    order_state = ib.whatIfOrder(contract, order)
    row = order_state_to_margin_row(order_state)
    row.update(
        {
            "action": action.upper(),
            "quantity": quantity,
            "limitPrice": limit_price,
            "account": account,
        }
    )
    return row


def estimate_contract_capacity(summary: pd.DataFrame, margin_row: dict[str, Any], safety_buffer: float = 0.0) -> dict[str, Any]:
    """用剩余流动性和单笔保证金变化粗算最多可承受手数。"""
    excess_liquidity = summary_value(summary, "ExcessLiquidity")
    maint_change = abs(parse_margin_number(margin_row.get("maintMarginChange")))
    init_change = abs(parse_margin_number(margin_row.get("initMarginChange")))
    binding_change = max(
        value for value in [maint_change, init_change] if not math.isnan(value)
    ) if any(not math.isnan(value) for value in [maint_change, init_change]) else math.nan
    usable_liquidity = max(excess_liquidity - safety_buffer, 0.0) if not math.isnan(excess_liquidity) else math.nan
    max_contracts = math.floor(usable_liquidity / binding_change) if binding_change and not math.isnan(usable_liquidity) else math.nan
    return {
        "excessLiquidity": excess_liquidity,
        "safetyBuffer": safety_buffer,
        "usableLiquidity": usable_liquidity,
        "bindingMarginChange": binding_change,
        "estimatedMaxContracts": max_contracts,
    }
