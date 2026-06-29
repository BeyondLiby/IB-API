from __future__ import annotations

import argparse
import random
from pathlib import Path

import pandas as pd
from ib_async import IB, util
from ib_async.ib import StartupFetch

from target_treasury_account_monitor.carry_view import zf_option_carry_frame
from target_treasury_account_monitor.live_option_chain import fetch_live_zf_near_expiry_chain


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Live IB test for near-expiry ZF futures options.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=random.randint(3000, 9999))
    parser.add_argument("--market-data-type", type=int, default=3, help="1 live, 2 frozen, 3 delayed, 4 delayed frozen")
    parser.add_argument("--months", default="202609,202612")
    parser.add_argument("--max-dte", type=int, default=14)
    parser.add_argument("--max-expirations", type=int, default=8)
    parser.add_argument("--strikes-each-side", type=int, default=12)
    parser.add_argument("--strike-width", type=float, default=5.0)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    parser.add_argument("--expect-min-dte", type=int, default=0)
    parser.add_argument("--no-snapshot", action="store_true")
    parser.add_argument("--output", default="data/live_zf_near_expiry_snapshot.csv")
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
        result = fetch_live_zf_near_expiry_chain(
            ib,
            future_months=args.months,
            market_data_type=args.market_data_type,
            max_dte=args.max_dte,
            max_expirations=args.max_expirations,
            strikes_each_side=args.strikes_each_side,
            strike_width=args.strike_width,
            batch_size=args.batch_size,
            wait_max_seconds=args.wait_seconds,
            snapshot=not args.no_snapshot,
        )

        metadata = result["metadata"]
        print("\nmonths:", result["months"])
        print("today:", result["today"])
        print("raw candidates:", result["candidate_count"])
        print("qualified contracts:", result["qualified_count"])
        print("\nfuture prices")
        print(result["future_prices"].to_string(index=False))
        print_group("chain summary", result["chain_summary"], ["underlyingMonth", "firstSelectedExpiration", "lastSelectedExpiration"])
        print_group("qualified expirations", metadata, ["underlyingMonth", "expiration", "dte"])

        if metadata.empty:
            raise AssertionError("No qualified near-expiry ZF option contracts returned from IB.")
        min_dte = int(pd.to_numeric(metadata["dte"], errors="coerce").min())
        print("\nmin qualified DTE:", min_dte)
        if min_dte > args.expect_min_dte:
            raise AssertionError(
                f"Expected min DTE <= {args.expect_min_dte}, got {min_dte}. "
                "Check months, exchange, and IB security-definition permissions."
            )

        snapshot = result["snapshot"]
        if not snapshot.empty:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            snapshot.to_csv(output, index=False, encoding="utf-8-sig")
            print("\nsnapshot rows:", len(snapshot))
            print("snapshot saved:", output)

            carry = zf_option_carry_frame(
                result["monitor_frame"],
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
