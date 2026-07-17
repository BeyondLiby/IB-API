from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Protocol
import uuid

from ib_async import Contract, IB, LimitOrder
from ib_async.ib import StartupFetch

from .ib_client_lock import acquire_ib_client_lock
from .ib_session import ib_connection
from .margin_whatif import MarginWhatIfRequest, run_margin_whatif_capacity
from .settings import IBSettings


TRADE_ORDER_REF_PREFIX = "IBDASH:"
ALLOWED_SECURITY_TYPES = {"FUT", "FOP"}
ALLOWED_EXCHANGES = {"CBOT"}
TERMINAL_ORDER_STATUSES = {"Cancelled", "ApiCancelled", "Filled", "Inactive"}


class TradeGatewayError(RuntimeError):
    """A safe error that may be returned to the local trading dashboard."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = int(status_code)


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _decimal(value: object, field: str) -> Decimal:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TradeGatewayError(f"{field} 必须是有效数字") from exc
    if not number.is_finite():
        raise TradeGatewayError(f"{field} 必须是有限数字")
    return number


def _integer(value: object, field: str) -> int:
    number = _decimal(value, field)
    if number != number.to_integral_value():
        raise TradeGatewayError(f"{field} 必须是整数")
    return int(number)


def _constant_equal(left: object, right: object) -> bool:
    return hmac.compare_digest(str(left).encode("utf-8"), str(right).encode("utf-8"))


@dataclass(frozen=True)
class TradeGatewayConfig:
    mode: str
    account: str
    max_order_quantity: int = 10
    max_preview_quantity: int = 100
    minimum_reserve_funds: float = 0.0
    preview_ttl_seconds: int = 45
    arm_ttl_seconds: int = 600
    audit_path: Path = Path("data/planner/trading_audit.jsonl")

    def __post_init__(self) -> None:
        mode = self.mode.strip().lower()
        if mode not in {"paper", "live"}:
            raise ValueError("mode must be paper or live")
        if not self.account.strip():
            raise ValueError("account is required")
        if not 1 <= int(self.max_order_quantity) <= 1000:
            raise ValueError("max_order_quantity must be between 1 and 1000")
        if not int(self.max_order_quantity) <= int(self.max_preview_quantity) <= 10_000:
            raise ValueError("max_preview_quantity must be at least max_order_quantity and at most 10000")
        if not math.isfinite(float(self.minimum_reserve_funds)) or float(self.minimum_reserve_funds) < 0:
            raise ValueError("minimum_reserve_funds must be non-negative")
        if mode == "live" and float(self.minimum_reserve_funds) <= 0:
            raise ValueError("live mode requires a positive minimum_reserve_funds")
        if not 15 <= int(self.preview_ttl_seconds) <= 300:
            raise ValueError("preview_ttl_seconds must be between 15 and 300")
        if not 60 <= int(self.arm_ttl_seconds) <= 3600:
            raise ValueError("arm_ttl_seconds must be between 60 and 3600")


@dataclass(frozen=True)
class OrderIntent:
    account: str
    mode: str
    con_id: int
    sec_type: str
    exchange: str
    action: str
    quantity: int
    order_type: str
    limit_price: str
    tif: str
    reserve_funds: float
    calculate_capacity: bool
    max_preview_quantity: int

    @classmethod
    def from_payload(cls, payload: object, config: TradeGatewayConfig) -> "OrderIntent":
        if not isinstance(payload, dict):
            raise TradeGatewayError("订单请求必须是 JSON 对象")
        con_id = _integer(payload.get("conId"), "conId")
        if con_id <= 0:
            raise TradeGatewayError("conId 必须是正整数")
        sec_type = str(payload.get("secType") or "").strip().upper()
        if sec_type not in ALLOWED_SECURITY_TYPES:
            raise TradeGatewayError("第一版只允许 FUT 和 FOP")
        exchange = str(payload.get("exchange") or "").strip().upper()
        if exchange not in ALLOWED_EXCHANGES:
            raise TradeGatewayError("第一版只允许 CBOT 合约")
        action = str(payload.get("action") or "").strip().upper()
        if action not in {"BUY", "SELL"}:
            raise TradeGatewayError("方向必须是 BUY 或 SELL")
        quantity = _integer(payload.get("quantity"), "quantity")
        if not 1 <= quantity <= int(config.max_order_quantity):
            raise TradeGatewayError(f"实盘数量必须在 1 到 {config.max_order_quantity} 之间")
        order_type = str(payload.get("orderType") or "LMT").strip().upper()
        if order_type != "LMT":
            raise TradeGatewayError("第一版只允许 LMT 限价单；MKT 已硬禁用")
        limit_price = _decimal(payload.get("limitPrice"), "limitPrice")
        if limit_price <= 0 or limit_price > Decimal("1000000"):
            raise TradeGatewayError("limitPrice 必须大于 0 且小于 1000000")
        tif = str(payload.get("tif") or "DAY").strip().upper()
        if tif != "DAY":
            raise TradeGatewayError("第一版只允许 DAY 有效期")
        reserve = float(_decimal(payload.get("reserveFunds", 0), "reserveFunds"))
        if reserve < 0 or reserve > 1_000_000_000:
            raise TradeGatewayError("reserveFunds 超出允许范围")
        reserve = max(reserve, float(config.minimum_reserve_funds))
        calculate_capacity = payload.get("calculateCapacity", True)
        if not isinstance(calculate_capacity, bool):
            raise TradeGatewayError("calculateCapacity 必须是布尔值")
        preview_cap = _integer(
            payload.get("maxPreviewQuantity", config.max_preview_quantity),
            "maxPreviewQuantity",
        )
        preview_cap = max(preview_cap, quantity)
        if not 1 <= preview_cap <= int(config.max_preview_quantity):
            raise TradeGatewayError(
                f"maxPreviewQuantity 必须在 1 到 {config.max_preview_quantity} 之间"
            )
        return cls(
            account=config.account,
            mode=config.mode.strip().lower(),
            con_id=con_id,
            sec_type=sec_type,
            exchange=exchange,
            action=action,
            quantity=quantity,
            order_type="LMT",
            limit_price=_decimal_text(limit_price),
            tif="DAY",
            reserve_funds=reserve,
            calculate_capacity=calculate_capacity,
            max_preview_quantity=preview_cap,
        )

    def canonical_payload(self) -> dict[str, object]:
        return {
            "version": 1,
            "account": self.account,
            "mode": self.mode,
            "conId": self.con_id,
            "secType": self.sec_type,
            "exchange": self.exchange,
            "action": self.action,
            "quantity": self.quantity,
            "orderType": self.order_type,
            "limitPrice": self.limit_price,
            "tif": self.tif,
        }

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps(
            self.canonical_payload(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class TradingBroker(Protocol):
    def preview(self, intent: OrderIntent) -> dict[str, Any]: ...

    def submit(self, intent: OrderIntent, *, preview_id: str, fingerprint: str) -> dict[str, Any]: ...

    def open_orders(self) -> list[dict[str, Any]]: ...

    def cancel(self, order_id: int) -> dict[str, Any]: ...


def build_dashboard_order(
    intent: OrderIntent,
    *,
    account: str,
    preview_id: str,
    fingerprint: str,
):
    """Build the only live order shape allowed by the first trading release."""
    if not preview_id or not fingerprint:
        raise TradeGatewayError("preview-id 和订单指纹不能为空")
    order = LimitOrder(
        intent.action,
        intent.quantity,
        float(Decimal(intent.limit_price)),
        account=account,
        tif="DAY",
    )
    order.whatIf = False
    order.transmit = True
    order.orderRef = f"{TRADE_ORDER_REF_PREFIX}{preview_id[:12]}:{fingerprint[:12]}"
    return order


@dataclass
class PreviewRecord:
    preview_id: str
    intent: OrderIntent
    fingerprint: str
    contract_label: str
    confirmation_phrase: str
    created_at: float
    expires_at: float
    margin: dict[str, Any]
    state: str = "previewed"
    result: dict[str, Any] | None = None


class TradeGateway:
    """One-shot, explicit-confirmation state machine in front of IB orders."""

    def __init__(
        self,
        config: TradeGatewayConfig,
        broker: TradingBroker,
        *,
        activation_code: str | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.broker = broker
        self.activation_code = activation_code or secrets.token_urlsafe(18)
        self._now = now
        self._lock = threading.RLock()
        self._session_token = ""
        self._armed_until = 0.0
        self._previews: dict[str, PreviewRecord] = {}
        self._audit("service_started", mode=config.mode, maxOrderQuantity=config.max_order_quantity)

    def _audit(self, event: str, **data: object) -> None:
        payload = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "event": event,
            "mode": self.config.mode,
            **data,
        }
        path = self.config.audit_path
        path.parent.mkdir(parents=True, exist_ok=True)
        line = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, line)
        finally:
            os.close(descriptor)

    def _session_is_valid(self, token: str) -> bool:
        return bool(
            self._session_token
            and token
            and _constant_equal(token, self._session_token)
            and self._now() < self._armed_until
        )

    def _require_session(self, token: str) -> None:
        if not self._session_is_valid(token):
            raise TradeGatewayError("交易会话未解锁或已经过期", status_code=401)

    def status(self, token: str = "") -> dict[str, object]:
        with self._lock:
            valid = self._session_is_valid(token)
            return {
                "ok": True,
                "online": True,
                "mode": self.config.mode,
                "armed": valid,
                "armedUntil": self._armed_until if valid else None,
                "maxOrderQuantity": self.config.max_order_quantity,
                "maxPreviewQuantity": self.config.max_preview_quantity,
                "minimumReserveFunds": self.config.minimum_reserve_funds,
                "previewTtlSeconds": self.config.preview_ttl_seconds,
                "armTtlSeconds": self.config.arm_ttl_seconds,
                "allowedSecTypes": sorted(ALLOWED_SECURITY_TYPES),
                "allowedOrderTypes": ["LMT"],
                "allowedTif": ["DAY"],
            }

    def arm(self, activation_code: str) -> dict[str, object]:
        with self._lock:
            if not activation_code or not _constant_equal(activation_code, self.activation_code):
                self._audit("arm_rejected")
                raise TradeGatewayError("交易解锁码错误", status_code=401)
            self._session_token = secrets.token_urlsafe(24)
            self._armed_until = self._now() + int(self.config.arm_ttl_seconds)
            self._previews.clear()
            self._audit("armed", armedUntil=self._armed_until)
            return {
                **self.status(self._session_token),
                "sessionToken": self._session_token,
            }

    def disarm(self, token: str) -> dict[str, object]:
        with self._lock:
            self._require_session(token)
            self._disarm("manual")
            return self.status()

    def _disarm(self, reason: str) -> None:
        self._audit("disarmed", reason=reason)
        self._session_token = ""
        self._armed_until = 0.0
        self._previews.clear()

    def _margin_is_safe(self, margin: dict[str, Any]) -> tuple[bool, str]:
        if margin.get("supported") is not True:
            return False, "IB 保证金预检未通过或无法判定"
        requested = margin.get("requested") if isinstance(margin.get("requested"), dict) else {}
        warning = str(requested.get("warning_text") or "").strip()
        if warning:
            return False, f"IB 返回订单警告：{warning}"
        if self.config.mode == "live":
            quote = margin.get("broker_quote") if isinstance(margin.get("broker_quote"), dict) else {}
            if int(quote.get("marketDataType") or 0) != 1:
                return False, "Live 模式要求所选合约具备 IB 实时行情；延迟/冻结/缺失行情已阻止下单"
            if not any(quote.get(name) is not None for name in ("bid", "ask", "last")):
                return False, "Live 模式未取得所选合约的有效实时 bid/ask/last"
        return True, ""

    def preview(self, token: str, payload: object) -> dict[str, object]:
        with self._lock:
            self._require_session(token)
            intent = OrderIntent.from_payload(payload, self.config)
            margin = self.broker.preview(intent)
            safe, reason = self._margin_is_safe(margin)
            contract_label = str(
                (margin.get("requested") or {}).get("contract_label")
                if isinstance(margin.get("requested"), dict)
                else ""
            ).strip() or f"conId {intent.con_id}"
            preview_id = uuid.uuid4().hex
            mode_label = "LIVE" if self.config.mode == "live" else "PAPER"
            confirmation = (
                f"确认 {mode_label} {intent.action} {intent.quantity} "
                f"{contract_label} @{intent.limit_price}"
            )
            created = self._now()
            record = PreviewRecord(
                preview_id=preview_id,
                intent=intent,
                fingerprint=intent.fingerprint,
                contract_label=contract_label,
                confirmation_phrase=confirmation,
                created_at=created,
                expires_at=created + int(self.config.preview_ttl_seconds),
                margin=margin,
            )
            self._previews[preview_id] = record
            self._audit(
                "preview_created",
                previewId=preview_id,
                fingerprint=intent.fingerprint,
                conId=intent.con_id,
                action=intent.action,
                quantity=intent.quantity,
                limitPrice=intent.limit_price,
                supported=safe,
                blockedReason=reason,
            )
            return {
                "previewId": preview_id,
                "fingerprint": intent.fingerprint,
                "fingerprintShort": intent.fingerprint[:12],
                "createdAt": created,
                "expiresAt": record.expires_at,
                "contractLabel": contract_label,
                "confirmationPhrase": confirmation,
                "intent": intent.canonical_payload(),
                "margin": margin,
                "submittable": safe,
                "blockedReason": reason,
            }

    def submit(
        self,
        token: str,
        *,
        preview_id: str,
        fingerprint: str,
        confirmation_phrase: str,
    ) -> dict[str, object]:
        with self._lock:
            self._require_session(token)
            record = self._previews.get(str(preview_id))
            if record is None:
                raise TradeGatewayError("找不到该预览；请重新测算", status_code=404)
            if record.state != "previewed":
                raise TradeGatewayError(
                    f"该预览已进入 {record.state} 状态，禁止重复发送",
                    status_code=409,
                )
            if self._now() >= record.expires_at:
                record.state = "expired"
                raise TradeGatewayError("订单预览已过期；请重新测算", status_code=409)
            if not fingerprint or not _constant_equal(fingerprint, record.fingerprint):
                raise TradeGatewayError("订单指纹不一致；请重新测算", status_code=409)
            if not confirmation_phrase or not _constant_equal(
                confirmation_phrase,
                record.confirmation_phrase,
            ):
                raise TradeGatewayError("二次确认词不一致", status_code=409)
            safe, reason = self._margin_is_safe(record.margin)
            if not safe:
                raise TradeGatewayError(reason, status_code=409)

            # Re-run the exact same broker What-If immediately before sending.
            # The record is consumed before placeOrder, so an ambiguous network
            # failure can never be retried into a duplicate order.
            fresh_margin = self.broker.preview(record.intent)
            safe, reason = self._margin_is_safe(fresh_margin)
            if not safe:
                record.state = "blocked"
                self._audit("submit_blocked_by_recheck", previewId=record.preview_id, reason=reason)
                raise TradeGatewayError(f"最终保证金复核失败：{reason}", status_code=409)
            if self._now() >= record.expires_at or not self._session_is_valid(token):
                record.state = "expired"
                self._audit("submit_blocked_after_recheck", previewId=record.preview_id, reason="expired")
                raise TradeGatewayError("最终复核期间预览或交易会话已过期；请重新开始", status_code=409)

            record.state = "submitting"
            self._audit(
                "submit_attempt",
                previewId=record.preview_id,
                fingerprint=record.fingerprint,
            )
            try:
                result = self.broker.submit(
                    record.intent,
                    preview_id=record.preview_id,
                    fingerprint=record.fingerprint,
                )
            except Exception as exc:
                record.state = "submission_unknown"
                self._audit(
                    "submit_unknown",
                    previewId=record.preview_id,
                    fingerprint=record.fingerprint,
                    error=f"{type(exc).__name__}: {exc}",
                )
                self._disarm("submit_unknown")
                raise TradeGatewayError(
                    "订单发送结果不确定，系统已锁定且不会自动重试；请检查 IB 订单状态",
                    status_code=502,
                ) from exc

            record.state = "submitted"
            record.result = result
            self._audit(
                "submit_result",
                previewId=record.preview_id,
                fingerprint=record.fingerprint,
                orderId=result.get("orderId"),
                permId=result.get("permId"),
                status=result.get("status"),
            )
            response = {
                "ok": True,
                "previewId": record.preview_id,
                "fingerprint": record.fingerprint,
                "finalMargin": fresh_margin,
                "order": result,
                "disarmed": True,
            }
            self._disarm("one_shot_submit")
            return response

    def open_orders(self, token: str) -> dict[str, object]:
        with self._lock:
            self._require_session(token)
            return {"ok": True, "orders": self.broker.open_orders()}

    def cancel(
        self,
        token: str,
        *,
        order_id: object,
        confirmation_phrase: str,
    ) -> dict[str, object]:
        with self._lock:
            self._require_session(token)
            normalized_id = _integer(order_id, "orderId")
            expected = f"取消订单 {normalized_id}"
            if not confirmation_phrase or not _constant_equal(confirmation_phrase, expected):
                raise TradeGatewayError(f"请输入：{expected}", status_code=409)
            self._audit("cancel_attempt", orderId=normalized_id)
            try:
                result = self.broker.cancel(normalized_id)
            finally:
                self._disarm("one_shot_cancel")
            self._audit("cancel_result", orderId=normalized_id, status=result.get("status"))
            return {"ok": True, "order": result, "disarmed": True}


class IBTradingBroker:
    """Small IB adapter. It has no HTTP concerns and never auto-retries orders."""

    def __init__(self, settings: IBSettings) -> None:
        if settings.readonly:
            raise ValueError("IBTradingBroker requires readonly=False")
        self.settings = settings

    @staticmethod
    def _ensure_event_loop() -> None:
        try:
            asyncio.get_event_loop()
        except RuntimeError:
            asyncio.set_event_loop(asyncio.new_event_loop())

    def _contract(self, intent: OrderIntent) -> Contract:
        return Contract(
            conId=intent.con_id,
            secType=intent.sec_type,
            exchange=intent.exchange,
            currency="USD",
        )

    def _connection(self, purpose: str, fetch_fields: StartupFetch):
        self._ensure_event_loop()
        return acquire_ib_client_lock(
            self.settings.host,
            self.settings.port,
            self.settings.client_id,
            purpose=purpose,
        ), ib_connection(self.settings, fetch_fields=fetch_fields)

    def _check_account(self, ib: IB) -> None:
        managed = {str(account) for account in ib.managedAccounts()}
        if self.settings.account not in managed:
            raise TradeGatewayError("交易服务连接的 IB 会话不包含配置账户", status_code=503)

    def preview(self, intent: OrderIntent) -> dict[str, Any]:
        lock, connection = self._connection(
            f"trade preview conId={intent.con_id}",
            StartupFetch.ACCOUNT_UPDATES | StartupFetch.SUB_ACCOUNT_UPDATES,
        )
        with lock:
            with connection as ib:
                self._check_account(ib)
                result = run_margin_whatif_capacity(
                    ib,
                    self.settings.account,
                    MarginWhatIfRequest(
                        contract=self._contract(intent),
                        action=intent.action,
                        quantity=float(intent.quantity),
                        order_type="LMT",
                        limit_price=float(Decimal(intent.limit_price)),
                    ),
                    reserve_funds=intent.reserve_funds,
                    calculate_capacity=intent.calculate_capacity,
                    max_search_quantity=intent.max_preview_quantity,
                )
                payload = result.to_dict()
                contracts = ib.qualifyContracts(self._contract(intent))
                payload["broker_quote"] = (
                    self._broker_quote(ib, contracts[0])
                    if len(contracts) == 1
                    else {
                        "marketDataType": None,
                        "bid": None,
                        "ask": None,
                        "last": None,
                        "sampledAt": datetime.now(timezone.utc).isoformat(),
                    }
                )
                return payload

    @staticmethod
    def _broker_quote(ib: IB, contract: Contract) -> dict[str, Any]:
        ticker = ib.reqMktData(
            contract,
            genericTickList="",
            snapshot=False,
            regulatorySnapshot=False,
        )
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                values = [getattr(ticker, name, None) for name in ("bid", "ask", "last")]
                if any(
                    isinstance(value, (int, float)) and math.isfinite(float(value)) and float(value) >= 0
                    for value in values
                ):
                    break
                ib.sleep(0.1)

            def finite(name: str) -> float | None:
                value = getattr(ticker, name, None)
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return None
                return number if math.isfinite(number) and number >= 0 else None

            return {
                "marketDataType": int(getattr(ticker, "marketDataType", 0) or 0) or None,
                "bid": finite("bid"),
                "ask": finite("ask"),
                "last": finite("last"),
                "close": finite("close"),
                "sampledAt": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            ib.cancelMktData(contract)

    @staticmethod
    def _order_payload(trade: Any) -> dict[str, Any]:
        order = trade.order
        status = trade.orderStatus
        return {
            "orderId": int(getattr(order, "orderId", 0) or 0),
            "permId": int(getattr(status, "permId", 0) or getattr(order, "permId", 0) or 0),
            "status": str(getattr(status, "status", "") or "PendingSubmit"),
            "action": str(getattr(order, "action", "") or ""),
            "quantity": float(getattr(order, "totalQuantity", 0) or 0),
            "orderType": str(getattr(order, "orderType", "") or ""),
            "limitPrice": float(getattr(order, "lmtPrice", 0) or 0),
            "filled": float(getattr(status, "filled", 0) or 0),
            "remaining": float(getattr(status, "remaining", 0) or 0),
            "avgFillPrice": float(getattr(status, "avgFillPrice", 0) or 0),
            "orderRef": str(getattr(order, "orderRef", "") or ""),
            "conId": int(getattr(trade.contract, "conId", 0) or 0),
            "localSymbol": str(getattr(trade.contract, "localSymbol", "") or ""),
            "secType": str(getattr(trade.contract, "secType", "") or ""),
        }

    def submit(self, intent: OrderIntent, *, preview_id: str, fingerprint: str) -> dict[str, Any]:
        lock, connection = self._connection(
            f"trade submit preview={preview_id}",
            StartupFetch.ORDERS_OPEN | StartupFetch.EXECUTIONS,
        )
        with lock:
            with connection as ib:
                self._check_account(ib)
                contracts = ib.qualifyContracts(self._contract(intent))
                if len(contracts) != 1 or int(getattr(contracts[0], "conId", 0) or 0) != intent.con_id:
                    raise TradeGatewayError("IB 无法唯一确认订单合约", status_code=409)
                order = build_dashboard_order(
                    intent,
                    account=self.settings.account,
                    preview_id=preview_id,
                    fingerprint=fingerprint,
                )
                trade = ib.placeOrder(contracts[0], order)
                deadline = time.monotonic() + 4.0
                while time.monotonic() < deadline:
                    status = str(getattr(trade.orderStatus, "status", "") or "")
                    if status and status != "PendingSubmit":
                        break
                    ib.sleep(0.1)
                return self._order_payload(trade)

    def open_orders(self) -> list[dict[str, Any]]:
        lock, connection = self._connection(
            "dashboard open orders",
            StartupFetch.ORDERS_OPEN,
        )
        with lock:
            with connection as ib:
                self._check_account(ib)
                ib.reqOpenOrders()
                return [
                    self._order_payload(trade)
                    for trade in ib.openTrades()
                    if str(getattr(trade.order, "account", "") or "") == self.settings.account
                    and str(getattr(trade.order, "orderRef", "") or "").startswith(TRADE_ORDER_REF_PREFIX)
                ]

    def cancel(self, order_id: int) -> dict[str, Any]:
        lock, connection = self._connection(
            f"dashboard cancel orderId={order_id}",
            StartupFetch.ORDERS_OPEN,
        )
        with lock:
            with connection as ib:
                self._check_account(ib)
                ib.reqOpenOrders()
                trade = next(
                    (
                        item
                        for item in ib.openTrades()
                        if int(getattr(item.order, "orderId", 0) or 0) == int(order_id)
                        and str(getattr(item.order, "account", "") or "") == self.settings.account
                        and str(getattr(item.order, "orderRef", "") or "").startswith(TRADE_ORDER_REF_PREFIX)
                    ),
                    None,
                )
                if trade is None:
                    raise TradeGatewayError("找不到可由本 Dashboard 撤销的活动订单", status_code=404)
                status = str(getattr(trade.orderStatus, "status", "") or "")
                if status in TERMINAL_ORDER_STATUSES:
                    raise TradeGatewayError(f"订单已经是终态：{status}", status_code=409)
                ib.cancelOrder(trade.order)
                deadline = time.monotonic() + 3.0
                while time.monotonic() < deadline:
                    status = str(getattr(trade.orderStatus, "status", "") or "")
                    if status in TERMINAL_ORDER_STATUSES:
                        break
                    ib.sleep(0.1)
                return self._order_payload(trade)
