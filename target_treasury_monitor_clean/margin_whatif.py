from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import math
import re
from typing import Any

from ib_async import Contract, IB, LimitOrder, MarketOrder


class MarginWhatIfError(ValueError):
    """Raised when a margin-preview request is incomplete or unusable."""


MARGIN_ACCOUNT_TAGS = {
    "InitMarginReq",
    "MaintMarginReq",
    "AvailableFunds",
    "ExcessLiquidity",
    "FullInitMarginReq",
    "FullMaintMarginReq",
    "FullAvailableFunds",
    "FullExcessLiquidity",
    "LookAheadInitMarginReq",
    "LookAheadMaintMarginReq",
    "LookAheadAvailableFunds",
    "LookAheadExcessLiquidity",
}


@dataclass(frozen=True)
class MarginAccountSnapshot:
    """The account-level baseline used to explain a What-If result."""

    account: str
    currency: str
    initial_margin: float | None
    maintenance_margin: float | None
    available_funds: float | None
    excess_liquidity: float | None
    full_initial_margin: float | None
    full_maintenance_margin: float | None
    full_available_funds: float | None
    full_excess_liquidity: float | None
    look_ahead_initial_margin: float | None
    look_ahead_maintenance_margin: float | None
    look_ahead_available_funds: float | None
    look_ahead_excess_liquidity: float | None

    @property
    def effective_available_funds(self) -> float | None:
        """Prefer IB's consolidated account value when it is available."""
        return self.full_available_funds if self.full_available_funds is not None else self.available_funds

    @property
    def effective_excess_liquidity(self) -> float | None:
        """Prefer IB's consolidated maintenance-margin buffer when available."""
        return self.full_excess_liquidity if self.full_excess_liquidity is not None else self.excess_liquidity


@dataclass(frozen=True)
class MarginWhatIfRequest:
    """One proposed order to send to IB only as a margin preview."""

    contract: Contract
    action: str
    quantity: float
    order_type: str = "MKT"
    limit_price: float | None = None


@dataclass(frozen=True)
class MarginWhatIfResult:
    """IB's portfolio-level margin impact for a single proposed order."""

    requested_at: str
    account: str
    currency: str
    contract_label: str
    con_id: int
    action: str
    quantity: float
    order_type: str
    limit_price: float | None
    initial_margin_before: float | None
    initial_margin_change: float | None
    initial_margin_after: float | None
    maintenance_margin_before: float | None
    maintenance_margin_change: float | None
    maintenance_margin_after: float | None
    equity_with_loan_before: float | None
    equity_with_loan_change: float | None
    equity_with_loan_after: float | None
    available_funds_before: float | None
    estimated_available_funds_change: float | None
    estimated_available_funds_after: float | None
    excess_liquidity_before: float | None
    estimated_excess_liquidity_change: float | None
    estimated_excess_liquidity_after: float | None
    linear_max_quantity_estimate: int | None
    warning_text: str

    @property
    def initial_margin_released(self) -> float | None:
        """Positive only when the simulated order reduces initial margin."""
        if self.initial_margin_change is None:
            return None
        return max(-self.initial_margin_change, 0.0)

    @property
    def maintenance_margin_released(self) -> float | None:
        """Positive only when the simulated order reduces maintenance margin."""
        if self.maintenance_margin_change is None:
            return None
        return max(-self.maintenance_margin_change, 0.0)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["initial_margin_released"] = self.initial_margin_released
        data["maintenance_margin_released"] = self.maintenance_margin_released
        data["linear_max_quantity_note"] = (
            "Based on this one What-If result; it assumes the incremental impact scales linearly. "
            "Verify the final size with a separate What-If request before trading."
        )
        return data


