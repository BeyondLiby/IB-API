from __future__ import annotations

import math
from typing import Any

import pandas as pd

try:
    from .contracts import contract_label
    from .utils import clean_number, is_valid_number, summary_value
except ImportError:
    from contracts import contract_label
    from utils import clean_number, is_valid_number, summary_value


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


def position_number(value: Any) -> float:
    """Convert position size while preserving valid short quantity -1."""
    if value is None:
        return math.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if not math.isnan(number) else math.nan


def account_positions_frame(
    all_positions: list[Any],
    portfolio_map: dict[int, Any],
) -> pd.DataFrame:
    """Build a compact all-account position view enriched with portfolio PnL."""
    rows: list[dict[str, Any]] = []
    for pos in all_positions:
        contract = getattr(pos, "contract", None)
        con_id = int(getattr(contract, "conId", 0) or 0)
        item = portfolio_map.get(con_id)
        quantity = position_number(getattr(pos, "position", math.nan))
        avg_cost = clean_number(getattr(pos, "avgCost", math.nan))
        rows.append(
            {
                "account": getattr(pos, "account", ""),
                "symbol": getattr(contract, "symbol", ""),
                "localSymbol": contract_label(contract),
                "secType": getattr(contract, "secType", ""),
                "expiry": getattr(contract, "lastTradeDateOrContractMonth", ""),
                "direction": str(getattr(contract, "right", "") or "").upper(),
                "strike": clean_number(getattr(contract, "strike", math.nan)),
                "position": quantity,
                "avgCost": avg_cost,
                "marketPrice": clean_number(getattr(item, "marketPrice", math.nan)) if item is not None else math.nan,
                "marketValue": clean_number(getattr(item, "marketValue", math.nan)) if item is not None else math.nan,
                "unrealizedPnL": clean_number(getattr(item, "unrealizedPNL", math.nan)) if item is not None else math.nan,
                "realizedPnL": clean_number(getattr(item, "realizedPNL", math.nan)) if item is not None else math.nan,
                "conId": con_id,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["secType", "symbol", "expiry", "strike", "localSymbol"], ignore_index=True)


def zf_option_carry_frame(
    frame: pd.DataFrame,
    *,
    target_return: float,
    capital_base: float,
    require_complete_greeks: bool = False,
) -> pd.DataFrame:
    """Build a ZF futures-option table sorted for monthly carry planning."""
    if frame.empty:
        return pd.DataFrame()
    symbol_series = frame["symbol"].astype(str).str.upper() if "symbol" in frame.columns else pd.Series("", index=frame.index)
    sec_type_series = frame["secType"].astype(str).str.upper() if "secType" in frame.columns else pd.Series("", index=frame.index)
    options = frame[(symbol_series == "ZF") & (sec_type_series == "FOP")].copy()
    if options.empty:
        return pd.DataFrame()

    for col in ["position", "strike", "delta", "gamma", "price", "multiplier"]:
        if col not in options.columns:
            options[col] = math.nan
        options[col] = pd.to_numeric(options[col], errors="coerce")

    options["dte"] = options["expiry"].map(expiry_days)
    options["direction"] = options["right"].map(
        lambda value: "put" if str(value).upper() == "P" else "call" if str(value).upper() == "C" else str(value).lower()
    )
    options["directionSort"] = options["direction"].map({"put": 0, "call": 1}).fillna(9)
    options["signedDelta"] = options["delta"]
    options["absDelta"] = options["delta"].abs()
    options["delta"] = options["absDelta"]
    options["greekReady"] = options["absDelta"].notna() & options["gamma"].notna()
    options["tradeCandidateReady"] = options["greekReady"] & options["price"].notna()
    if require_complete_greeks:
        options = options[options["tradeCandidateReady"]].copy()
        if options.empty:
            return pd.DataFrame()
    options["premiumPerContract"] = options["price"] * options["multiplier"].fillna(1.0)
    target_premium = capital_base * target_return if is_valid_number(capital_base) else math.nan
    options["targetMonthlyPremium"] = target_premium
    options["contractsForTarget"] = target_premium / options["premiumPerContract"].abs()
    options["currentCarryPremium"] = options["position"].clip(upper=0).abs() * options["premiumPerContract"].abs()
    options["deltaAtTarget"] = options["contractsForTarget"] * options["absDelta"]

    columns = [
        "dte",
        "direction",
        "strike",
        "delta",
        "price",
        "gamma",
        "signedDelta",
        "position",
        "premiumPerContract",
        "currentCarryPremium",
        "targetMonthlyPremium",
        "contractsForTarget",
        "deltaAtTarget",
        "localSymbol",
        "expiry",
        "iv",
        "theta",
        "greekReady",
        "tradeCandidateReady",
        "priceSource",
        "greekSource",
        "missingData",
        "marketValue",
        "unrealizedPnL",
    ]
    out = options[[col for col in columns if col in options.columns]].copy()
    sort_cols = [col for col in ["dte", "directionSort", "strike", "absDelta", "price", "gamma"] if col in options.columns]
    sorted_index = options.sort_values(sort_cols, na_position="last").index
    return out.loc[sorted_index].reset_index(drop=True)


def capital_base_value(summary: pd.DataFrame, mode: str, custom_value: float) -> float:
    """Resolve carry sizing capital base from account summary or a custom value."""
    if mode == "Custom":
        return clean_number(custom_value)
    tag_by_mode = {
        "Net liquidation": "NetLiquidation",
        "Excess liquidity": "ExcessLiquidity",
        "Available funds": "AvailableFunds",
    }
    return summary_value(summary, tag_by_mode.get(mode, "NetLiquidation"))
