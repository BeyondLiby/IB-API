from __future__ import annotations

import math

import pandas as pd

try:
    from .config import DEFAULT_TICK_SIZE
    from .utils import is_valid_number
except ImportError:
    from config import DEFAULT_TICK_SIZE
    from utils import is_valid_number


def classify_vertical(right: str, short_strike: float, long_strike: float) -> str:
    """Classify a same-expiry same-right vertical spread by leg direction."""
    if right == "P":
        return "bull put spread" if short_strike > long_strike else "bear put spread"
    if right == "C":
        return "bear call spread" if short_strike < long_strike else "bull call spread"
    return "vertical spread"


def valid_position_quantity(value: object) -> bool:
    """Return whether a position quantity is non-zero, allowing -1 as a real short."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(number) and number != 0.0


def add_empty_spread_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Ensure a frame has spread columns without pairing any legs."""
    annotated = frame.copy()
    for col, default in [
        ("spreadId", ""),
        ("spreadType", ""),
        ("spreadRole", ""),
        ("spreadUnits", math.nan),
        ("spreadPartner", ""),
        ("spreadWidthTicks", math.nan),
        ("spreadSource", ""),
    ]:
        if col not in annotated.columns:
            annotated[col] = default
    return annotated


def pair_vertical_spreads(frame: pd.DataFrame, tick_size: float = DEFAULT_TICK_SIZE) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pair option legs into vertical spread summaries and annotate leg rows."""
    if frame.empty or "secType" not in frame.columns:
        return frame.copy(), pd.DataFrame()

    annotated = add_empty_spread_columns(frame)

    option_rows = annotated[annotated["secType"].astype(str) == "FOP"].copy()
    summaries: list[dict[str, object]] = []
    spread_no = 1

    for (symbol, expiry, right), group in option_rows.groupby(["symbol", "expiry", "right"], dropna=False):
        remaining = {idx: abs(float(row["position"])) for idx, row in group.iterrows() if valid_position_quantity(row["position"])}
        shorts = group[group["position"] < 0].sort_values("strike")
        longs = group[group["position"] > 0].sort_values("strike")

        for short_idx, short_row in shorts.iterrows():
            while remaining.get(short_idx, 0.0) > 0:
                candidates = longs[[remaining.get(idx, 0.0) > 0 for idx in longs.index]].copy()
                if candidates.empty:
                    break
                candidates["distance"] = (candidates["strike"] - short_row["strike"]).abs()
                long_idx = candidates.sort_values(["distance", "strike"]).index[0]
                long_row = annotated.loc[long_idx]
                units = min(remaining[short_idx], remaining[long_idx])
                if units <= 0:
                    break

                spread_id = f"SP{spread_no:03d}"
                spread_no += 1
                short_strike = float(short_row["strike"])
                long_strike = float(long_row["strike"])
                width = abs(short_strike - long_strike)
                width_ticks = width / tick_size if tick_size > 0 else math.nan
                spread_type = classify_vertical(str(right), short_strike, long_strike)

                short_price = float(short_row["price"]) if is_valid_number(short_row.get("price")) else math.nan
                long_price = float(long_row["price"]) if is_valid_number(long_row.get("price")) else math.nan
                net_credit = short_price - long_price if is_valid_number(short_price) and is_valid_number(long_price) else math.nan
                max_risk = width - net_credit if is_valid_number(net_credit) else math.nan

                annotated.loc[[short_idx, long_idx], "spreadId"] = spread_id
                annotated.loc[[short_idx, long_idx], "spreadType"] = spread_type
                annotated.loc[[short_idx, long_idx], "spreadUnits"] = units
                annotated.loc[[short_idx, long_idx], "spreadWidthTicks"] = round(width_ticks, 1) if is_valid_number(width_ticks) else math.nan
                annotated.loc[[short_idx, long_idx], "spreadSource"] = "inferred"
                annotated.loc[short_idx, "spreadRole"] = "short"
                annotated.loc[long_idx, "spreadRole"] = "long"
                annotated.loc[short_idx, "spreadPartner"] = str(long_row["optionName"])
                annotated.loc[long_idx, "spreadPartner"] = str(short_row["optionName"])

                summaries.append(
                    {
                        "spreadId": spread_id,
                        "spreadType": spread_type,
                        "spreadSource": "inferred",
                        "symbol": symbol,
                        "expiry": expiry,
                        "right": right,
                        "units": units,
                        "shortLeg": short_row["optionName"],
                        "longLeg": long_row["optionName"],
                        "shortStrike": short_strike,
                        "longStrike": long_strike,
                        "width": width,
                        "widthTicks": round(width_ticks, 1) if is_valid_number(width_ticks) else math.nan,
                        "netCredit": net_credit,
                        "maxRisk": max_risk,
                        "shortOtmTicks": short_row.get("otmTicks", math.nan),
                        "longOtmTicks": long_row.get("otmTicks", math.nan),
                    }
                )

                remaining[short_idx] -= units
                remaining[long_idx] -= units

        for idx, qty in remaining.items():
            if qty <= 0 or annotated.loc[idx, "spreadId"]:
                continue
            annotated.loc[idx, "spreadType"] = "unpaired option"
            annotated.loc[idx, "spreadRole"] = "single"
            annotated.loc[idx, "spreadUnits"] = qty
            annotated.loc[idx, "spreadSource"] = "unpaired"

    return annotated, pd.DataFrame(summaries)