@dataclass(frozen=True)
class MarginCapacityResult:
    """A requested-order decision plus a broker-verified size search."""

    requested: MarginWhatIfResult
    supported: bool | None
    reserve_funds: float
    binding_constraint: str
    available_headroom_after: float | None
    excess_headroom_after: float | None
    max_quantity: int | None
    max_quantity_is_search_cap: bool
    max_search_quantity: int
    probe_count: int
    capacity_note: str
    max_quantity_result: MarginWhatIfResult | None
    first_unsupported_result: MarginWhatIfResult | None

    def to_dict(self) -> dict[str, object]:
        return {
            "requested": self.requested.to_dict(),
            "supported": self.supported,
            "reserve_funds": self.reserve_funds,
            "binding_constraint": self.binding_constraint,
            "available_headroom_after": self.available_headroom_after,
            "excess_headroom_after": self.excess_headroom_after,
            "max_quantity": self.max_quantity,
            "max_quantity_is_search_cap": self.max_quantity_is_search_cap,
            "max_search_quantity": self.max_search_quantity,
            "probe_count": self.probe_count,
            "capacity_note": self.capacity_note,
            "max_quantity_result": self.max_quantity_result.to_dict() if self.max_quantity_result else None,
            "first_unsupported_result": (
                self.first_unsupported_result.to_dict() if self.first_unsupported_result else None
            ),
        }


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if match is None:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    # IB uses DBL_MAX-like sentinels for unavailable numeric order-state fields.
    return number if math.isfinite(number) and abs(number) < 1e100 else None


def _difference(before: float | None, after: float | None, reported: object) -> float | None:
    value = _number(reported)
    if value is not None:
        return value
    if before is not None and after is not None:
        return after - before
    return None


def _contract_label(contract: Contract) -> str:
    local_symbol = str(getattr(contract, "localSymbol", "") or "").strip()
    if local_symbol:
        return local_symbol
    symbol = str(getattr(contract, "symbol", "") or "").strip()
    expiry = str(getattr(contract, "lastTradeDateOrContractMonth", "") or "").strip()
    right = str(getattr(contract, "right", "") or "").strip().upper()
    strike = _number(getattr(contract, "strike", None))
    pieces = [symbol, expiry]
    if strike is not None:
        pieces.append(f"{strike:g}")
    if right:
        pieces.append(right)
    return " ".join(piece for piece in pieces if piece) or f"conId {getattr(contract, 'conId', 0)}"


def _value_by_tag(rows: list[Any]) -> tuple[dict[str, float], str]:
    values: dict[str, float] = {}
    currency = ""
    priorities: dict[str, int] = {}
    for row in rows:
        tag = str(getattr(row, "tag", "") or "")
        if tag not in MARGIN_ACCOUNT_TAGS:
            continue
        value = _number(getattr(row, "value", None))
        if value is None:
            continue
        row_currency = str(getattr(row, "currency", "") or "").strip()
        priority = 2 if row_currency.upper() == "BASE" else 1
        if priority >= priorities.get(tag, -1):
            values[tag] = value
            priorities[tag] = priority
        if not currency and row_currency:
            currency = row_currency
    return values, currency


def read_margin_account_snapshot(ib: IB, account: str) -> MarginAccountSnapshot:
    """Read the current account-level margin baseline from IB."""
    if not account.strip():
        raise MarginWhatIfError("IB account is required for a margin preview")
    rows = ib.accountSummary(account)
    values, currency = _value_by_tag(list(rows))
    return MarginAccountSnapshot(
        account=account,
        currency=currency,
        initial_margin=values.get("InitMarginReq"),
        maintenance_margin=values.get("MaintMarginReq"),
        available_funds=values.get("AvailableFunds"),
        excess_liquidity=values.get("ExcessLiquidity"),
        full_initial_margin=values.get("FullInitMarginReq"),
        full_maintenance_margin=values.get("FullMaintMarginReq"),
        full_available_funds=values.get("FullAvailableFunds"),
        full_excess_liquidity=values.get("FullExcessLiquidity"),
        look_ahead_initial_margin=values.get("LookAheadInitMarginReq"),
        look_ahead_maintenance_margin=values.get("LookAheadMaintMarginReq"),
        look_ahead_available_funds=values.get("LookAheadAvailableFunds"),
        look_ahead_excess_liquidity=values.get("LookAheadExcessLiquidity"),
    )


def build_margin_whatif_order(request: MarginWhatIfRequest, account: str):
    """Create a safe order object for use exclusively with ``whatIfOrder``."""
    action = request.action.strip().upper()
    if action not in {"BUY", "SELL"}:
        raise MarginWhatIfError("action must be BUY or SELL")
    quantity = float(request.quantity)
    if not math.isfinite(quantity) or quantity <= 0:
        raise MarginWhatIfError("quantity must be a positive number")
    if not account.strip():
        raise MarginWhatIfError("IB account is required for a margin preview")

    order_type = request.order_type.strip().upper()
    if order_type == "MKT":
        return MarketOrder(action, quantity, account=account, tif="DAY")
    if order_type == "LMT":
        limit_price = _number(request.limit_price)
        if limit_price is None or limit_price <= 0:
            raise MarginWhatIfError("limit_price must be positive for a LMT What-If order")
        return LimitOrder(action, quantity, limit_price, account=account, tif="DAY")
    raise MarginWhatIfError("order_type must be MKT or LMT")


