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

from target_treasury_account_monitor.config import DEFAULT_CLIENT_ID, DEFAULT_HOST, DEFAULT_PORT, MARKET_DATA_TYPES, MonitorSettings
from target_treasury_account_monitor.frames import positions_to_frame
from target_treasury_account_monitor.greeks import greek_totals
from target_treasury_account_monitor.ib_client import account_summary_frame, fetch_target_positions, portfolio_items_by_key, refresh_account_portfolio, subscribe_quotes_for_positions


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the position snapshot smoke test."""
    parser = argparse.ArgumentParser(description="Fetch target treasury positions with prices, values, and option names.")
    parser.add_argument("--account", required=True, help="IB account ID, for example U1234567.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID + 30)
    parser.add_argument("--market-data", choices=list(MARKET_DATA_TYPES), default="Live")
    parser.add_argument("--wait", type=float, default=6.0, help="Seconds to wait after market-data subscription.")
    parser.add_argument("--csv", default="", help="Optional CSV output path.")
    return parser.parse_args()


def make_settings(args: argparse.Namespace) -> MonitorSettings:
    """Build monitor settings from CLI arguments."""
    return MonitorSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        account=args.account,
        market_data_type=MARKET_DATA_TYPES[args.market_data],
        quote_wait_seconds=args.wait,
        refresh_seconds=5,
        auto_refresh=False,
        auto_reconnect=False,
        reconnect_backoff_seconds=10,
        wechat_webhook_url="",
        wechat_push_enabled=False,
        wechat_min_interval_seconds=300,
    )


def main() -> None:
    """Connect to IB, print a full treasury position snapshot, and disconnect."""
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
        positions, all_positions = fetch_target_positions(ib, settings.account)
        tickers = subscribe_quotes_for_positions(ib, positions, settings)
        refresh_account_portfolio(ib, settings.account)
        ib.sleep(settings.quote_wait_seconds)
        frame = positions_to_frame(positions, tickers, portfolio_items_by_key(ib, settings.account))
        summary = account_summary_frame(ib, settings.account)

        print(f"treasury positions: {len(positions)} / all positions: {len(all_positions)}")
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
