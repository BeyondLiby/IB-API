from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd
from ib_async import IB, util
from ib_async.ib import StartupFetch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from target_treasury_account_monitor.config import DEFAULT_CLIENT_ID, DEFAULT_HOST, DEFAULT_MARKET_DATA_LABEL, DEFAULT_PORT, DEFAULT_REFRESH_SECONDS, MARKET_DATA_TYPES, MonitorSettings
from target_treasury_account_monitor.greeks import greek_totals
from target_treasury_account_monitor.ib_client import subscribe_quotes_for_positions
from target_treasury_account_monitor.snapshot import build_snapshot


def parse_args() -> argparse.Namespace:
    """解析持仓快照 smoke test 参数。"""
    parser = argparse.ArgumentParser(description="Fetch target treasury positions with prices, values, and option names.")
    parser.add_argument("--account", required=True, help="IB account ID, for example U1234567.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID + 30)
    parser.add_argument("--market-data", choices=list(MARKET_DATA_TYPES), default=DEFAULT_MARKET_DATA_LABEL)
    parser.add_argument("--wait", type=float, default=6.0, help="Seconds to wait after market-data subscription.")
    parser.add_argument("--csv", default="", help="Optional CSV output path.")
    return parser.parse_args()


def make_settings(args: argparse.Namespace) -> MonitorSettings:
    """从命令行参数生成监控配置。"""
    return MonitorSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        account=args.account,
        market_data_type=MARKET_DATA_TYPES[args.market_data],
        quote_wait_seconds=args.wait,
        refresh_seconds=DEFAULT_REFRESH_SECONDS,
        auto_refresh=False,
        auto_reconnect=False,
        reconnect_backoff_seconds=10,
        wechat_webhook_url="",
        wechat_push_enabled=False,
        wechat_min_interval_seconds=300,
        order_preview_enabled=False,
        readonly=True,
    )


def main() -> None:
    """连接 IB，打印一份美债持仓快照，然后断开。"""
    args = parse_args()
    settings = make_settings(args)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 240)

    util.startLoop()
    ib = IB()
    ib.connect(
        settings.host,
        settings.port,
        clientId=settings.client_id,
        readonly=True,
        timeout=10,
        fetchFields=StartupFetch.POSITIONS | StartupFetch.ACCOUNT_UPDATES | StartupFetch.SUB_ACCOUNT_UPDATES,
    )
    try:
        snapshot = build_snapshot(ib, settings, lambda positions: subscribe_quotes_for_positions(ib, positions, settings))
        frame = snapshot.frame
        summary = snapshot.summary

        print(f"treasury positions: {len(snapshot.positions)} / all positions: {len(snapshot.all_positions)}")
        print("\naccount summary:")
        print(summary.to_string(index=False))
        print("\nposition snapshot:")
        useful_cols = [
            "optionName",
            "localSymbol",
            "position",
            "bid",
            "ask",
            "mid",
            "last",
            "modelOptionPrice",
            "price",
            "priceSource",
            "marketValue",
            "valueSource",
            "unrealizedPnL",
            "estimatedUnrealizedPnL",
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
            "missingData",
            "conId",
        ]
        print(frame[[col for col in useful_cols if col in frame.columns]].to_string(index=False))
        print("\ngreek totals:")
        print(greek_totals(frame).to_string(index=False))
        if args.csv:
            frame.to_csv(args.csv, index=False, encoding="utf-8-sig")
            print(f"\nwrote {args.csv}")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