def qualify_margin_contract(ib: IB, contract: Contract) -> Contract:
    """Qualify a copy of the proposed contract before it reaches IB credit checks."""
    candidate = copy(contract)
    qualified = ib.qualifyContracts(candidate)
    if not qualified:
        raise MarginWhatIfError("IB could not qualify the proposed contract")
    return qualified[0]


def _linear_max_quantity(
    quantity: float,
    available_funds_before: float | None,
    estimated_available_funds_change: float | None,
) -> int | None:
    if available_funds_before is None or estimated_available_funds_change is None:
        return None
    if available_funds_before <= 0 or estimated_available_funds_change >= 0:
        return None
    consumption_per_contract = -estimated_available_funds_change / quantity
    if consumption_per_contract <= 0 or not math.isfinite(consumption_per_contract):
        return None
    return max(math.floor(available_funds_before / consumption_per_contract), 0)


def margin_whatif_result(
    request: MarginWhatIfRequest,
    account_snapshot: MarginAccountSnapshot,
    contract: Contract,
    order_state: Any,
) -> MarginWhatIfResult:
    """Normalize an IB ``OrderState`` into an auditable margin-impact result."""
    initial_before = _number(getattr(order_state, "initMarginBefore", None))
    initial_after = _number(getattr(order_state, "initMarginAfter", None))
    initial_change = _difference(initial_before, initial_after, getattr(order_state, "initMarginChange", None))
    maintenance_before = _number(getattr(order_state, "maintMarginBefore", None))
    maintenance_after = _number(getattr(order_state, "maintMarginAfter", None))
    maintenance_change = _difference(maintenance_before, maintenance_after, getattr(order_state, "maintMarginChange", None))
    equity_before = _number(getattr(order_state, "equityWithLoanBefore", None))
    equity_after = _number(getattr(order_state, "equityWithLoanAfter", None))
    equity_change = _difference(equity_before, equity_after, getattr(order_state, "equityWithLoanChange", None))

    estimated_available_change = (
        equity_change - initial_change
        if equity_change is not None and initial_change is not None
        else None
    )
    estimated_excess_change = (
        equity_change - maintenance_change
        if equity_change is not None and maintenance_change is not None
        else None
    )
    available_before = account_snapshot.effective_available_funds
    excess_before = account_snapshot.effective_excess_liquidity
    available_after = (
        available_before + estimated_available_change
        if available_before is not None and estimated_available_change is not None
        else None
    )
    excess_after = (
        excess_before + estimated_excess_change
        if excess_before is not None and estimated_excess_change is not None
        else None
    )

    warning_text = str(getattr(order_state, "warningText", "") or "").strip()
    return MarginWhatIfResult(
        requested_at=datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        account=account_snapshot.account,
        currency=account_snapshot.currency,
        contract_label=_contract_label(contract),
        con_id=int(getattr(contract, "conId", 0) or 0),
        action=request.action.strip().upper(),
        quantity=float(request.quantity),
        order_type=request.order_type.strip().upper(),
        limit_price=_number(request.limit_price),
        initial_margin_before=initial_before,
        initial_margin_change=initial_change,
        initial_margin_after=initial_after,
        maintenance_margin_before=maintenance_before,
        maintenance_margin_change=maintenance_change,
        maintenance_margin_after=maintenance_after,
        equity_with_loan_before=equity_before,
        equity_with_loan_change=equity_change,
        equity_with_loan_after=equity_after,
        available_funds_before=available_before,
        estimated_available_funds_change=estimated_available_change,
        estimated_available_funds_after=available_after,
        excess_liquidity_before=excess_before,
        estimated_excess_liquidity_change=estimated_excess_change,
        estimated_excess_liquidity_after=excess_after,
        linear_max_quantity_estimate=_linear_max_quantity(
            float(request.quantity), available_before, estimated_available_change
        ),
        warning_text=warning_text,
    )


