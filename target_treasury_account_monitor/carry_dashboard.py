from __future__ import annotations

import math
from typing import Any

import pandas as pd

try:
    from .carry_view import expiry_days
    from .utils import clean_number, is_valid_number
except ImportError:
    from carry_view import expiry_days
    from utils import clean_number, is_valid_number


OPTION_DASHBOARD_COLUMNS = [
    "flag",
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
    "marketValue",
    "unrealizedPnL",
    "absDelta",
    "deltaExposure",
    "gammaExposure",
    "thetaExposure",
    "remainingPremium",
    "isShortOption",
    "isLongOption",
    "effectiveCarry",
    "riskPremium",
    "riskLevel",
    "actionCandidate",
]
OPTION_DASHBOARD_OUTPUT_COLUMNS = OPTION_DASHBOARD_COLUMNS + ["dteBucket", "absDeltaBucket"]

SHOCK_MOVES = [-1.00, -0.50, -0.25, -0.10, 0.10, 0.25, 0.50, 1.00]
DTE_BUCKETS = ["0DTE", "1DTE", "2DTE", "3-7DTE", "8-21DTE", "22DTE+"]
DELTA_BUCKETS = ["<0.10", "0.10-0.25", "0.25-0.40", ">=0.40"]


def numeric_series(frame: pd.DataFrame, column: str, default: float = math.nan) -> pd.Series:
    """Return a numeric column aligned to the frame index."""
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def text_series(frame: pd.DataFrame, column: str, default: str = "") -> pd.Series:
    """Return a text column aligned to the frame index."""
    if column not in frame.columns:
        return pd.Series(default, index=frame.index, dtype="object")
    return frame[column].fillna(default).astype(str)


def dte_bucket(value: Any) -> str:
    """Map DTE to the requested dashboard bucket."""
    number = clean_number(value)
    if math.isnan(number):
        return "22DTE+"
    if number <= 0:
        return "0DTE"
    if number <= 1:
        return "1DTE"
    if number <= 2:
        return "2DTE"
    if number <= 7:
        return "3-7DTE"
    if number <= 21:
        return "8-21DTE"
    return "22DTE+"


def delta_bucket(value: Any) -> str:
    """Map abs delta to the requested dashboard bucket."""
    number = clean_number(value)
    if math.isnan(number):
        return ">=0.40"
    if number < 0.10:
        return "<0.10"
    if number < 0.25:
        return "0.10-0.25"
    if number < 0.40:
        return "0.25-0.40"
    return ">=0.40"


def effective_carry_value(abs_delta: float, remaining_premium: float) -> float:
    """Apply the requested delta haircut to remaining premium."""
    if not is_valid_number(remaining_premium):
        return 0.0
    if not is_valid_number(abs_delta):
        return 0.0
    if abs_delta < 0.10:
        return remaining_premium * 1.00
    if abs_delta < 0.25:
        return remaining_premium * 0.75
    if abs_delta < 0.40:
        return remaining_premium * 0.35
    return 0.0


def position_value(value: Any) -> float:
    """Parse position size while preserving a real short quantity of -1."""
    if value is None:
        return math.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    return number if not math.isnan(number) else math.nan


def risk_level(row: pd.Series) -> str:
    """Classify one option row using the requested rules."""
    position = position_value(row.get("position", math.nan))
    dte = clean_number(row.get("dte", math.nan))
    abs_delta = clean_number(row.get("absDelta", math.nan))
    if math.isnan(position) or position >= 0:
        return "Long Option / Excluded"
    if dte <= 1 and abs_delta >= 0.40:
        return "Critical"
    if dte <= 2 and abs_delta >= 0.30:
        return "Danger"
    if abs_delta >= 0.40:
        return "Danger"
    if abs_delta >= 0.25:
        return "Warning"
    if abs_delta >= 0.10:
        return "Normal"
    return "Green"


def action_candidate(row: pd.Series, *, harvest_threshold: float = 10.0) -> str:
    """Classify one short option into the requested action bucket."""
    position = position_value(row.get("position", math.nan))
    dte = clean_number(row.get("dte", math.nan))
    abs_delta = clean_number(row.get("absDelta", math.nan))
    remaining_premium = clean_number(row.get("remainingPremium", math.nan))
    if math.isnan(position) or position >= 0:
        return ""
    if dte <= 2 and abs_delta >= 0.40:
        return "Close Candidate"
    if dte <= 2 and 0.25 <= abs_delta < 0.40:
        return "Roll Candidate"
    if abs_delta < 0.10 and remaining_premium <= harvest_threshold:
        return "Harvest Candidate"
    if 0.10 <= abs_delta < 0.25 and dte >= 2:
        return "Hold Candidate"
    return ""


