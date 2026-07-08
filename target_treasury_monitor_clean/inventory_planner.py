from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
import math
import re
from typing import Any, Iterable


EPSILON = 1e-9
PRICE_SCENARIOS = [-1.00, -0.75, -0.50, -0.25, 0.25, 0.50, 0.75, 1.00]
EXPECTED_CAPTURE_RATE = {
    "1-2DTE": 0.55,
    "3-4DTE": 0.60,
    "5-8DTE": 0.65,
    "9-15DTE": 0.70,
}


@dataclass(frozen=True)
class PlannerConfig:
    capital: float = 20_000.0
    monthly_target_return: float = 0.10
    month_to_date_realized_pnl: float = 0.0
    remaining_trading_days: int = 20
    allowed_underlyings: tuple[str, ...] = ("ZF", "ZN", "ZC")
    put_dte_min: int = 0
    put_dte_max: int = 15
    call_dte_min: int = 0
    call_dte_max: int = 4
    new_trade_min_dte: int = 1
    new_trade_max_dte_put: int = 15
    new_trade_max_dte_call: int = 4
    put_strike_zone: dict[str, tuple[float, float]] = field(default_factory=dict)
    call_strike_zone: dict[str, tuple[float, float]] = field(default_factory=dict)
    preferred_put_delta_range: tuple[float, float] = (0.05, 0.35)
    preferred_call_delta_range: tuple[float, float] = (0.03, 0.20)
    max_margin_usage: float | None = None
    contract_multiplier: dict[str, float] = field(default_factory=lambda: {"ZF": 1000.0, "ZN": 1000.0, "ZC": 50.0})
    yield_to_price_sensitivity: dict[str, float] = field(default_factory=dict)
    credit_source: str = "mid"
    scoring_weights: dict[str, float] = field(
        default_factory=lambda: {
            "distribution": 0.35,
            "income": 0.25,
            "risk": 0.30,
            "target": 0.10,
        }
    )
    risk_weights: dict[str, float] = field(
        default_factory=lambda: {
            "delta": 12.0,
            "gamma": 8.0,
            "vega": 1.0,
            "margin": 0.001,
            "stress": 0.002,
            "spread": 10.0,
        }
    )


@dataclass(frozen=True)
class ShortPosition:
    underlying: str
    expiry: str
    dte: int
    right: str
    strike: float
    position: float
    abs_contracts: float
    remaining_premium: float
    unrealized_pnl: float
    delta: float
    gamma: float
    theta: float
    vega: float
    multiplier: float
    local_symbol: str = ""
    option_name: str = ""
    con_id: str = ""

    @property
    def node(self) -> str:
        return f"{self.underlying}-{self.right}-{self.strike:g}"

    @property
    def position_delta(self) -> float:
        return self.position * self.delta

    @property
    def position_gamma(self) -> float:
        return self.position * self.gamma

    @property
    def position_theta(self) -> float:
        return self.position * self.theta

    @property
    def position_vega(self) -> float:
        return self.position * self.vega


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    underlying: str
    expiry: str
    dte: int
    dte_bucket: str
    right: str
    strike: float
    bid: float
    ask: float
    mid: float
    estimated_credit: float
    delta: float
    gamma: float
    theta: float
    vega: float
    volume: float
    open_interest: float
    margin_estimate: float
    bid_ask_spread: float
    income_score: float
    distribution_score: float
    risk_score: float
    target_fit_score: float
    final_score: float
    warnings: tuple[str, ...] = ()

    @property
    def node(self) -> str:
        return f"{self.underlying}-{self.right}-{self.strike:g}"


