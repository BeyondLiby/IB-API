from __future__ import annotations

import argparse
import random
from pathlib import Path

import pandas as pd
from ib_async.ib import StartupFetch

from .account_dashboard import fetch_account_dashboard
from .chain_batch import refresh_static_chain
from .chain_realtime import LiveChainMonitor
from .ib_session import ib_connection
from .quality import evaluate_option_chain_data, print_option_chain_quality_report
from .settings import (
    AccountDashboardSettings,
    IBSettings,
    LiveChainSettings,
    StaticChainSettings,
    market_data_type_from_label,
)


def _add_ib_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=random.randint(3000, 9999))
    parser.add_argument("--account", default="")
    parser.add_argument("--market-data-type", default="delayed", help="live, frozen, delayed, delayed_frozen, or 1/2/3/4")


def _ib_settings(args: argparse.Namespace) -> IBSettings:
    return IBSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        account=getattr(args, "account", ""),
        market_data_type=market_data_type_from_label(args.market_data_type),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Clean treasury account monitor workflows.")
    sub = parser.add_subparsers(dest="command", required=True)

    dashboard = sub.add_parser("dashboard-snapshot", help="Fetch one account dashboard snapshot.")
    _add_ib_args(dashboard)
    dashboard.add_argument("--quote-wait-seconds", type=float, default=6.0)
    dashboard.add_argument("--infer-spreads", action="store_true")
    dashboard.add_argument("--output-dir", default="data/clean_account_snapshot")

    batch = sub.add_parser("batch-chain", help="Batch-refresh a static option chain.")
    _add_ib_args(batch)
    batch.add_argument("--root", default="ZF")
    batch.add_argument("--months", default="202609,202612")
    batch.add_argument("--min-expiration", default="")
    batch.add_argument("--max-expiration", default="")
    batch.add_argument("--no-market-data", action="store_true")
    batch.add_argument("--batch-size", type=int, default=150)
    batch.add_argument("--wait-seconds", type=float, default=10.0)
    batch.add_argument("--stable-seconds", type=float, default=2.0)
    batch.add_argument("--request-interval", type=float, default=0.025)
    batch.add_argument("--inter-batch-pause-seconds", type=float, default=0.5)
    batch.add_argument("--empty-batch-retries", type=int, default=1)
    batch.add_argument("--empty-batch-retry-pause-seconds", type=float, default=5.0)
    batch.add_argument("--no-market-data-filter", action="store_true", help="Subscribe the full contract universe.")
    batch.add_argument("--near-dte-days", type=int, default=7)
    batch.add_argument("--near-strike-width", type=float, default=1.0)
    batch.add_argument("--far-strike-width", type=float, default=3.0)
    batch.add_argument("--output-dir", default="data")
    batch.add_argument("--no-contract-cache", action="store_true", help="Do not reuse saved qualified contracts.")
    batch.add_argument("--rebuild-contract-cache", action="store_true", help="Force IB contract discovery and qualify.")

    live = sub.add_parser("live-chain", help="Run persistent near-expiry chain subscriptions.")
    _add_ib_args(live)
    live.add_argument("--root", default="ZF")
    live.add_argument("--months", default="202609,202612")
    live.add_argument("--max-dte", type=int, default=14)
    live.add_argument("--max-expirations", type=int, default=8)
    live.add_argument("--strikes-each-side", type=int, default=12)
    live.add_argument("--strike-width", type=float, default=5.0)
    live.add_argument("--poll-seconds", type=float, default=1.0)
    live.add_argument("--iterations", type=int, default=0, help="0 means run until interrupted.")
    live.add_argument("--output", default="data/live_zf_chain_latest.csv")
    live.add_argument("--flow-db", default="data/zf_option_flow.sqlite")

    quality = sub.add_parser("quality-report", help="Evaluate a saved option-chain CSV.")
    quality.add_argument("csv_path")
    quality.add_argument("--reference-price", type=float, default=None)
    quality.add_argument("--output-prefix", default="")

    return parser