def normalize_option_dashboard_frame(
    frame: pd.DataFrame,
    *,
    target_return: float = 0.0,
    capital_base: float = math.nan,
    harvest_threshold: float = 10.0,
) -> pd.DataFrame:
    """Build the requested account/carry option dashboard fields from position rows."""
    if frame.empty:
        return pd.DataFrame(columns=OPTION_DASHBOARD_OUTPUT_COLUMNS)

    sec_type = text_series(frame, "secType").str.upper()
    options = frame[sec_type.eq("FOP")].copy() if "secType" in frame.columns else frame.copy()
    if options.empty:
        return pd.DataFrame(columns=OPTION_DASHBOARD_OUTPUT_COLUMNS)

    out = pd.DataFrame(index=options.index)
    out["flag"] = text_series(options, "flag")
    out["expiry"] = text_series(options, "expiry")
    if out["expiry"].eq("").all() and "lastTradeDateOrContractMonth" in options.columns:
        out["expiry"] = text_series(options, "lastTradeDateOrContractMonth")
    out["dte"] = numeric_series(options, "dte")
    out.loc[out["dte"].isna(), "dte"] = out.loc[out["dte"].isna(), "expiry"].map(expiry_days)

    direction = text_series(options, "direction").str.lower()
    if direction.eq("").all() and "right" in options.columns:
        right = text_series(options, "right").str.upper()
        direction = right.map({"P": "put", "C": "call"}).fillna(right.str.lower())
    out["direction"] = direction

    out["strike"] = numeric_series(options, "strike")
    out["signedDelta"] = numeric_series(options, "signedDelta")
    raw_delta = numeric_series(options, "delta")
    out.loc[out["signedDelta"].isna(), "signedDelta"] = raw_delta
    out["absDelta"] = out["signedDelta"].abs()
    out["delta"] = out["absDelta"]
    out["price"] = numeric_series(options, "price")
    out["gamma"] = numeric_series(options, "gamma")
    out["theta"] = numeric_series(options, "theta")
    out["position"] = numeric_series(options, "position")
    multiplier = numeric_series(options, "multiplier", 1000.0).fillna(1000.0)
    out["premiumPerContract"] = numeric_series(options, "premiumPerContract")
    out.loc[out["premiumPerContract"].isna(), "premiumPerContract"] = out["price"] * multiplier
    out["targetMonthlyPremium"] = numeric_series(options, "targetMonthlyPremium")
    target_premium = capital_base * target_return if is_valid_number(capital_base) else math.nan
    out.loc[out["targetMonthlyPremium"].isna(), "targetMonthlyPremium"] = target_premium
    out["contractsForTarget"] = numeric_series(options, "contractsForTarget")
    out.loc[out["contractsForTarget"].isna(), "contractsForTarget"] = out["targetMonthlyPremium"] / out["premiumPerContract"].abs()
    out["deltaAtTarget"] = numeric_series(options, "deltaAtTarget")
    out.loc[out["deltaAtTarget"].isna(), "deltaAtTarget"] = out["contractsForTarget"] * out["absDelta"]
    out["localSymbol"] = text_series(options, "localSymbol")
    out["iv"] = numeric_series(options, "iv")
    out["marketValue"] = numeric_series(options, "marketValue")
    out["unrealizedPnL"] = numeric_series(options, "unrealizedPnL")
    out["currentCarryPremium"] = numeric_series(options, "currentCarryPremium")
    out.loc[out["currentCarryPremium"].isna(), "currentCarryPremium"] = out["position"].clip(upper=0).abs() * out["premiumPerContract"].abs()

    out["deltaExposure"] = out["position"] * out["signedDelta"]
    out["gammaExposure"] = out["position"] * out["gamma"]
    out["thetaExposure"] = out["position"] * out["theta"]
    out["remainingPremium"] = out["marketValue"].map(lambda value: -value if clean_number(value) < 0 else 0.0)
    out["isShortOption"] = out["position"] < 0
    out["isLongOption"] = out["position"] > 0
    out["effectiveCarry"] = out.apply(lambda row: effective_carry_value(row["absDelta"], row["remainingPremium"]), axis=1)
    out["riskPremium"] = (out["remainingPremium"] - out["effectiveCarry"]).clip(lower=0)
    out["riskLevel"] = out.apply(risk_level, axis=1)
    out["actionCandidate"] = out.apply(lambda row: action_candidate(row, harvest_threshold=harvest_threshold), axis=1)
    out["dteBucket"] = out["dte"].map(dte_bucket)
    out["absDeltaBucket"] = out["absDelta"].map(delta_bucket)

    cols = [col for col in OPTION_DASHBOARD_COLUMNS if col in out.columns]
    cols += [col for col in ["dteBucket", "absDeltaBucket"] if col in out.columns]
    return out[cols].reset_index(drop=True)