def to_float(value: Any, default: float = math.nan) -> float:
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else default
    text = str(value).strip().replace("$", "").replace("%", "").replace(",", "")
    if not text or text.lower() in {"nan", "none", "missing"}:
        return default
    try:
        number = float(text)
    except ValueError:
        return default
    return number if math.isfinite(number) else default


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def parse_underlying(row: dict[str, Any]) -> str:
    explicit = text(row.get("underlying") or row.get("product") or row.get("symbol") or row.get("root") or row.get("tradingClass")).upper()
    if explicit in {"ZF", "ZN", "ZC", "ZT", "TN", "ZB", "UB"}:
        return explicit
    for key in ("optionName", "localSymbol", "contract", "description", "name", "conId"):
        candidate = text(row.get(key)).upper()
        if not candidate:
            continue
        match = re.search(r"\b(ZF|ZN|ZC|ZT|TN|ZB|UB)\b|^(ZF|ZN|ZC|ZT|TN|ZB|UB)[-\s_]|^(ZF|ZN|ZC|ZT|TN|ZB|UB)[FGHJKMNQUVXZ]\d\b", candidate)
        if match:
            return next(group for group in match.groups() if group)
        if candidate.startswith("OZN"):
            return "ZN"
        if candidate.startswith("OZC"):
            return "ZC"
    return ""


def normalize_right(value: Any) -> str:
    raw = text(value).upper()
    if raw in {"P", "PUT"}:
        return "P"
    if raw in {"C", "CALL"}:
        return "C"
    return raw[:1]


def parse_expiry(value: Any) -> date | None:
    raw = text(value)[:10].replace("-", "")
    if not re.match(r"^\d{8}$", raw):
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
    except ValueError:
        return None


def resolve_as_of(as_of: date | datetime | str | None = None) -> date:
    if as_of is None or text(as_of).lower() == "now":
        return datetime.now(timezone.utc).date()
    if isinstance(as_of, datetime):
        return as_of.date()
    if isinstance(as_of, date):
        return as_of
    parsed = parse_expiry(as_of)
    if parsed:
        return parsed
    return datetime.fromisoformat(text(as_of).replace("Z", "+00:00")).date()


def dte_bucket(dte: int | float) -> str:
    if not math.isfinite(float(dte)):
        return "15DTE+"
    value = int(dte)
    if value <= 0:
        return "0DTE"
    if value <= 2:
        return "1-2DTE"
    if value <= 4:
        return "3-4DTE"
    if value <= 8:
        return "5-8DTE"
    if value <= 15:
        return "9-15DTE"
    return "15DTE+"


def dte_from_row(row: dict[str, Any], as_of: date) -> int:
    explicit = to_float(row.get("DTE", row.get("dte")))
    if math.isfinite(explicit):
        return int(round(explicit))
    expiry = parse_expiry(row.get("expiry") or row.get("expiration") or row.get("lastTradeDateOrContractMonth"))
    if not expiry:
        return 999
    return (expiry - as_of).days


def multiplier_for(row: dict[str, Any], underlying: str, config: PlannerConfig) -> float:
    if underlying == "ZC":
        return config.contract_multiplier.get("ZC", 50.0)
    value = to_float(row.get("multiplier"))
    if math.isfinite(value) and value > 0:
        return value
    return config.contract_multiplier.get(underlying, 1000.0)