def command_dashboard(args: argparse.Namespace) -> None:
    ib_settings = _ib_settings(args)
    if not ib_settings.account:
        raise SystemExit("--account is required for dashboard-snapshot")
    dashboard_settings = AccountDashboardSettings(
        quote_wait_seconds=args.quote_wait_seconds,
        infer_spreads=args.infer_spreads,
    )
    fetch_fields = StartupFetch.POSITIONS | StartupFetch.ACCOUNT_UPDATES | StartupFetch.SUB_ACCOUNT_UPDATES
    with ib_connection(ib_settings, fetch_fields=fetch_fields) as ib:
        snapshot = fetch_account_dashboard(ib, ib_settings, dashboard_settings)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        snapshot.position_frame.to_csv(output_dir / "treasury_positions.csv", index=False, encoding="utf-8-sig")
        snapshot.account_positions.to_csv(output_dir / "all_positions.csv", index=False, encoding="utf-8-sig")
        snapshot.account_summary.to_csv(output_dir / "account_summary.csv", index=False, encoding="utf-8-sig")
        snapshot.greek_summary.to_csv(output_dir / "greek_summary.csv", index=False, encoding="utf-8-sig")
        print(f"treasury positions: {len(snapshot.position_frame)}")
        print(f"all positions: {len(snapshot.account_positions)}")
        print(f"visible accounts: {', '.join(snapshot.visible_accounts)}")
        print(f"saved: {output_dir}")


def command_batch_chain(args: argparse.Namespace) -> None:
    ib_settings = _ib_settings(args)
    settings = StaticChainSettings(
        root=args.root,
        future_months=args.months,
        min_expiration=args.min_expiration.strip() or None,
        max_expiration=args.max_expiration.strip() or None,
        batch_size=args.batch_size,
        wait_max_seconds=args.wait_seconds,
        wait_stable_seconds=args.stable_seconds,
        request_interval=args.request_interval,
        inter_batch_pause_seconds=args.inter_batch_pause_seconds,
        empty_batch_retries=args.empty_batch_retries,
        empty_batch_retry_pause_seconds=args.empty_batch_retry_pause_seconds,
        request_market_data=not args.no_market_data,
        output_dir=Path(args.output_dir),
        use_contract_cache=not args.no_contract_cache,
        force_rebuild_contract_cache=args.rebuild_contract_cache,
        filter_market_data_by_moneyness=not args.no_market_data_filter,
        near_dte_days=args.near_dte_days,
        near_strike_width=args.near_strike_width,
        far_strike_width=args.far_strike_width,
    )
    with ib_connection(ib_settings) as ib:
        result = refresh_static_chain(ib, settings)
        print(f"contracts: {result.raw.get('contract_count', 0)}")
        print(f"snapshot rows: {result.raw.get('snapshot_count', 0)}")
        print(f"selected contracts: {result.raw.get('selected_contract_count', '')}")
        print(f"snapshot scope: {result.raw.get('snapshot_scope', '')}")
        print(f"universe source: {result.raw.get('universe_source', '')}")
        print(f"contract cache: {result.raw.get('contract_cache_path', '')}")
        for name, path in result.saved_paths.items():
            print(f"{name}: {path}")


def command_live_chain(args: argparse.Namespace) -> None:
    ib_settings = _ib_settings(args)
    settings = LiveChainSettings(
        root=args.root,
        future_months=args.months,
        max_dte=args.max_dte,
        max_expirations=args.max_expirations,
        strikes_each_side=args.strikes_each_side,
        strike_width=args.strike_width,
        poll_seconds=args.poll_seconds,
        output_path=Path(args.output) if args.output else None,
        flow_db_path=Path(args.flow_db) if args.flow_db else None,
    )
    with ib_connection(ib_settings) as ib:
        with LiveChainMonitor(ib, settings) as monitor:
            monitor.run_forever(max_iterations=args.iterations or None)


def command_quality_report(args: argparse.Namespace) -> None:
    frame = pd.read_csv(args.csv_path)
    report = evaluate_option_chain_data(frame, reference_price=args.reference_price)
    print_option_chain_quality_report(report)
    if args.output_prefix:
        prefix = Path(args.output_prefix)
        prefix.parent.mkdir(parents=True, exist_ok=True)
        for name in ["coverage", "quality_labels", "by_expiration", "by_dte", "by_distance"]:
            table = report.get(name, pd.DataFrame())
            if isinstance(table, pd.DataFrame) and not table.empty:
                table.to_csv(prefix.with_name(prefix.name + f"_{name}.csv"), index=False, encoding="utf-8-sig")


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "dashboard-snapshot":
        command_dashboard(args)
    elif args.command == "batch-chain":
        command_batch_chain(args)
    elif args.command == "live-chain":
        command_live_chain(args)
    elif args.command == "quality-report":
        command_quality_report(args)


if __name__ == "__main__":
    main()
