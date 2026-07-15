from __future__ import annotations

from copy import copy
from dataclasses import asdict, dataclass
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
    look_ahead_initial_margin: float | None
    look_ahead_maintenance_margin: float | None
    look_ahead_available_funds: float | None
    look_ahead_excess_liquidity: float | None


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


def _number(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    text = str(value).replace(",", "").strip()
    if not text:
        return None
    match = re.search(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)", text)
    if match is None:
        return None
    try:
        number = float(match.group(0))
    except ValueError:
        return None
    return number if math.isfinite(number) else None


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
    available_after = (
        account_snapshot.available_funds + estimated_available_change
        if account_snapshot.available_funds is not None and estimated_available_change is not None
        else None
    )
    excess_after = (
        account_snapshot.excess_liquidity + estimated_excess_change
        if account_snapshot.excess_liquidity is not None and estimated_excess_change is not None
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
        available_funds_before=account_snapshot.available_funds,
        estimated_available_funds_change=estimated_available_change,
        estimated_available_funds_after=available_after,
        excess_liquidity_before=account_snapshot.excess_liquidity,
        estimated_excess_liquidity_change=estimated_excess_change,
        estimated_excess_liquidity_after=excess_after,
        linear_max_quantity_estimate=_linear_max_quantity(
            float(request.quantity), account_snapshot.available_funds, estimated_available_change
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