def parse_short_positions(rows: Iterable[dict[str, Any]], config: PlannerConfig, as_of: date | datetime | str | None = None) -> list[ShortPosition]:
    today = resolve_as_of(as_of)
    allowed = set(config.allowed_underlyings)
    out: list[ShortPosition] = []
    for row in rows:
        position = to_float(row.get("position"))
        if not math.isfinite(position) or position >= 0:
            continue
        sec_type = text(row.get("secType")).upper()
        right = normalize_right(row.get("right") or row.get("direction"))
        if sec_type and sec_type not in {"FOP", "OPT"}:
            continue
        if right not in {"P", "C"}:
            continue
        underlying = parse_underlying(row)
        if allowed and underlying not in allowed:
            continue
        dte = dte_from_row(row, today)
        if dte < 0:
            continue
        if right == "P" and not (config.put_dte_min <= dte <= config.put_dte_max):
            continue
        if right == "C" and not (config.call_dte_min <= dte <= config.call_dte_max):
            continue
        market_value = to_float(row.get("marketValue"), 0.0)
        remaining_premium = -market_value if market_value < 0 else abs(position) * abs(first_finite(row, ["mid", "price", "last", "bid", "ask"], 0.0)) * multiplier_for(row, underlying, config)
        out.append(
            ShortPosition(
                underlying=underlying,
                expiry=text(row.get("expiry") or row.get("expiration") or row.get("lastTradeDateOrContractMonth")),
                dte=dte,
                right=right,
                strike=to_float(row.get("strike"), 0.0),
                position=position,
                abs_contracts=abs(position),
                remaining_premium=remaining_premium,
                unrealized_pnl=to_float(row.get("unrealizedPnL"), 0.0),
                delta=to_float(row.get("delta"), 0.0),
                gamma=to_float(row.get("gamma"), 0.0),
                theta=to_float(row.get("theta"), 0.0),
                vega=to_float(row.get("vega"), 0.0),
                multiplier=multiplier_for(row, underlying, config),
                local_symbol=text(row.get("localSymbol")),
                option_name=text(row.get("optionName")),
                con_id=text(row.get("conId")),
            )
        )
    return out


def first_finite(row: dict[str, Any], fields: list[str], default: float = math.nan) -> float:
    for field in fields:
        value = to_float(row.get(field))
        if math.isfinite(value):
            return value
    return default


def aggregate_inventory(positions: list[ShortPosition]) -> dict[str, Any]:
    total_premium = sum(p.remaining_premium for p in positions)
    total_gamma_abs = sum(abs(p.position_gamma) for p in positions)
    total_theta_abs = sum(abs(p.position_theta) for p in positions)
    by_dte = _aggregate(positions, lambda p: p.dte_bucket if hasattr(p, "dte_bucket") else dte_bucket(p.dte), total_premium, total_gamma_abs, total_theta_abs)
    return {
        "totalContracts": sum(p.abs_contracts for p in positions),
        "remainingPremium": total_premium,
        "unrealizedPnL": sum(p.unrealized_pnl for p in positions),
        "deltaExposure": sum(p.position_delta for p in positions),
        "gammaExposure": sum(p.position_gamma for p in positions),
        "thetaExposure": sum(p.position_theta for p in positions),
        "vegaExposure": sum(p.position_vega for p in positions),
        "byUnderlying": _aggregate(positions, lambda p: p.underlying, total_premium, total_gamma_abs, total_theta_abs),
        "bySide": _aggregate(positions, lambda p: p.right, total_premium, total_gamma_abs, total_theta_abs),
        "byDteBucket": by_dte,
        "byExpiry": _aggregate(positions, lambda p: p.expiry, total_premium, total_gamma_abs, total_theta_abs),
        "byStrikeNode": _aggregate(positions, lambda p: p.node, total_premium, total_gamma_abs, total_theta_abs),
    }


def _aggregate(
    positions: list[ShortPosition],
    key_fn: Any,
    total_premium: float,
    total_gamma_abs: float,
    total_theta_abs: float,
) -> list[dict[str, Any]]:
    groups: dict[str, list[ShortPosition]] = {}
    for position in positions:
        groups.setdefault(str(key_fn(position)), []).append(position)
    rows = []
    for key, items in groups.items():
        premium = sum(p.remaining_premium for p in items)
        gamma_abs = sum(abs(p.position_gamma) for p in items)
        theta_abs = sum(abs(p.position_theta) for p in items)
        rows.append(
            {
                "key": key,
                "contracts": sum(p.abs_contracts for p in items),
                "remainingPremium": premium,
                "unrealizedPnL": sum(p.unrealized_pnl for p in items),
                "deltaExposure": sum(p.position_delta for p in items),
                "gammaExposure": sum(p.position_gamma for p in items),
                "thetaExposure": sum(p.position_theta for p in items),
                "vegaExposure": sum(p.position_vega for p in items),
                "premiumWeight": premium / max(total_premium, EPSILON),
                "gammaWeight": gamma_abs / max(total_gamma_abs, EPSILON),
                "thetaWeight": theta_abs / max(total_theta_abs, EPSILON),
            }
        )
    return sorted(rows, key=lambda row: (-row["remainingPremium"], row["key"]))


