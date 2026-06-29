from __future__ import annotations

import math
from typing import Any

import pandas as pd

try:
    from .utils import is_valid_number
except ImportError:
    from utils import is_valid_number


PORTFOLIO_VIEW_COLUMNS = [
    "投资产品",
    "说明",
    "最后价",
    "市场价值",
    "盈亏",
    "未实现盈亏",
    "未实现盈亏%",
    "持仓",
    "距离最后交易日天数",
    "盈亏%",
    "隐含波动率%",
    "平均价格",
    "Delta",
    "投资组合Dlt值",
    "结构",
    "明细",
]


def clean_sum(series: pd.Series) -> float:
    """Sum a numeric series while preserving NaN when every value is missing."""
    values = pd.to_numeric(series, errors="coerce")
    return values.sum() if values.notna().any() else math.nan


def weighted_average(values: pd.Series, weights: pd.Series) -> float:
    """Calculate a weighted average with NaN-aware numeric inputs."""
    numeric_values = pd.to_numeric(values, errors="coerce")
    numeric_weights = pd.to_numeric(weights, errors="coerce").abs()
    mask = numeric_values.notna() & numeric_weights.notna() & (numeric_weights > 0)
    if not mask.any():
        return math.nan
    return float((numeric_values[mask] * numeric_weights[mask]).sum() / numeric_weights[mask].sum())


def expiry_days(expiry: Any) -> float:
    """Return days from today in Asia/Shanghai to an IB YYYYMMDD expiry."""
    text = str(expiry or "")
    if len(text) < 8:
        return math.nan
    expiry_date = pd.to_datetime(text[:8], format="%Y%m%d", errors="coerce")
    if pd.isna(expiry_date):
        return math.nan
    today = pd.Timestamp.now(tz="Asia/Shanghai").normalize().tz_localize(None)
    return float((expiry_date - today).days)


def expiry_label(expiry: Any) -> str:
    """Format an IB YYYYMMDD expiry into a compact label similar to IBKR UI."""
    text = str(expiry or "")
    date = pd.to_datetime(text[:8], format="%Y%m%d", errors="coerce") if len(text) >= 8 else pd.NaT
    if pd.isna(date):
        return text
    return date.strftime("%b%d")


def option_side_name(right: str) -> str:
    """Convert option right into an English label used by IBKR-like rows."""
    return "Call" if str(right).upper() == "C" else "Put" if str(right).upper() == "P" else str(right)


def spread_product_name(spread_type: str, symbol: str) -> str:
    """Build a concise spread product name."""
    direction = "Bull" if "bull" in str(spread_type).lower() else "Bear" if "bear" in str(spread_type).lower() else "Vertical"
    return f"{symbol} {direction} Spread"


def spread_description(spread: pd.Series) -> str:
    """Build a multi-line-ish spread description for the product column."""
    expiry = expiry_label(spread.get("expiry", ""))
    short_strike = spread.get("shortStrike", math.nan)
    long_strike = spread.get("longStrike", math.nan)
    spread_type = str(spread.get("spreadType", "")).replace(" spread", "").title()
    return f"{expiry} {short_strike:g}/{long_strike:g} {spread_type} CBOT"


def single_description(row: pd.Series) -> str:
    """Build a compact single-leg option description."""
    expiry = expiry_label(row.get("expiry", ""))
    strike = row.get("strike", math.nan)
    side = option_side_name(str(row.get("right", "")))
    local = str(row.get("localSymbol", ""))
    if is_valid_number(strike):
        return f"{local} {expiry} {float(strike):g} {side} Fut.Opt CBOT"
    return f"{local} {expiry} CBOT"


def pnl_percent(unrealized_pnl: float, cost_basis: float) -> float:
    """Calculate unrealized PnL percentage from cost basis."""
    if not is_valid_number(unrealized_pnl) or not is_valid_number(cost_basis, allow_zero=False):
        return math.nan
    return unrealized_pnl / abs(cost_basis) * 100.0


