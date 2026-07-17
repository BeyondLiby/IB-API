from __future__ import annotations

from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Any
from urllib.parse import urlparse

from .trade_gateway import TradeGateway, TradeGatewayError


MAX_TRADE_API_BODY_BYTES = 32 * 1024
DEFAULT_ALLOWED_ORIGINS = {
    "http://127.0.0.1:8766",
    "http://localhost:8766",
}


def trade_gateway_handler(
    gateway: TradeGateway,
    *,
    allowed_origins: set[str] | None = None,
):
    origins = set(allowed_origins or DEFAULT_ALLOWED_ORIGINS)

    class TradeGatewayHandler(BaseHTTPRequestHandler):
        server_version = "IBDashboardTradeGateway/1"

        def log_message(self, format: str, *args: object) -> None:
            # Keep activation/session secrets out of logs. Routes and response
            # codes remain useful; request bodies are never logged.
            super().log_message(format, *args)

        def _origin(self) -> str:
            return str(self.headers.get("Origin") or "").strip()

        def _origin_allowed(self) -> bool:
            origin = self._origin()
            return not origin or origin in origins

        def _cors_headers(self) -> None:
            origin = self._origin()
            if origin in origins:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Credentials", "false")

        def send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self._cors_headers()
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _reject_bad_origin(self) -> bool:
            if self._origin_allowed():
                return False
            self.send_json(403, {"ok": False, "error": "请求来源不在本地 Dashboard 白名单"})
            return True

        def _session_token(self) -> str:
            return str(self.headers.get("X-Trade-Session") or "").strip()

        def _read_json(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError as exc:
                raise TradeGatewayError("Content-Length 无效") from exc
            if length <= 0:
                raise TradeGatewayError("必须提供 JSON 请求体")
            if length > MAX_TRADE_API_BODY_BYTES:
                raise TradeGatewayError("请求体过大", status_code=413)
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise TradeGatewayError("请求体必须是 UTF-8 JSON") from exc
            if not isinstance(payload, dict):
                raise TradeGatewayError("请求体必须是 JSON 对象")
            return payload

        def _handle_error(self, exc: Exception) -> None:
            if isinstance(exc, TradeGatewayError):
                self.send_json(exc.status_code, {"ok": False, "error": str(exc)})
                return
            self.send_json(500, {"ok": False, "error": f"交易服务内部错误：{type(exc).__name__}"})

        def do_OPTIONS(self) -> None:  # noqa: N802
            if self._reject_bad_origin():
                return
            self.send_response(204)
            self._cors_headers()
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Trade-Session")
            self.send_header("Access-Control-Max-Age", "600")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            if self._reject_bad_origin():
                return
            path = urlparse(self.path).path
            try:
                if path == "/api/trading/status":
                    self.send_json(200, gateway.status(self._session_token()))
                    return
                if path == "/api/trading/orders":
                    self.send_json(200, gateway.open_orders(self._session_token()))
                    return
                self.send_json(404, {"ok": False, "error": "not found"})
            except Exception as exc:
                self._handle_error(exc)

        def do_POST(self) -> None:  # noqa: N802
            if self._reject_bad_origin():
                return
            path = urlparse(self.path).path
            try:
                payload = self._read_json()
                if path == "/api/trading/arm":
                    self.send_json(200, gateway.arm(str(payload.get("activationCode") or "")))
                    return
                if path == "/api/trading/disarm":
                    self.send_json(200, gateway.disarm(self._session_token()))
                    return
                if path == "/api/trading/preview":
                    self.send_json(200, gateway.preview(self._session_token(), payload))
                    return
                if path == "/api/trading/submit":
                    self.send_json(200, gateway.submit(
                        self._session_token(),
                        preview_id=str(payload.get("previewId") or ""),
                        fingerprint=str(payload.get("fingerprint") or ""),
                        confirmation_phrase=str(payload.get("confirmationPhrase") or ""),
                    ))
                    return
                if path == "/api/trading/cancel":
                    self.send_json(200, gateway.cancel(
                        self._session_token(),
                        order_id=payload.get("orderId"),
                        confirmation_phrase=str(payload.get("confirmationPhrase") or ""),
                    ))
                    return
                self.send_json(404, {"ok": False, "error": "not found"})
            except Exception as exc:
                self._handle_error(exc)

    return partial(TradeGatewayHandler)


def serve_trade_gateway(
    gateway: TradeGateway,
    *,
    host: str = "127.0.0.1",
    port: int = 8767,
    allowed_origins: set[str] | None = None,
) -> None:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("trade gateway may only bind to a loopback host")
    try:
        server = ThreadingHTTPServer(
            (host, int(port)),
            trade_gateway_handler(gateway, allowed_origins=allowed_origins),
        )
    except OSError as exc:
        raise SystemExit(f"cannot bind trade gateway {host}:{port} ({exc})") from exc
    bound_host, bound_port = server.server_address[:2]
    print(f"trade gateway: http://{bound_host}:{bound_port}", flush=True)
    print(f"mode: {gateway.config.mode.upper()}", flush=True)
    print(f"activation code: {gateway.activation_code}", flush=True)
    print("The code is valid only for this process. Press Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\ntrade gateway stopped", flush=True)
    finally:
        server.server_close()