def target_pressure(positions: list[ShortPosition], config: PlannerConfig) -> dict[str, Any]:
    monthly_target = config.capital * config.monthly_target_return
    remaining_target = monthly_target - config.month_to_date_realized_pnl
    daily_target = remaining_target / max(config.remaining_trading_days, 1)
    current_premium = sum(p.remaining_premium for p in positions)
    ratio = remaining_target / max(current_premium, EPSILON)
    if ratio < 0.5:
        label = "LOW"
    elif ratio <= 1.0:
        label = "NORMAL"
    elif ratio <= 1.5:
        label = "ELEVATED"
    else:
        label = "HIGH"
    return {
        "monthlyTargetProfit": monthly_target,
        "remainingTarget": remaining_target,
        "dailyTargetReference": daily_target,
        "currentShortRemainingPremium": current_premium,
        "targetPressureRatio": ratio,
        "targetPressureLabel": label,
    }


def scan_candidates(rows: Iterable[dict[str, Any]], positions: list[ShortPosition], config: PlannerConfig, as_of: date | datetime | str | None = None) -> list[Candidate]:
    today = resolve_as_of(as_of)
    inventory = aggregate_inventory(positions)
    pressure = target_pressure(positions, config)
    allowed = set(config.allowed_underlyings)
    candidates: list[Candidate] = []
    for index, row in enumerate(rows):
        underlying = parse_underlying(row)
        if allowed and underlying not in allowed:
            continue
        right = normalize_right(row.get("right") or row.get("direction"))
        if right not in {"P", "C"}:
            continue
        dte = dte_from_row(row, today)
        if right == "P" and not (config.new_trade_min_dte <= dte <= config.new_trade_max_dte_put):
            continue
        if right == "C" and not (config.new_trade_min_dte <= dte <= config.new_trade_max_dte_call):
            continue
        strike = to_float(row.get("strike"))
        if not math.isfinite(strike):
            continue
        if right == "P" and not strike_allowed(strike, config.put_strike_zone.get(underlying), "P"):
            continue
        if right == "C" and not strike_allowed(strike, config.call_strike_zone.get(underlying), "C"):
            continue
        delta = signed_delta(row, right)
        abs_delta = abs(delta)
        lo, hi = config.preferred_put_delta_range if right == "P" else config.preferred_call_delta_range
        if not (lo <= abs_delta <= hi):
            continue
        bid = first_finite(row, ["bid"], math.nan)
        ask = first_finite(row, ["ask"], math.nan)
        mid = first_finite(row, ["mid", "price", "last", "modelOptionPrice", "optPrice"], math.nan)
        if not any(math.isfinite(x) and x > 0 for x in [bid, ask, mid]):
            continue
        if not math.isfinite(mid) and math.isfinite(bid) and math.isfinite(ask):
            mid = (bid + ask) / 2
        estimated_credit = bid if config.credit_source == "bid" and math.isfinite(bid) and bid > 0 else mid
        if not math.isfinite(estimated_credit) or estimated_credit <= 0:
            continue
        bucket = dte_bucket(dte)
        spread = max(ask - bid, 0.0) if math.isfinite(ask) and math.isfinite(bid) else 0.0
        gamma = first_finite(row, ["gamma", "modelGreeks_gamma", "bidGreeks_gamma", "askGreeks_gamma"], 0.0)
        theta = first_finite(row, ["theta", "modelGreeks_theta", "bidGreeks_theta", "askGreeks_theta"], 0.0)
        vega = first_finite(row, ["vega", "modelGreeks_vega", "bidGreeks_vega", "askGreeks_vega"], 0.0)
        margin_estimate = first_finite(row, ["marginEstimate"], estimated_credit * multiplier_for(row, underlying, config) * 6)
        stress_loss = abs(delta) * 0.75 * multiplier_for(row, underlying, config) + abs(gamma) * 0.5 * 0.75 * 0.75 * multiplier_for(row, underlying, config)
        income_score = estimated_credit * EXPECTED_CAPTURE_RATE.get(bucket, 0.5) * multiplier_for(row, underlying, config)
        distribution_score = candidate_distribution_score(inventory, underlying, right, bucket, f"{underlying}-{right}-{strike:g}")
        risk_score = candidate_risk_score(abs_delta, gamma, vega, margin_estimate, stress_loss, spread, config)
        target_fit = min((estimated_credit * multiplier_for(row, underlying, config)) / max(pressure["dailyTargetReference"], EPSILON), 3.0)
        weights = config.scoring_weights
        final_score = (
            income_score * weights.get("income", 0.25)
            + distribution_score * weights.get("distribution", 0.35)
            + target_fit * 100 * weights.get("target", 0.10)
            - risk_score * weights.get("risk", 0.30)
        )
        warnings = candidate_warnings(inventory, right, bucket, f"{underlying}-{right}-{strike:g}", distribution_score)
        candidates.append(
            Candidate(
                candidate_id=text(row.get("conId")) or f"{underlying}-{right}-{strike:g}-{dte}-{index}",
                underlying=underlying,
                expiry=text(row.get("expiry") or row.get("expiration") or row.get("lastTradeDateOrContractMonth")),
                dte=dte,
                dte_bucket=bucket,
                right=right,
                strike=strike,
                bid=bid,
                ask=ask,
                mid=mid,
                estimated_credit=estimated_credit,
                delta=delta,
                gamma=gamma,
                theta=theta,
                vega=vega,
                volume=first_finite(row, ["volume", "rawVolume", "callVolume", "putVolume"], 0.0),
                open_interest=first_finite(row, ["openInterest", "rawOpenInterest", "callOpenInterest", "putOpenInterest"], 0.0),
                margin_estimate=margin_estimate,
                bid_ask_spread=spread,
                income_score=income_score,
                distribution_score=distribution_score,
                risk_score=risk_score,
                target_fit_score=target_fit,
                final_score=final_score,
                warnings=warnings,
            )
        )
    return sorted(candidates, key=lambda candidate: candidate.final_score, reverse=True)