def run_margin_whatif(
    ib: IB,
    account: str,
    request: MarginWhatIfRequest,
    *,
    qualify_contract: bool = True,
) -> MarginWhatIfResult:
    """Run one IB portfolio margin preview without transmitting a live order."""
    account_snapshot = read_margin_account_snapshot(ib, account)
    contract = qualify_margin_contract(ib, request.contract) if qualify_contract else request.contract
    order = build_margin_whatif_order(request, account)
    order_state = ib.whatIfOrder(contract, order)
    return margin_whatif_result(request, account_snapshot, contract, order_state)


def margin_support_decision(
    result: MarginWhatIfResult,
    reserve_funds: float = 0.0,
) -> tuple[bool | None, str, float | None, float | None]:
    """Decide support from the two account buffers affected by IB margin."""
    reserve = float(reserve_funds)
    if not math.isfinite(reserve) or reserve < 0:
        raise MarginWhatIfError("reserve_funds must be a non-negative number")
    available_headroom = (
        result.estimated_available_funds_after - reserve
        if result.estimated_available_funds_after is not None
        else None
    )
    excess_headroom = (
        result.estimated_excess_liquidity_after - reserve
        if result.estimated_excess_liquidity_after is not None
        else None
    )
    constraints = [
        ("available_funds", available_headroom),
        ("excess_liquidity", excess_headroom),
    ]
    observed = [(name, value) for name, value in constraints if value is not None]
    if not observed:
        return None, "unavailable", available_headroom, excess_headroom
    binding, minimum_headroom = min(observed, key=lambda item: item[1])
    return minimum_headroom >= 0, binding, available_headroom, excess_headroom


