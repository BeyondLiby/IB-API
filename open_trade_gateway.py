from __future__ import annotations

import argparse
import os
from pathlib import Path

from target_treasury_monitor_clean.settings import DEFAULT_IB_ACCOUNT, IBSettings
from target_treasury_monitor_clean.trade_gateway import (
    IBTradingBroker,
    TradeGateway,
    TradeGatewayConfig,
)
from target_treasury_monitor_clean.trade_gateway_server import serve_trade_gateway


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Start the loopback-only IB dashboard trade gateway. Paper mode is the default.",
    )
    parser.add_argument("--mode", choices=("paper", "live"), default="paper")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8767, type=int)
    parser.add_argument("--dashboard-origin", action="append", default=[])
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", default=4002, type=int)
    parser.add_argument("--client-id", default=7321, type=int)
    parser.add_argument("--account", default=os.environ.get("IB_ACCOUNT", DEFAULT_IB_ACCOUNT))
    parser.add_argument("--max-order-quantity", default=10, type=int)
    parser.add_argument("--max-preview-quantity", default=100, type=int)
    parser.add_argument("--minimum-reserve-funds", default=0.0, type=float)
    parser.add_argument("--preview-ttl-seconds", default=45, type=int)
    parser.add_argument("--arm-ttl-seconds", default=600, type=int)
    parser.add_argument(
        "--audit-path",
        default=Path("data/planner/trading_audit.jsonl"),
        type=Path,
    )
    parser.add_argument(
        "--enable-order-transmission",
        action="store_true",
        help="Required in both paper and live modes; without it the service refuses to start.",
    )
    parser.add_argument(
        "--live-account-confirm",
        default="",
        help="Live mode requires this to exactly equal --account.",
    )
    args = parser.parse_args()

    if not args.enable_order_transmission:
        raise SystemExit("Refusing to start: pass --enable-order-transmission explicitly")
    if args.mode == "live" and args.live_account_confirm != args.account:
        raise SystemExit("Refusing live mode: --live-account-confirm must exactly equal --account")
    if args.mode == "live" and args.ib_port == 4002:
        raise SystemExit("Refusing live mode on the default paper Gateway port 4002")

    config = TradeGatewayConfig(
        mode=args.mode,
        account=args.account,
        max_order_quantity=args.max_order_quantity,
        max_preview_quantity=args.max_preview_quantity,
        minimum_reserve_funds=args.minimum_reserve_funds,
        preview_ttl_seconds=args.preview_ttl_seconds,
        arm_ttl_seconds=args.arm_ttl_seconds,
        audit_path=args.audit_path,
    )
    settings = IBSettings(
        host=args.ib_host,
        port=args.ib_port,
        client_id=args.client_id,
        account=args.account,
        market_data_type=1,
        readonly=False,
    )
    gateway = TradeGateway(config, IBTradingBroker(settings))
    origins = set(args.dashboard_origin) or None
    serve_trade_gateway(
        gateway,
        host=args.host,
        port=args.port,
        allowed_origins=origins,
    )


if __name__ == "__main__":
    main()