def signed_delta(row: dict[str, Any], right: str) -> float:
    delta = first_finite(row, ["delta", "modelGreeks_delta", "bidGreeks_delta", "askGreeks_delta"], 0.0)
    if right == "P" and delta > 0:
        return -delta
    return delta


def strike_allowed(strike: float, zone: tuple[float, float] | None, right: str) -> bool:
    if not zone:
        return True
    lo, hi = min(zone), max(zone)
    if right == "P":
        return strike <= hi
    return strike >= lo


def candidate_distribution_score(inventory: dict[str, Any], underlying: str, right: str, bucket: str, node: str) -> float:
    dte_weight = _weight_for(inventory["byDteBucket"], bucket, "premiumWeight")
    side_weight = _weight_for(inventory["bySide"], right, "premiumWeight")
    node_weight = _weight_for(inventory["byStrikeNode"], node, "premiumWeight")
    score = 0.0
    score += 40 if dte_weight < 0.15 else -40 if dte_weight > 0.35 else 5
    score += 25 if node_weight < 0.10 else -35 if node_weight > 0.25 else 5
    score += 20 if side_weight < 0.35 else -20 if side_weight > 0.70 else 5
    score += 5 if underlying else 0
    return score


def _weight_for(rows: list[dict[str, Any]], key: str, field: str) -> float:
    for row in rows:
        if row["key"] == key:
            return float(row.get(field, 0.0))
    return 0.0