def account_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Return all option positions."""
    return frame.copy()


def carry_view(frame: pd.DataFrame) -> pd.DataFrame:
    """Return only short options."""
    if frame.empty:
        return frame.copy()
    return frame[pd.to_numeric(frame["position"], errors="coerce") < 0].copy()


def summary_metrics(carry: pd.DataFrame) -> dict[str, float]:
    """Aggregate the requested carry-view metrics."""
    if carry.empty:
        return {
            "carryNetDelta": 0.0,
            "carryNetGamma": 0.0,
            "carryNetTheta": 0.0,
            "carryMarketValue": 0.0,
            "carryRemainingPremium": 0.0,
            "carryUnrealizedPnL": 0.0,
            "shortContracts": 0.0,
            "effectiveCarry": 0.0,
            "riskPremium": 0.0,
            "carryQualityRatio": math.nan,
            "0-2DTE gamma share": math.nan,
            "dangerPremium": 0.0,
        }
    gamma_abs = pd.to_numeric(carry["gammaExposure"], errors="coerce").abs()
    near_gamma_abs = gamma_abs[pd.to_numeric(carry["dte"], errors="coerce") <= 2].sum()
    total_gamma_abs = gamma_abs.sum()
    remaining = pd.to_numeric(carry["remainingPremium"], errors="coerce").sum()
    effective = pd.to_numeric(carry["effectiveCarry"], errors="coerce").sum()
    danger = carry[pd.to_numeric(carry["absDelta"], errors="coerce") >= 0.40]
    return {
        "carryNetDelta": pd.to_numeric(carry["deltaExposure"], errors="coerce").sum(),
        "carryNetGamma": pd.to_numeric(carry["gammaExposure"], errors="coerce").sum(),
        "carryNetTheta": pd.to_numeric(carry["thetaExposure"], errors="coerce").sum(),
        "carryMarketValue": pd.to_numeric(carry["marketValue"], errors="coerce").sum(),
        "carryRemainingPremium": remaining,
        "carryUnrealizedPnL": pd.to_numeric(carry["unrealizedPnL"], errors="coerce").sum(),
        "shortContracts": pd.to_numeric(carry["position"], errors="coerce").abs().sum(),
        "effectiveCarry": effective,
        "riskPremium": (remaining - effective) if is_valid_number(remaining) else math.nan,
        "carryQualityRatio": effective / remaining if remaining else math.nan,
        "0-2DTE gamma share": near_gamma_abs / total_gamma_abs if total_gamma_abs else math.nan,
        "dangerPremium": pd.to_numeric(danger["remainingPremium"], errors="coerce").sum() if not danger.empty else 0.0,
    }


def dte_bucket_summary(carry: pd.DataFrame) -> pd.DataFrame:
    """Aggregate carry view by DTE bucket."""
    rows = []
    for bucket in DTE_BUCKETS:
        one = carry[carry["dteBucket"] == bucket] if not carry.empty else carry
        rows.append(
            {
                "dteBucket": bucket,
                "netDelta": pd.to_numeric(one.get("deltaExposure", pd.Series(dtype=float)), errors="coerce").sum(),
                "netGamma": pd.to_numeric(one.get("gammaExposure", pd.Series(dtype=float)), errors="coerce").sum(),
                "netTheta": pd.to_numeric(one.get("thetaExposure", pd.Series(dtype=float)), errors="coerce").sum(),
                "remainingPremium": pd.to_numeric(one.get("remainingPremium", pd.Series(dtype=float)), errors="coerce").sum(),
                "unrealizedPnL": pd.to_numeric(one.get("unrealizedPnL", pd.Series(dtype=float)), errors="coerce").sum(),
                "dangerPremium": pd.to_numeric(one.loc[pd.to_numeric(one.get("absDelta", pd.Series(dtype=float)), errors="coerce") >= 0.40, "remainingPremium"], errors="coerce").sum() if not one.empty else 0.0,
                "contracts": pd.to_numeric(one.get("position", pd.Series(dtype=float)), errors="coerce").abs().sum(),
            }
        )
    return pd.DataFrame(rows)


def delta_bucket_summary(carry: pd.DataFrame) -> pd.DataFrame:
    """Aggregate carry view by absDelta bucket."""
    rows = []
    for bucket in DELTA_BUCKETS:
        one = carry[carry["absDeltaBucket"] == bucket] if not carry.empty else carry
        rows.append(
            {
                "absDeltaBucket": bucket,
                "contracts": pd.to_numeric(one.get("position", pd.Series(dtype=float)), errors="coerce").abs().sum(),
                "netDelta": pd.to_numeric(one.get("deltaExposure", pd.Series(dtype=float)), errors="coerce").sum(),
                "netGamma": pd.to_numeric(one.get("gammaExposure", pd.Series(dtype=float)), errors="coerce").sum(),
                "remainingPremium": pd.to_numeric(one.get("remainingPremium", pd.Series(dtype=float)), errors="coerce").sum(),
                "unrealizedPnL": pd.to_numeric(one.get("unrealizedPnL", pd.Series(dtype=float)), errors="coerce").sum(),
            }
        )
    return pd.DataFrame(rows)


def shock_pnl_table(account: pd.DataFrame, carry: pd.DataFrame, moves: list[float] | None = None) -> pd.DataFrame:
    """Calculate requested delta/gamma shock PnL for account and carry views."""
    moves = moves or SHOCK_MOVES

    def net_values(frame: pd.DataFrame) -> tuple[float, float]:
        return (
            pd.to_numeric(frame.get("deltaExposure", pd.Series(dtype=float)), errors="coerce").sum(),
            pd.to_numeric(frame.get("gammaExposure", pd.Series(dtype=float)), errors="coerce").sum(),
        )

    account_delta, account_gamma = net_values(account)
    carry_delta, carry_gamma = net_values(carry)
    rows = []
    for move in moves:
        rows.append(
            {
                "move": move,
                "accountPnL": account_delta * move * 1000 + 0.5 * account_gamma * (move**2) * 1000,
                "carryPnL": carry_delta * move * 1000 + 0.5 * carry_gamma * (move**2) * 1000,
            }
        )
    return pd.DataFrame(rows)


def action_table(carry: pd.DataFrame) -> pd.DataFrame:
    """Return short options with a non-empty action candidate."""
    if carry.empty:
        return carry.copy()
    out = carry[carry["actionCandidate"].astype(str).ne("")].copy()
    level_rank = {"Critical": 0, "Danger": 1, "Warning": 2, "Normal": 3, "Green": 4}
    out["riskRank"] = out["riskLevel"].map(level_rank).fillna(9)
    return out.sort_values(["riskRank", "dte", "absDelta", "remainingPremium"], ascending=[True, True, False, False]).drop(columns=["riskRank"])


def generate_summary_text(metrics: dict[str, float], carry: pd.DataFrame, shock: pd.DataFrame) -> str:
    """Generate a concise Chinese risk summary."""
    net_delta = metrics.get("carryNetDelta", 0.0)
    net_gamma = metrics.get("carryNetGamma", 0.0)
    gamma_share = metrics.get("0-2DTE gamma share", math.nan)
    quality = metrics.get("carryQualityRatio", math.nan)
    bias = "偏多" if net_delta > 0.05 else "偏空" if net_delta < -0.05 else "接近中性"
    delta_risk = abs(net_delta) * 0.50 * 1000
    gamma_risk = abs(0.5 * net_gamma * (0.50**2) * 1000)
    source = "delta" if delta_risk >= gamma_risk else "gamma"
    concentration = "过度集中" if is_valid_number(gamma_share) and gamma_share >= 0.50 else "未明显过度集中"
    if not is_valid_number(quality):
        quality_text = "无法判断"
    elif quality >= 0.75:
        quality_text = "较好"
    elif quality >= 0.50:
        quality_text = "一般"
    else:
        quality_text = "偏差"

    danger = carry[carry["riskLevel"].isin(["Critical", "Danger"])].copy() if not carry.empty else pd.DataFrame()
    if danger.empty:
        danger_text = "暂无 Critical/Danger short option"
    else:
        danger = danger.sort_values(["dte", "absDelta", "remainingPremium"], ascending=[True, False, False]).head(3)
        danger_text = ", ".join(
            f"{row.localSymbol}({row.riskLevel}, DTE {row.dte:.0f}, |delta| {row.absDelta:.2f})"
            for row in danger.itertuples()
        )

    def pnl_for(move: float, column: str = "carryPnL") -> float:
        rows = shock[shock["move"] == move]
        return float(rows.iloc[0][column]) if not rows.empty else math.nan

    return "\n".join(
        [
            f"- 当前 carry 组合：{bias}，carryNetDelta={net_delta:,.3f}。",
            f"- 主要风险来源：{source}；按 +/-0.50 shock 估算，delta 风险约 {delta_risk:,.0f}，gamma 风险约 {gamma_risk:,.0f}。",
            f"- 0-2DTE gamma：{concentration}，占比 {gamma_share:.1%}。" if is_valid_number(gamma_share) else "- 0-2DTE gamma：暂无可用 gamma 暴露。",
            f"- 剩余权利金质量：{quality_text}，carryQualityRatio={quality:.1%}。" if is_valid_number(quality) else "- 剩余权利金质量：无法判断。",
            f"- 最危险仓位：{danger_text}。",
            f"- Carry shock PnL：-0.25={pnl_for(-0.25):,.0f}, +0.25={pnl_for(0.25):,.0f}, -0.50={pnl_for(-0.50):,.0f}, +0.50={pnl_for(0.50):,.0f}。",
        ]
    )
