from __future__ import annotations

from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from target_treasury_monitor_clean.trade_gateway import (
    OrderIntent,
    TradeGateway,
    TradeGatewayConfig,
    TradeGatewayError,
    build_dashboard_order,
)
from target_treasury_monitor_clean.trade_gateway_server import trade_gateway_handler


def safe_margin(label: str = "ZFU6") -> dict[str, object]:
    return {
        "supported": True,
        "reserve_funds": 1000.0,
        "binding_constraint": "available_funds",
        "available_headroom_after": 5000.0,
        "excess_headroom_after": 6000.0,
        "max_quantity": 12,
        "requested": {
            "contract_label": label,
            "warning_text": "",
            "initial_margin_before": 10000.0,
            "initial_margin_change": 1500.0,
            "initial_margin_after": 11500.0,
            "maintenance_margin_before": 9000.0,
            "maintenance_margin_change": 1200.0,
            "maintenance_margin_after": 10200.0,
            "available_funds_before": 20000.0,
            "estimated_available_funds_after": 18500.0,
            "excess_liquidity_before": 21000.0,
            "estimated_excess_liquidity_after": 19800.0,
        },
    }


class FakeBroker:
    def __init__(self) -> None:
        self.preview_calls: list[OrderIntent] = []
        self.submit_calls: list[tuple[OrderIntent, str, str]] = []
        self.cancel_calls: list[int] = []
        self.margin = safe_margin()
        self.raise_on_submit = False

    def preview(self, intent: OrderIntent) -> dict[str, object]:
        self.preview_calls.append(intent)
        return deepcopy(self.margin)

    def submit(self, intent: OrderIntent, *, preview_id: str, fingerprint: str) -> dict[str, object]:
        self.submit_calls.append((intent, preview_id, fingerprint))
        if self.raise_on_submit:
            raise TimeoutError("ambiguous broker response")
        return {
            "orderId": 17,
            "permId": 901,
            "status": "Submitted",
            "action": intent.action,
            "quantity": intent.quantity,
            "limitPrice": float(intent.limit_price),
            "orderRef": f"IBDASH:{preview_id[:12]}:{fingerprint[:12]}",
        }

    def open_orders(self) -> list[dict[str, object]]:
        return [{"orderId": 17, "status": "Submitted", "orderRef": "IBDASH:test"}]

    def cancel(self, order_id: int) -> dict[str, object]:
        self.cancel_calls.append(order_id)
        return {"orderId": order_id, "status": "Cancelled"}


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.value = now

    def __call__(self) -> float:
        return self.value


class TradeGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.clock = FakeClock()
        self.broker = FakeBroker()
        self.config = TradeGatewayConfig(
            mode="paper",
            account="DU123456",
            max_order_quantity=5,
            max_preview_quantity=50,
            minimum_reserve_funds=1000,
            preview_ttl_seconds=30,
            arm_ttl_seconds=120,
            audit_path=Path(self.tmp.name) / "audit.jsonl",
        )
        self.gateway = TradeGateway(
            self.config,
            self.broker,
            activation_code="paper-secret",
            now=self.clock,
        )
        self.payload = {
            "conId": 842590380,
            "secType": "FUT",
            "exchange": "CBOT",
            "action": "BUY",
            "quantity": 2,
            "orderType": "LMT",
            "limitPrice": 106.5,
            "tif": "DAY",
            "reserveFunds": 500,
            "calculateCapacity": True,
            "maxPreviewQuantity": 25,
        }

    def arm(self) -> str:
        return str(self.gateway.arm("paper-secret")["sessionToken"])

    def test_live_config_requires_positive_reserve(self) -> None:
        with self.assertRaises(ValueError):
            TradeGatewayConfig(mode="live", account="U123", minimum_reserve_funds=0)

    def test_live_preview_requires_real_time_broker_quote(self) -> None:
        live_gateway = TradeGateway(
            TradeGatewayConfig(
                mode="live",
                account="U123",
                minimum_reserve_funds=1000,
                audit_path=Path(self.tmp.name) / "live-audit.jsonl",
            ),
            self.broker,
            activation_code="live-secret",
            now=self.clock,
        )
        token = str(live_gateway.arm("live-secret")["sessionToken"])
        blocked = live_gateway.preview(token, self.payload)
        self.assertFalse(blocked["submittable"])
        self.assertIn("实时行情", blocked["blockedReason"])

        self.broker.margin["broker_quote"] = {
            "marketDataType": 1,
            "bid": 106.484375,
            "ask": 106.5,
            "last": 106.4921875,
        }
        allowed = live_gateway.preview(token, self.payload)
        self.assertTrue(allowed["submittable"])

    def test_intent_rejects_market_orders_and_oversized_quantity(self) -> None:
        market = {**self.payload, "orderType": "MKT"}
        oversized = {**self.payload, "quantity": 6}

        with self.assertRaises(TradeGatewayError):
            OrderIntent.from_payload(market, self.config)
        with self.assertRaises(TradeGatewayError):
            OrderIntent.from_payload(oversized, self.config)

    def test_live_order_builder_is_transmitted_limit_with_auditable_reference(self) -> None:
        intent = OrderIntent.from_payload(self.payload, self.config)
        order = build_dashboard_order(
            intent,
            account=self.config.account,
            preview_id="preview123456789",
            fingerprint=intent.fingerprint,
        )

        self.assertEqual(order.orderType, "LMT")
        self.assertEqual(order.tif, "DAY")
        self.assertFalse(order.whatIf)
        self.assertTrue(order.transmit)
        self.assertTrue(order.orderRef.startswith("IBDASH:preview12345:"))

    def test_preview_requires_unlock_and_uses_server_minimum_reserve(self) -> None:
        with self.assertRaises(TradeGatewayError) as locked:
            self.gateway.preview("", self.payload)
        self.assertEqual(locked.exception.status_code, 401)

        token = self.arm()
        preview = self.gateway.preview(token, self.payload)

        self.assertTrue(preview["submittable"])
        self.assertEqual(preview["fingerprint"], self.broker.preview_calls[0].fingerprint)
        self.assertEqual(self.broker.preview_calls[0].reserve_funds, 1000)
        self.assertIn("确认 PAPER BUY 2 ZFU6 @106.5", preview["confirmationPhrase"])

    def test_submit_rechecks_exact_intent_then_disarms_and_never_duplicates(self) -> None:
        token = self.arm()
        preview = self.gateway.preview(token, self.payload)

        with self.assertRaises(TradeGatewayError):
            self.gateway.submit(
                token,
                preview_id=str(preview["previewId"]),
                fingerprint="tampered",
                confirmation_phrase=str(preview["confirmationPhrase"]),
            )
        self.assertEqual(self.broker.submit_calls, [])

        result = self.gateway.submit(
            token,
            preview_id=str(preview["previewId"]),
            fingerprint=str(preview["fingerprint"]),
            confirmation_phrase=str(preview["confirmationPhrase"]),
        )

        self.assertEqual(result["order"]["status"], "Submitted")
        self.assertEqual(len(self.broker.preview_calls), 2)
        self.assertEqual(len(self.broker.submit_calls), 1)
        self.assertFalse(self.gateway.status(token)["armed"])
        with self.assertRaises(TradeGatewayError):
            self.gateway.submit(
                token,
                preview_id=str(preview["previewId"]),
                fingerprint=str(preview["fingerprint"]),
                confirmation_phrase=str(preview["confirmationPhrase"]),
            )
        self.assertEqual(len(self.broker.submit_calls), 1)

    def test_expired_preview_cannot_submit(self) -> None:
        token = self.arm()
        preview = self.gateway.preview(token, self.payload)
        self.clock.value += 31

        with self.assertRaises(TradeGatewayError) as expired:
            self.gateway.submit(
                token,
                preview_id=str(preview["previewId"]),
                fingerprint=str(preview["fingerprint"]),
                confirmation_phrase=str(preview["confirmationPhrase"]),
            )

        self.assertEqual(expired.exception.status_code, 409)
        self.assertEqual(self.broker.submit_calls, [])

    def test_ambiguous_submit_is_not_retried_and_locks_gateway(self) -> None:
        token = self.arm()
        preview = self.gateway.preview(token, self.payload)
        self.broker.raise_on_submit = True

        with self.assertRaises(TradeGatewayError) as ambiguous:
            self.gateway.submit(
                token,
                preview_id=str(preview["previewId"]),
                fingerprint=str(preview["fingerprint"]),
                confirmation_phrase=str(preview["confirmationPhrase"]),
            )

        self.assertEqual(ambiguous.exception.status_code, 502)
        self.assertEqual(len(self.broker.submit_calls), 1)
        self.assertFalse(self.gateway.status(token)["armed"])

    def test_expiry_during_final_margin_recheck_blocks_order(self) -> None:
        token = self.arm()
        preview = self.gateway.preview(token, self.payload)
        original_preview = self.broker.preview

        def advancing_preview(intent: OrderIntent):
            result = original_preview(intent)
            self.clock.value += 31
            return result

        self.broker.preview = advancing_preview  # type: ignore[method-assign]

        with self.assertRaises(TradeGatewayError):
            self.gateway.submit(
                token,
                preview_id=str(preview["previewId"]),
                fingerprint=str(preview["fingerprint"]),
                confirmation_phrase=str(preview["confirmationPhrase"]),
            )

        self.assertEqual(self.broker.submit_calls, [])

    def test_ib_warning_blocks_submit(self) -> None:
        self.broker.margin["requested"]["warning_text"] = "order precaution"
        token = self.arm()
        preview = self.gateway.preview(token, self.payload)

        self.assertFalse(preview["submittable"])
        with self.assertRaises(TradeGatewayError):
            self.gateway.submit(
                token,
                preview_id=str(preview["previewId"]),
                fingerprint=str(preview["fingerprint"]),
                confirmation_phrase=str(preview["confirmationPhrase"]),
            )
        self.assertEqual(self.broker.submit_calls, [])

    def test_cancel_requires_exact_phrase_and_is_one_shot(self) -> None:
        token = self.arm()
        with self.assertRaises(TradeGatewayError):
            self.gateway.cancel(token, order_id=17, confirmation_phrase="取消")
        result = self.gateway.cancel(token, order_id=17, confirmation_phrase="取消订单 17")

        self.assertEqual(result["order"]["status"], "Cancelled")
        self.assertEqual(self.broker.cancel_calls, [17])
        self.assertFalse(self.gateway.status(token)["armed"])

    def test_audit_log_does_not_contain_activation_or_session_secret(self) -> None:
        token = self.arm()
        self.gateway.preview(token, self.payload)
        audit = self.config.audit_path.read_text(encoding="utf-8")

        self.assertNotIn("paper-secret", audit)
        self.assertNotIn(token, audit)
        self.assertIn("preview_created", audit)


class TradeGatewayHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.broker = FakeBroker()
        self.gateway = TradeGateway(
            TradeGatewayConfig(
                mode="paper",
                account="DU123456",
                minimum_reserve_funds=1000,
                audit_path=Path(self.tmp.name) / "audit.jsonl",
            ),
            self.broker,
            activation_code="http-secret",
        )
        handler = trade_gateway_handler(
            self.gateway,
            allowed_origins={"http://127.0.0.1:8766"},
        )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        origin: str = "http://127.0.0.1:8766",
        token: str = "",
    ):
        headers = {"Origin": origin}
        data = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload).encode("utf-8")
        if token:
            headers["X-Trade-Session"] = token
        return urlopen(Request(self.base + path, data=data, method=method, headers=headers), timeout=5)

    def test_bad_browser_origin_is_rejected(self) -> None:
        with self.assertRaises(HTTPError) as raised:
            self.request("/api/trading/status", origin="https://evil.example")
        self.assertEqual(raised.exception.code, 403)

    def test_http_arm_preview_and_status_flow(self) -> None:
        with self.request(
            "/api/trading/arm",
            method="POST",
            payload={"activationCode": "http-secret"},
        ) as response:
            armed = json.load(response)
            self.assertEqual(response.headers["Access-Control-Allow-Origin"], "http://127.0.0.1:8766")
        token = armed["sessionToken"]

        with self.request(
            "/api/trading/preview",
            method="POST",
            token=token,
            payload={
                "conId": 1,
                "secType": "FOP",
                "exchange": "CBOT",
                "action": "SELL",
                "quantity": 1,
                "orderType": "LMT",
                "limitPrice": 0.03125,
                "reserveFunds": 1000,
            },
        ) as response:
            preview = json.load(response)

        self.assertTrue(preview["submittable"])
        self.assertEqual(preview["intent"]["action"], "SELL")


if __name__ == "__main__":
    unittest.main()