def candidate_risk_score(abs_delta: float, gamma: float, vega: float, margin: float, stress_loss: float, spread: float, config: PlannerConfig) -> float:
    weights = config.risk_weights
    return (
        abs_delta * weights.get("delta", 12.0)
        + abs(gamma) * weights.get("gamma", 8.0)
        + abs(vega) * weights.get("vega", 1.0)
        + max(margin, 0.0) * weights.get("margin", 0.001)
        + max(stress_loss, 0.0) * weights.get("stress", 0.002)
        + max(spread, 0.0) * weights.get("spread", 10.0)
    )


def candidate_warnings(inventory: dict[str, Any], right: str, bucket: str, node: str, distribution_score: float) -> tuple[str, ...]:
    warnings: list[str] = []
    if _weight_for(inventory["byStrikeNode"], node, "premiumWeight") > 0.25:
        warnings.append("该节点正在变得集中")
    if _weight_for(inventory["byStrikeNode"], node, "gammaWeight") > 0.35:
        warnings.append("该节点主导 Gamma 暴露")
    if _weight_for(inventory["byDteBucket"], bucket, "premiumWeight") > 0.35:
        warnings.append("加入这里会增加 DTE 集中度")
    if _weight_for(inventory["bySide"], right, "premiumWeight") > 0.70:
        warnings.append("加入这里会增加方向集中度")
    if distribution_score > 40:
        warnings.append("加入这里有助于改善分布平衡")
    return tuple(warnings)


def portfolio_exposure(
    positions: list[ShortPosition],
    candidates: list[Candidate],
    proposed_quantities: dict[str, float],
    config: PlannerConfig,
) -> dict[str, Any]:
    before = exposure_from_positions(positions)
    added = exposure_from_candidates(candidates, proposed_quantities, config)
    after = {key: before.get(key, 0.0) + added.get(key, 0.0) for key in before}
    return {"before": before, "added": added, "after": after}


def exposure_from_positions(positions: list[ShortPosition]) -> dict[str, float]:
    return {
        "totalRemainingPremium": sum(p.remaining_premium for p in positions),
        "netDelta": sum(p.position_delta for p in positions),
        "netGamma": sum(p.position_gamma for p in positions),
        "netTheta": sum(p.position_theta for p in positions),
        "netVega": sum(p.position_vega for p in positions),
        "estimatedMargin": 0.0,
    }


def exposure_from_candidates(candidates: list[Candidate], proposed_quantities: dict[str, float], config: PlannerConfig) -> dict[str, float]:
    out = {"totalRemainingPremium": 0.0, "netDelta": 0.0, "netGamma": 0.0, "netTheta": 0.0, "netVega": 0.0, "estimatedMargin": 0.0}
    for candidate in candidates:
        qty = to_float(proposed_quantities.get(candidate.candidate_id), 0.0)
        if qty <= 0:
            continue
        multiplier = config.contract_multiplier.get(candidate.underlying, 1000.0)
        position = -qty
        out["totalRemainingPremium"] += qty * candidate.estimated_credit * multiplier
        out["netDelta"] += position * candidate.delta
        out["netGamma"] += position * candidate.gamma
        out["netTheta"] += position * candidate.theta
        out["netVega"] += position * candidate.vega
        out["estimatedMargin"] += qty * candidate.margin_estimate
    return out


