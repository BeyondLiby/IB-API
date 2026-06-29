from __future__ import annotations

import argparse
import random

import pandas as pd
from ib_async import IB, util
from ib_async.ib import StartupFetch

from target_treasury_account_monitor.carry_view import zf_option_carry_frame
from target_treasury_account_monitor.static_option_chain import (
    fetch_static_fop_chain_snapshot,
    save_static_chain_result,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="One-shot static ZF FOP chain snapshot from IB.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=random.randint(3000, 9999))
    parser.add_argument("--market-data-type", type=int, default=3, help="1 live, 2 frozen, 3 delayed, 4 delayed frozen")
    parser.add_argument("--months", default="202609,202612")
    parser.add_argument("--min-expiration", default="", help="YYYYMMDD; defaults to today")
    parser.add_argument("--max-expiration", default="", help="YYYYMMDD")
    parser.add_argument("--qualify-batch-size", type=int, default=300)
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--wait-seconds", type=float, default=10.0)
    parser.add_argument("--stable-seconds", type=float, default=2.0)
    parser.add_argument("--request-interval", type=float, default=0.025)
    parser.add_argument("--no-market-data", action="store_true", help="Only fetch static contract definitions.")
    parser.add_argument("--output-dir", default="data")
    return parser.parse_args()


def print_group(title: str, frame: pd.DataFrame, group_cols: list[str]) -> None:
    print(f"\n{title}")
    if frame.empty:
        print("(empty)")
        return
    print(frame.groupby(group_cols, dropna=False).size().reset_index(name="count").to_string(index=False))


def main() -> None:
    args = parse_args()
    util.startLoop()
    ib = IB()
    print(f"connecting {args.host}:{args.port} clientId={args.client_id}")
    ib.connect(
        args.host,
        args.port,
        clientId=args.client_id,
        readonly=True,
        timeout=10,
        fetchFields=StartupFetch(0),
    )
    try:
        result = fetch_static_fop_chain_snapshot(
            ib,
            future_months=args.months,
            market_data_type=args.market_data_type,
            min_expiration=args.min_expiration.strip() or None,
            max_expiration=args.max_expiration.strip() or None,
            qualify_batch_size=args.qualify_batch_size,
            batch_size=args.batch_size,
            wait_max_seconds=args.wait_seconds,
            wait_stable_seconds=args.stable_seconds,
            request_interval=args.request_interval,
            request_market_data=not args.no_market_data,
        )
        paths = save_static_chain_result(result, args.output_dir)

        print("\nsource:", result["universe_source"])
        print("months:", result["months"])
        print("today:", result["today"])
        print("min expiration:", result["min_expiration"])
        print("contracts:", result["contract_count"])
        print("snapshot rows:", result["snapshot_count"])
        print("\nfuture prices")
        print(result["future_prices"].to_string(index=False))
        print_group("chain summary", result["chain_summary"], ["underlyingMonth", "firstExpiration", "lastExpiration"])
        print_group("qualified expirations", result["metadata"], ["underlyingMonth", "expiration", "dte"])

        print("\nsaved files")
        for name, path in paths.items():
            print(f"{name}: {path}")

        monitor_frame = result["monitor_frame"]
        if not monitor_frame.empty:
            carry = zf_option_carry_frame(
                monitor_frame,
                target_return=0.10,
                capital_base=100_000,
                require_complete_greeks=False,
            )
            print("\ncarry head")
            print(carry.head(30).to_string(index=False))
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