def spread_row(spread: pd.Series, legs: pd.DataFrame) -> dict[str, Any]:
    """Aggregate paired option legs into one IBKR-like spread row."""
    units = float(spread.get("units", math.nan))
    multiplier = weighted_average(legs["multiplier"], legs["position"]) if "multiplier" in legs else 1.0
    market_value = clean_sum(legs["marketValue"])
    unrealized_pnl = clean_sum(legs["unrealizedPnL"])
    cost_basis = clean_sum(legs["costBasis"])
    delta_value = clean_sum(legs["systemDeltaMultiplier"])
    net_price = clean_sum(legs["position"] * legs["price"]) / units if is_valid_number(units, allow_zero=False) and "price" in legs else math.nan
    avg_price = clean_sum(legs["position"] * legs["avgCost"]) / units if is_valid_number(units, allow_zero=False) and "avgCost" in legs else math.nan
    delta = delta_value / (units * multiplier) if is_valid_number(delta_value) and is_valid_number(units, allow_zero=False) and is_valid_number(multiplier, allow_zero=False) else math.nan
    symbol = str(spread.get("symbol", "ZF"))
    return {
        "投资产品": spread_product_name(str(spread.get("spreadType", "")), symbol),
        "说明": spread_description(spread),
        "最后价": net_price,
        "市场价值": market_value,
        "盈亏": unrealized_pnl,
        "未实现盈亏": unrealized_pnl,
        "未实现盈亏%": pnl_percent(unrealized_pnl, cost_basis),
        "持仓": units,
        "距离最后交易日天数": expiry_days(spread.get("expiry", "")),
        "盈亏%": math.nan,
        "隐含波动率%": weighted_average(legs["iv"], legs["position"]) * 100.0 if "iv" in legs else math.nan,
        "平均价格": avg_price,
        "Delta": delta,
        "投资组合Dlt值": delta_value,
        "结构": spread.get("spreadType", ""),
        "明细": f"{spread.get('shortLeg', '')} / {spread.get('longLeg', '')}",
    }


def single_row(row: pd.Series) -> dict[str, Any]:
    """Convert one unpaired option leg into an IBKR-like row."""
    position = row.get("position", math.nan)
    multiplier = row.get("multiplier", math.nan)
    delta_value = row.get("systemDeltaMultiplier", math.nan)
    delta = delta_value / (float(position) * float(multiplier)) if is_valid_number(delta_value) and is_valid_number(position, allow_zero=False) and is_valid_number(multiplier, allow_zero=False) else row.get("delta", math.nan)
    return {
        "投资产品": row.get("symbol", "ZF"),
        "说明": single_description(row),
        "最后价": row.get("price", math.nan),
        "市场价值": row.get("marketValue", math.nan),
        "盈亏": row.get("unrealizedPnL", math.nan),
        "未实现盈亏": row.get("unrealizedPnL", math.nan),
        "未实现盈亏%": pnl_percent(row.get("unrealizedPnL", math.nan), row.get("costBasis", math.nan)),
        "持仓": position,
        "距离最后交易日天数": expiry_days(row.get("expiry", "")),
        "盈亏%": math.nan,
        "隐含波动率%": row.get("iv", math.nan) * 100.0 if is_valid_number(row.get("iv", math.nan)) else math.nan,
        "平均价格": row.get("avgCost", math.nan),
        "Delta": delta,
        "投资组合Dlt值": delta_value,
        "结构": row.get("spreadType", "") or "single option",
        "明细": row.get("optionName", ""),
    }


def build_portfolio_view(frame: pd.DataFrame, spread_summary: pd.DataFrame | None = None) -> pd.DataFrame:
    """Build an IBKR-like portfolio view for Streamlit and notebook debugging."""
    if frame.empty:
        return pd.DataFrame(columns=PORTFOLIO_VIEW_COLUMNS)
    frame = frame.copy()
    for col in ["spreadId", "spreadType", "spreadRole", "spreadUnits", "spreadPartner", "spreadWidthTicks", "spreadSource"]:
        if col not in frame.columns:
            frame[col] = ""
    spread_summary = spread_summary if spread_summary is not None else pd.DataFrame()
    rows: list[dict[str, Any]] = []

    if spread_summary is not None and not spread_summary.empty:
        for _, spread in spread_summary.iterrows():
            legs = frame[frame["spreadId"] == spread["spreadId"]]
            if not legs.empty:
                rows.append(spread_row(spread, legs))

    paired_ids = set(spread_summary["spreadId"].dropna().astype(str)) if spread_summary is not None and not spread_summary.empty else set()
    singles = frame[
        (frame["secType"].astype(str) == "FOP")
        & (~frame["spreadId"].astype(str).isin(paired_ids))
    ].copy()
    for _, row in singles.iterrows():
        rows.append(single_row(row))

    view = pd.DataFrame(rows)
    if view.empty:
        return pd.DataFrame(columns=PORTFOLIO_VIEW_COLUMNS)
    return view[PORTFOLIO_VIEW_COLUMNS].sort_values(["结构", "说明", "投资产品"], ignore_index=True)