def node_exposure(positions: list[ShortPosition], candidates: list[Candidate] | None = None, proposed_quantities: dict[str, float] | None = None) -> list[dict[str, Any]]:
    nodes: dict[str, dict[str, Any]] = {}
    total_premium = sum(p.remaining_premium for p in positions)
    for position in positions:
        item = nodes.setdefault(position.node, {"node": position.node, "currentContracts": 0.0, "proposedContracts": 0.0, "remainingPremium": 0.0, "deltaWeight": 0.0, "gammaWeight": 0.0, "thetaWeight": 0.0, "vegaWeight": 0.0})
        item["currentContracts"] += position.abs_contracts
        item["remainingPremium"] += position.remaining_premium
        item["deltaWeight"] += abs(position.position_delta)
        item["gammaWeight"] += abs(position.position_gamma)
        item["thetaWeight"] += abs(position.position_theta)
        item["vegaWeight"] += abs(position.position_vega)
    if candidates and proposed_quantities:
        for candidate in candidates:
            qty = to_float(proposed_quantities.get(candidate.candidate_id), 0.0)
            if qty <= 0:
                continue
            item = nodes.setdefault(candidate.node, {"node": candidate.node, "currentContracts": 0.0, "proposedContracts": 0.0, "remainingPremium": 0.0, "deltaWeight": 0.0, "gammaWeight": 0.0, "thetaWeight": 0.0, "vegaWeight": 0.0})
            item["proposedContracts"] += qty
    rows = []
    for item in nodes.values():
        premium_weight = item["remainingPremium"] / max(total_premium, EPSILON)
        warnings = []
        if premium_weight > 0.25:
            warnings.append("该节点正在变得集中")
        if item["gammaWeight"] > 0 and item["gammaWeight"] / max(sum(v["gammaWeight"] for v in nodes.values()), EPSILON) > 0.35:
            warnings.append("该节点主导 Gamma 暴露")
        if item["proposedContracts"] > 0 and premium_weight < 0.10:
            warnings.append("加入这里有助于改善分布平衡")
        rows.append({**item, "totalContractsAfterAdjustment": item["currentContracts"] + item["proposedContracts"], "premiumWeight": premium_weight, "warnings": warnings})
    return sorted(rows, key=lambda row: (-row["totalContractsAfterAdjustment"], row["node"]))


def stress_scenarios(positions: list[ShortPosition], candidates: list[Candidate] | None = None, proposed_quantities: dict[str, float] | None = None, config: PlannerConfig | None = None) -> list[dict[str, Any]]:
    config = config or PlannerConfig()
    synthetic_positions = list(positions)
    if candidates and proposed_quantities:
        for candidate in candidates:
            qty = to_float(proposed_quantities.get(candidate.candidate_id), 0.0)
            if qty <= 0:
                continue
            synthetic_positions.append(
                ShortPosition(
                    underlying=candidate.underlying,
                    expiry=candidate.expiry,
                    dte=candidate.dte,
                    right=candidate.right,
                    strike=candidate.strike,
                    position=-qty,
                    abs_contracts=qty,
                    remaining_premium=qty * candidate.estimated_credit * config.contract_multiplier.get(candidate.underlying, 1000.0),
                    unrealized_pnl=0.0,
                    delta=candidate.delta,
                    gamma=candidate.gamma,
                    theta=candidate.theta,
                    vega=candidate.vega,
                    multiplier=config.contract_multiplier.get(candidate.underlying, 1000.0),
                )
            )
    rows = []
    for move in PRICE_SCENARIOS:
        parts = [stress_pnl(position, move, 0.0, 1 / 252) for position in synthetic_positions]
        worst_index = min(range(len(parts)), key=lambda i: parts[i]) if parts else -1
        worst = synthetic_positions[worst_index] if worst_index >= 0 else None
        rows.append(
            {
                "scenarioName": f"价格变动 {move:+.2f}",
                "underlyingMove": move,
                "estimatedPortfolioPnl": sum(parts),
                "worstNode": worst.node if worst else "",
                "worstExpiry": worst.expiry if worst else "",
                "worstSide": worst.right if worst else "",
                "deltaContribution": sum(p.position * p.multiplier * p.delta * move for p in synthetic_positions),
                "gammaContribution": sum(p.position * p.multiplier * 0.5 * p.gamma * move * move for p in synthetic_positions),
                "vegaContribution": 0.0,
            }
        )
    return rows


def stress_pnl(position: ShortPosition, underlying_move: float, iv_move: float, day_fraction: float) -> float:
    return position.position * position.multiplier * (
        position.delta * underlying_move
        + 0.5 * position.gamma * underlying_move * underlying_move
        + position.vega * iv_move
        + position.theta * day_fraction
    )