def run_margin_whatif_capacity(
    ib: IB,
    account: str,
    request: MarginWhatIfRequest,
    *,
    reserve_funds: float = 0.0,
    calculate_capacity: bool = True,
    max_search_quantity: int = 10_000,
) -> MarginCapacityResult:
    """Preview a requested order and verify the largest supported integer size.

    Capacity probing uses IB's portfolio-level What-If result at every sampled
    quantity. It expands exponentially, then binary-searches and verifies the
    boundary. This is materially safer than multiplying a one-contract margin
    estimate, while still keeping the number of IB requests bounded.
    """
    reserve = float(reserve_funds)
    if not math.isfinite(reserve) or reserve < 0:
        raise MarginWhatIfError("reserve_funds must be a non-negative number")
    search_cap = int(max_search_quantity)
    if search_cap < 1 or search_cap > 1_000_000:
        raise MarginWhatIfError("max_search_quantity must be between 1 and 1000000")

    account_snapshot = read_margin_account_snapshot(ib, account)
    contract = qualify_margin_contract(ib, request.contract)
    probes: dict[int, MarginWhatIfResult] = {}
    decisions: dict[int, bool | None] = {}

    def probe(quantity: int) -> MarginWhatIfResult:
        quantity = int(quantity)
        if quantity not in probes:
            candidate_request = replace(request, contract=contract, quantity=float(quantity))
            order = build_margin_whatif_order(candidate_request, account)
            order_state = ib.whatIfOrder(contract, order)
            result = margin_whatif_result(candidate_request, account_snapshot, contract, order_state)
            probes[quantity] = result
            decisions[quantity] = margin_support_decision(result, reserve)[0]
        return probes[quantity]

    requested_quantity = float(request.quantity)
    if not requested_quantity.is_integer() or requested_quantity < 1:
        raise MarginWhatIfError("futures and options What-If quantity must be a positive integer")
    requested_int = int(requested_quantity)
    requested_result = probe(requested_int)
    supported, binding, available_headroom, excess_headroom = margin_support_decision(
        requested_result,
        reserve,
    )

    if not calculate_capacity or supported is None:
        note = (
            "Capacity search was not requested."
            if not calculate_capacity
            else "IB did not return enough margin fields to determine supported size."
        )
        return MarginCapacityResult(
            requested=requested_result,
            supported=supported,
            reserve_funds=reserve,
            binding_constraint=binding,
            available_headroom_after=available_headroom,
            excess_headroom_after=excess_headroom,
            max_quantity=None,
            max_quantity_is_search_cap=False,
            max_search_quantity=search_cap,
            probe_count=len(probes),
            capacity_note=note,
            max_quantity_result=None,
            first_unsupported_result=None,
        )

    one_result = probe(1)
    if decisions[1] is None:
        return MarginCapacityResult(
            requested=requested_result,
            supported=supported,
            reserve_funds=reserve,
            binding_constraint=binding,
            available_headroom_after=available_headroom,
            excess_headroom_after=excess_headroom,
            max_quantity=None,
            max_quantity_is_search_cap=False,
            max_search_quantity=search_cap,
            probe_count=len(probes),
            capacity_note="IB did not return enough fields for the one-contract capacity probe.",
            max_quantity_result=None,
            first_unsupported_result=None,
        )
    if decisions[1] is False:
        # A larger passing request would prove the feasible set is non-monotonic;
        # in that unusual case do not make a false maximum-size claim.
        non_monotonic = requested_int > 1 and supported is True
        return MarginCapacityResult(
            requested=requested_result,
            supported=supported,
            reserve_funds=reserve,
            binding_constraint=binding,
            available_headroom_after=available_headroom,
            excess_headroom_after=excess_headroom,
            max_quantity=None if non_monotonic else 0,
            max_quantity_is_search_cap=False,
            max_search_quantity=search_cap,
            probe_count=len(probes),
            capacity_note=(
                "Margin feasibility is non-monotonic; the requested size passed although one contract failed. "
                "No maximum is reported."
                if non_monotonic
                else "One contract does not preserve the requested account reserve."
            ),
            max_quantity_result=None,
            first_unsupported_result=one_result,
        )

    low = 1
    high: int | None = None
    while low < search_cap:
        candidate = min(low * 2, search_cap)
        probe(candidate)
        if decisions[candidate] is not True:
            high = candidate
            break
        low = candidate
        if low == search_cap:
            break

    if high is None:
        return MarginCapacityResult(
            requested=requested_result,
            supported=supported,
            reserve_funds=reserve,
            binding_constraint=binding,
            available_headroom_after=available_headroom,
            excess_headroom_after=excess_headroom,
            max_quantity=low,
            max_quantity_is_search_cap=True,
            max_search_quantity=search_cap,
            probe_count=len(probes),
            capacity_note=(
                f"IB verified support through the configured search cap of {search_cap}; "
                "this is a lower bound, not an unlimited-size claim."
            ),
            max_quantity_result=probes[low],
            first_unsupported_result=None,
        )

    # A missing decision is not a valid failing boundary for binary search.
    if decisions[high] is None:
        return MarginCapacityResult(
            requested=requested_result,
            supported=supported,
            reserve_funds=reserve,
            binding_constraint=binding,
            available_headroom_after=available_headroom,
            excess_headroom_after=excess_headroom,
            max_quantity=None,
            max_quantity_is_search_cap=False,
            max_search_quantity=search_cap,
            probe_count=len(probes),
            capacity_note="IB stopped returning enough margin fields while searching for the capacity boundary.",
            max_quantity_result=None,
            first_unsupported_result=probes[high],
        )

    while high - low > 1:
        middle = (low + high) // 2
        probe(middle)
        if decisions[middle] is None:
            return MarginCapacityResult(
                requested=requested_result,
                supported=supported,
                reserve_funds=reserve,
                binding_constraint=binding,
                available_headroom_after=available_headroom,
                excess_headroom_after=excess_headroom,
                max_quantity=None,
                max_quantity_is_search_cap=False,
                max_search_quantity=search_cap,
                probe_count=len(probes),
                capacity_note="IB returned incomplete fields near the capacity boundary.",
                max_quantity_result=None,
                first_unsupported_result=probes[middle],
            )
        if decisions[middle] is True:
            low = middle
        else:
            high = middle

    # Both sides of the boundary are actual IB What-If probes in the cache.
    return MarginCapacityResult(
        requested=requested_result,
        supported=supported,
        reserve_funds=reserve,
        binding_constraint=binding,
        available_headroom_after=available_headroom,
        excess_headroom_after=excess_headroom,
        max_quantity=low,
        max_quantity_is_search_cap=False,
        max_search_quantity=search_cap,
        probe_count=len(probes),
        capacity_note=(
            f"IB What-If verified {low} as supported and {high} as unsupported for the same order fields. "
            "Re-run What-If immediately before any future live order because portfolio and market inputs can change."
        ),
        max_quantity_result=probes[low],
        first_unsupported_result=probes[high],
    )
