from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import random
from pathlib import Path
import signal
import webbrowser

import pandas as pd
from ib_async import Contract, Future
from ib_async.ib import StartupFetch

from .account_dashboard import fetch_account_dashboard
from .carry_dashboard_sync import (
    discover_latest_carry_dashboard_inputs,
    sync_carry_dashboard_files,
    validate_carry_dashboard_files,
    write_carry_dashboard_files,
)
from .chain_batch import refresh_future_prices_sidecar, refresh_static_chain
from .chain_realtime import LiveChainMonitor
from .future_bars import fetch_future_bars, parse_contract_specs, save_future_bars
from .ib_client_lock import IbClientLockBusy, acquire_ib_client_lock
from .ib_session import ib_connection
from .inventory_planner_server import inventory_planner_handler
from .margin_whatif import MarginWhatIfRequest, run_margin_whatif
from .quality import evaluate_option_chain_data, print_option_chain_quality_report
from .settings import (
    AccountDashboardSettings,
    DEFAULT_IB_ACCOUNT,
    IBSettings,
    LiveChainSettings,
    StaticChainSettings,
    market_data_type_from_label,
)


DEFAULT_DASHBOARD_PRODUCTS = "ZF,ZN,ZC"
DEFAULT_SHARED_CHAIN_SPECS = "ZF=202609,202612;ZN=202609,202612"


def _add_ib_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=random.randint(3000, 9999))
    parser.add_argument("--account", default=os.environ.get("IB_ACCOUNT", DEFAULT_IB_ACCOUNT))
    parser.add_argument("--market-data-type", default="delayed", help="live, frozen, delayed, delayed_frozen, or 1/2/3/4")


def _ib_settings(args: argparse.Namespace) -> IBSettings:
    return IBSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        account=getattr(args, "account", ""),
        market_data_type=market_data_type_from_label(args.market_data_type),
    )


def _parse_chain_specs(value: str) -> dict[str, str]:
    specs: dict[str, str] = {}
    for part in str(value or "").split(";"):
        text = part.strip()
        if not text:
            continue
        sep = "=" if "=" in text else ":"
        if sep not in text:
            raise ValueError(f"Chain spec must be ROOT=YYYYMM,YYYYMM; got {text!r}")
        root, months = [item.strip() for item in text.split(sep, 1)]
        if not root or not months:
            raise ValueError(f"Chain spec must be ROOT=YYYYMM,YYYYMM; got {text!r}")
        specs[root.upper()] = months
    return specs


def _bar_specs_from_chain_specs(chain_specs: dict[str, str]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for root, months in chain_specs.items():
        first_month = next((part.strip() for part in months.split(",") if part.strip()), "")
        if first_month:
            out.append((root, first_month))
    return out


def _raise_if_carry_html_not_ready(report: dict[str, object]) -> None:
    readiness = report.get("readiness", {})
    if not isinstance(readiness, dict) or readiness.get("ready_for_full_zf_zn_view"):
        return
    missing_chain = ", ".join(str(item) for item in readiness.get("missing_full_chain", []) or [])
    missing_bars = ", ".join(str(item) for item in readiness.get("missing_bars", []) or [])
    details = []
    if missing_chain:
        details.append(f"missing full chain: {missing_chain}")
    if missing_bars:
        details.append(f"missing bars: {missing_bars}")
    raise SystemExit("carry HTML inputs are not ready" + (f" ({'; '.join(details)})" if details else ""))


def _format_carry_html_summary(report: dict[str, object]) -> str:
    readiness = report.get("readiness", {})
    product_status = report.get("product_status", {})
    ready = bool(
        isinstance(readiness, dict)
        and (readiness.get("ready_for_full_view") or readiness.get("ready_for_full_zf_zn_view"))
    )
    lines = [
        f"ready_for_full_view: {str(ready).lower()}",
        f"ready_for_full_zf_zn_view: {str(ready).lower()}",
    ]
    if isinstance(product_status, dict):
        for product in sorted(product_status):
            status = product_status[product]
            if not isinstance(status, dict):
                continue
            chain_view = status.get("chain_view", "missing")
            bars_state = "ready" if status.get("has_bars") else ("partial" if status.get("has_bars_rows") else "missing")
            chain_rows = status.get("chain", 0)
            bars_rows = status.get("bars", 0)
            chain_age = status.get("chain_age_hours")
            bars_age = status.get("bars_age_hours")
            chain_age_text = "" if chain_age is None else f", chain_age_h={float(chain_age):.1f}"
            bars_age_text = "" if bars_age is None else f", bars_age_h={float(bars_age):.1f}"
            lines.append(
                f"{product}: chain={chain_view} rows={chain_rows}{chain_age_text}; "
                f"bars={bars_state} rows={bars_rows}{bars_age_text}"
            )
    if isinstance(readiness, dict):
        missing_chain = readiness.get("missing_full_chain", []) or []
        missing_bars = readiness.get("missing_bars", []) or []
        if missing_chain:
            lines.append(f"missing_full_chain: {', '.join(str(item) for item in missing_chain)}")
        if missing_bars:
            lines.append(f"missing_bars: {', '.join(str(item) for item in missing_bars)}")
    return "\n".join(lines)


def _future_price_summary(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "<none>"
    items: list[str] = []
    for row in frame.head(6).to_dict("records"):
        month = str(row.get("month") or row.get("lastTradeDateOrContractMonth") or "").strip()
        local_symbol = str(row.get("localSymbol") or "").strip()
        price = pd.to_numeric(row.get("price"), errors="coerce")
        label = local_symbol or month or "future"
        if pd.notna(price):
            items.append(f"{label}={float(price):.3f}")
        else:
            items.append(f"{label}=n/a")
    suffix = "" if len(frame) <= 6 else f", +{len(frame) - 6} more"
    return ", ".join(items) + suffix


@contextmanager
def _time_limit(seconds: float):
    if seconds <= 0 or os.name == "nt":
        yield
        return

    def _raise_timeout(_signum, _frame):
        raise TimeoutError(f"operation timed out after {seconds:.1f}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, seconds)
    signal.signal(signal.SIGALRM, _raise_timeout)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])
        signal.signal(signal.SIGALRM, previous_handler)


def _read_csv_if_exists(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def _fallback_positions_path(args: argparse.Namespace) -> Path:
    html_path = Path(args.html_data_dir) / "carry_dashboard_positions.csv"
    return html_path if html_path.exists() else Path(args.working_dir) / "dashboard_treasury_positions.csv"


def _nonempty_cached_positions(args: argparse.Namespace) -> tuple[pd.DataFrame, Path | None]:
    candidates = [
        Path(args.html_data_dir) / "carry_dashboard_positions.csv",
        Path(args.working_dir) / "dashboard_treasury_positions.csv",
    ]
    for path in dict.fromkeys(candidates):
        try:
            frame = _read_csv_if_exists(path)
        except (OSError, pd.errors.EmptyDataError):
            continue
        if not frame.empty:
            return frame, path
    return pd.DataFrame(), None


def _position_con_ids_by_root(position_frame: pd.DataFrame) -> dict[str, tuple[int, ...]]:
    if position_frame.empty or "conId" not in position_frame.columns:
        return {}
    roots = position_frame.get("symbol", pd.Series("", index=position_frame.index)).astype(str).str.upper()
    sec_types = position_frame.get("secType", pd.Series("", index=position_frame.index)).astype(str).str.upper()
    positions = pd.to_numeric(position_frame.get("position", 0), errors="coerce").fillna(0)
    con_ids = pd.to_numeric(position_frame["conId"], errors="coerce")
    out: dict[str, tuple[int, ...]] = {}
    for root in sorted(set(roots)):
        if not root:
            continue
        mask = (roots == root) & (sec_types == "FOP") & (positions != 0) & con_ids.notna()
        values = sorted({int(value) for value in con_ids[mask].astype(int).tolist() if int(value) > 0})
        if values:
            out[root] = tuple(values)
    return out


def _merge_chain_rows(existing: pd.DataFrame, refreshed: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        return refreshed
    if refreshed.empty:
        return existing
    key_candidates = [
        ["conId"],
        ["symbol", "expiration", "strike", "right"],
        ["symbol", "expiry", "strike", "right"],
    ]
    key = next((cols for cols in key_candidates if all(col in existing.columns and col in refreshed.columns for col in cols)), [])
    if not key:
        return refreshed
    existing_copy = existing.copy()
    refreshed_copy = refreshed.copy()
    for frame in (existing_copy, refreshed_copy):
        for col in key:
            frame[col] = frame[col].astype(str)
    combined = pd.concat([existing_copy, refreshed_copy], ignore_index=True)
    return combined.drop_duplicates(subset=key, keep="last", ignore_index=True)


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

    bars = sub.add_parser("future-bars", help="Fetch futures OHLCV bars for the HTML K-line panel.")
    _add_ib_args(bars)
    bars.add_argument("--contracts", required=True, help="Comma-separated specs like ZF:202609,ZN:202609.")
    bars.add_argument("--bar-size", default="30 mins")
    bars.add_argument("--duration", default="1 M")
    bars.add_argument("--what-to-show", default="TRADES")
    bars.add_argument("--timeout", type=float, default=45.0)
    bars.add_argument("--cache-dir", default="data/planner/debug", help="Search recursively for *_future_prices.csv to avoid re-qualifying futures.")
    bars.add_argument("--keep-going", action="store_true", help="Continue with later contracts if one historical data request fails.")
    bars.add_argument("--prefer-local-symbol", action="store_true", help="Use standard futures localSymbol such as ZFU6/ZNU6 when no cached conId is available, instead of qualifying first.")
    bars.add_argument("--output", default="data/planner/carry_dashboard_bars.csv")

    smoke = sub.add_parser("ib-smoke", help="Diagnose IB connectivity and futures contract qualification without writing dashboard files.")
    _add_ib_args(smoke)
    smoke.add_argument("--contracts", default="ZF:202609,ZN:202609", help="Comma-separated futures specs to qualify.")
    smoke.add_argument("--timeout", type=float, default=10.0, help="Request timeout for time and qualify checks.")

    margin_whatif = sub.add_parser(
        "margin-whatif",
        help="Preview one proposed order's portfolio-level IB margin impact without placing an order.",
    )
    _add_ib_args(margin_whatif)
    margin_whatif.add_argument("--con-id", type=int, required=True, help="IB contract conId to preview.")
    margin_whatif.add_argument("--exchange", default="CBOT", help="Contract exchange used to qualify the conId.")
    margin_whatif.add_argument("--action", required=True, choices=["BUY", "SELL", "buy", "sell"])
    margin_whatif.add_argument("--quantity", type=float, required=True)
    margin_whatif.add_argument("--order-type", default="MKT", choices=["MKT", "LMT", "mkt", "lmt"])
    margin_whatif.add_argument("--limit-price", type=float, default=None, help="Required when --order-type LMT.")
    margin_whatif.add_argument("--skip-qualification", action="store_true", help="Use the supplied conId directly; normally leave this off.")

    quality = sub.add_parser("quality-report", help="Evaluate a saved option-chain CSV.")
    quality.add_argument("csv_path")
    quality.add_argument("--reference-price", type=float, default=None)
    quality.add_argument("--output-prefix", default="")

    sync = sub.add_parser("sync-carry-html", help="Publish generated CSVs for carry_risk_dashboard.html.")
    sync.add_argument("--positions", required=True, help="Generated treasury positions CSV, or comma-separated CSVs.")
    sync.add_argument("--chain", required=True, help="Generated option-chain monitor-frame CSV, or comma-separated CSVs.")
    sync.add_argument("--bars", default="", help="Optional futures OHLCV bars CSV, or comma-separated CSVs, for the K-line panel.")
    sync.add_argument("--output-dir", default="data", help="Directory served beside carry_risk_dashboard.html.")
    sync.add_argument("--expected-products", default=DEFAULT_DASHBOARD_PRODUCTS, help="Comma-separated roots expected in the dashboard readiness report.")
    sync.add_argument("--min-chain-rows", type=int, default=50, help="Minimum per-product rows required to treat an option chain as complete enough for the HTML board.")
    sync.add_argument("--min-bars-rows", type=int, default=100, help="Minimum per-product rows required to treat futures bars as present.")
    sync.add_argument("--max-chain-age-hours", type=float, default=24.0, help="Maximum age of the latest option-chain snapshot before it is treated as stale.")
    sync.add_argument("--max-bars-age-hours", type=float, default=72.0, help="Maximum age of the latest futures bar before bars are treated as stale.")
    sync.add_argument("--as-of", default="now", help="Timestamp for freshness checks; defaults to now.")
    sync.add_argument("--require-ready", action="store_true", help="Exit non-zero after publishing unless all products with positions have fresh full chains and bars.")
    sync.add_argument("--summary-only", action="store_true", help="After publishing, print a concise readiness summary instead of full JSON.")

    sync_latest = sub.add_parser("sync-latest-carry-html", help="Publish the latest notebook output CSVs for carry_risk_dashboard.html.")
    sync_latest.add_argument("--input-dir", default="data/planner/debug", help="Directory where refresh/debug workflows write dashboard_treasury_positions.csv and *_monitor_frame.csv.")
    sync_latest.add_argument("--output-dir", default="data", help="Directory served beside carry_risk_dashboard.html.")
    sync_latest.add_argument("--products", default=DEFAULT_DASHBOARD_PRODUCTS, help="Comma-separated products to discover, e.g. ZF,ZN,ZC.")
    sync_latest.add_argument("--positions", default="", help="Optional explicit positions CSV. Defaults to latest notebook position output.")
    sync_latest.add_argument("--chain", default="", help="Optional explicit chain CSV(s). Defaults to latest per-product *_monitor_frame.csv files.")
    sync_latest.add_argument("--bars", default="", help="Optional explicit bars CSV. Defaults to carry_dashboard_bars.csv when present.")
    sync_latest.add_argument("--expected-products", default="", help="Defaults to --products.")
    sync_latest.add_argument("--min-chain-rows", type=int, default=50, help="Minimum per-product rows required to treat an option chain as complete enough for the HTML board.")
    sync_latest.add_argument("--min-bars-rows", type=int, default=100, help="Minimum per-product rows required to treat futures bars as present.")
    sync_latest.add_argument("--max-chain-age-hours", type=float, default=24.0, help="Maximum age of the latest option-chain snapshot before it is treated as stale.")
    sync_latest.add_argument("--max-bars-age-hours", type=float, default=72.0, help="Maximum age of the latest futures bar before it is treated as stale.")
    sync_latest.add_argument("--as-of", default="now", help="Timestamp for freshness checks; defaults to now.")
    sync_latest.add_argument("--require-ready", action="store_true", help="Exit non-zero after publishing unless all products with positions have fresh full chains and bars.")
    sync_latest.add_argument("--summary-only", action="store_true", help="After publishing, print a concise readiness summary instead of full JSON.")

    validate = sub.add_parser("validate-carry-html", help="Summarize stable CSV inputs for carry_risk_dashboard.html.")
    validate.add_argument("--data-dir", default="data")
    validate.add_argument("--expected-products", default=DEFAULT_DASHBOARD_PRODUCTS, help="Comma-separated roots expected in the dashboard readiness report.")
    validate.add_argument("--min-chain-rows", type=int, default=50, help="Minimum per-product rows required to treat an option chain as complete enough for the HTML board.")
    validate.add_argument("--min-bars-rows", type=int, default=100, help="Minimum per-product rows required to treat futures bars as present.")
    validate.add_argument("--max-chain-age-hours", type=float, default=24.0, help="Maximum age of the latest option-chain snapshot before it is treated as stale.")
    validate.add_argument("--max-bars-age-hours", type=float, default=72.0, help="Maximum age of the latest futures bar before bars are treated as stale.")
    validate.add_argument("--as-of", default="now", help="Timestamp for freshness checks; defaults to now.")
    validate.add_argument("--require-ready", action="store_true", help="Exit non-zero unless all products with positions have fresh full chains and bars.")
    validate.add_argument("--summary-only", action="store_true", help="Print a concise readiness summary instead of full JSON.")

    serve = sub.add_parser("serve-carry-html", help="Serve carry_risk_dashboard.html and data/*.csv over local HTTP.")
    serve.add_argument("--directory", default=".", help="Project directory containing carry_risk_dashboard.html and data/.")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="Open the dashboard URL in the default browser.")

    serve_inventory = sub.add_parser("serve-inventory-planner", help="Serve sell_side_inventory_planner.html and data/*.csv over local HTTP.")
    serve_inventory.add_argument("--directory", default=".", help="Project directory containing sell_side_inventory_planner.html and data/.")
    serve_inventory.add_argument("--host", default="127.0.0.1")
    serve_inventory.add_argument("--port", type=int, default=8766)
    serve_inventory.add_argument("--open", action="store_true", help="Open the planner URL in the default browser.")

    refresh = sub.add_parser("refresh-carry-html", help="Refresh positions, ZF/ZN chains, optional ZC chain, optional bars, then publish HTML CSVs.")
    _add_ib_args(refresh)
    refresh.add_argument("--chain-specs", default=DEFAULT_SHARED_CHAIN_SPECS, help="Shared chain specs for ZF/ZN or other products with the same refresh parameters.")
    refresh.add_argument("--zc-chain-specs", default="", help="Optional separate ZC chain spec, e.g. ZC=202609,202612. Leave empty until ZC parameters are finalized.")
    refresh.add_argument("--positions-csv", default="", help="Reuse an existing treasury positions CSV instead of fetching account data from IB.")
    refresh.add_argument("--positions-timeout", type=float, default=30.0, help="Seconds to wait for the account snapshot before falling back to cached positions.")
    refresh.add_argument("--strict-positions", action="store_true", help="Fail instead of reusing cached positions when account refresh times out or errors.")
    refresh.add_argument("--allow-empty-positions", action="store_true", help="Publish an empty account snapshot instead of preserving a non-empty local positions cache.")
    refresh.add_argument("--min-expiration", default="")
    refresh.add_argument("--max-expiration", default="")
    refresh.add_argument("--quote-wait-seconds", type=float, default=6.0)
    refresh.add_argument("--infer-spreads", action="store_true")
    refresh.add_argument("--batch-size", type=int, default=150)
    refresh.add_argument("--wait-seconds", type=float, default=8.0)
    refresh.add_argument("--stable-seconds", type=float, default=1.5)
    refresh.add_argument("--request-interval", type=float, default=0.025)
    refresh.add_argument("--inter-batch-pause-seconds", type=float, default=1.0)
    refresh.add_argument("--empty-batch-retries", type=int, default=1)
    refresh.add_argument("--empty-batch-retry-pause-seconds", type=float, default=5.0)
    refresh.add_argument("--no-market-data-filter", action="store_true")
    refresh.add_argument("--near-dte-days", type=int, default=7)
    refresh.add_argument("--near-strike-width", type=float, default=1.0)
    refresh.add_argument("--far-strike-width", type=float, default=3.0)
    refresh.add_argument("--fast-refresh", action="store_true", help="Refresh only near/current-position contracts and preserve cached far-chain rows.")
    refresh.add_argument("--market-data-max-dte", type=int, default=0, help="Only request market data up to this DTE; 0 disables the cap.")
    refresh.add_argument("--future-price-wait-seconds", type=float, default=6.0, help="Seconds to wait when refreshing underlying futures prices for each root.")
    refresh.add_argument("--no-contract-cache", action="store_true")
    refresh.add_argument("--rebuild-contract-cache", action="store_true")
    refresh.add_argument("--strict-chain", action="store_true", help="Fail if any root chain cannot be refreshed.")
    refresh.add_argument("--skip-bars", action="store_true")
    refresh.add_argument("--strict-bars", action="store_true", help="Fail the command if futures bars cannot be fetched.")
    refresh.add_argument("--bars-contracts", default="", help="Optional ROOT:YYYYMM list. Defaults to first month from each chain spec.")
    refresh.add_argument("--bar-size", default="30 mins")
    refresh.add_argument("--duration", default="1 M")
    refresh.add_argument("--what-to-show", default="TRADES")
    refresh.add_argument("--timeout", type=float, default=45.0)
    refresh.add_argument("--prefer-local-symbol-bars", action="store_true", help="Use localSymbol futures contracts for bars when no cached conId is available.")
    refresh.add_argument("--min-chain-rows", type=int, default=50, help="Minimum per-product chain rows required by --require-ready.")
    refresh.add_argument("--min-bars-rows", type=int, default=100, help="Minimum per-product futures bars required by --require-ready.")
    refresh.add_argument("--max-chain-age-hours", type=float, default=24.0, help="Maximum option-chain snapshot age allowed by --require-ready.")
    refresh.add_argument("--max-bars-age-hours", type=float, default=72.0, help="Maximum futures bar age allowed by --require-ready.")
    refresh.add_argument("--working-dir", default="data/planner/debug")
    refresh.add_argument("--html-data-dir", default="data/planner")
    refresh.add_argument("--require-ready", action="store_true", help="Exit non-zero after publishing unless the HTML inputs are ready for all products with positions.")
    refresh.add_argument("--no-client-lock", action="store_true", help="Disable the local process lock for this IB host/port/client-id.")

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
    print("refresh positions/account snapshot", flush=True)
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


def command_margin_whatif(args: argparse.Namespace) -> None:
    """Run one IB credit-check preview. No live order is transmitted."""
    ib_settings = _ib_settings(args)
    if not ib_settings.account:
        raise SystemExit("--account is required for margin-whatif")
    contract = Contract(conId=int(args.con_id), exchange=args.exchange.strip())
    request = MarginWhatIfRequest(
        contract=contract,
        action=args.action,
        quantity=float(args.quantity),
        order_type=args.order_type,
        limit_price=args.limit_price,
    )
    print(
        "IB margin What-If preview "
        f"(account={ib_settings.account}, conId={args.con_id}, action={args.action.upper()}, quantity={args.quantity:g})",
        flush=True,
    )
    with acquire_ib_client_lock(
        ib_settings.host,
        ib_settings.port,
        ib_settings.client_id,
        purpose="IB margin What-If preview",
    ):
        with ib_connection(ib_settings, fetch_fields=StartupFetch.ACCOUNT_UPDATES) as ib:
            result = run_margin_whatif(
                ib,
                ib_settings.account,
                request,
                qualify_contract=not args.skip_qualification,
            )
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))


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


def command_future_bars(args: argparse.Namespace) -> None:
    ib_settings = _ib_settings(args)
    specs = parse_contract_specs(args.contracts)
    try:
        with ib_connection(ib_settings) as ib:
            frame = fetch_future_bars(
                ib,
                specs,
                bar_size=args.bar_size,
                duration=args.duration,
                what_to_show=args.what_to_show,
                timeout=args.timeout,
                cache_dir=args.cache_dir,
                strict=not args.keep_going,
                prefer_local_symbol=args.prefer_local_symbol,
            )
    except asyncio.TimeoutError as exc:
        raise SystemExit("future-bars timed out while qualifying/fetching IB historical data. Check TWS/Gateway status and contract months.") from exc
    path = save_future_bars(frame, args.output)
    print(f"bars: {len(frame)}")
    print(f"saved: {path}")


def command_ib_smoke(args: argparse.Namespace) -> None:
    ib_settings = _ib_settings(args)
    specs = parse_contract_specs(args.contracts)
    report: dict[str, object] = {
        "host": ib_settings.host,
        "port": ib_settings.port,
        "client_id": ib_settings.client_id,
        "market_data_type": ib_settings.market_data_type,
        "timeout": args.timeout,
        "connected": False,
        "server_time": "",
        "contracts": [],
    }
    with ib_connection(ib_settings) as ib:
        report["connected"] = bool(ib.isConnected())
        previous_timeout = getattr(ib, "RequestTimeout", None)
        if previous_timeout is not None:
            ib.RequestTimeout = float(args.timeout)
        try:
            try:
                report["server_time"] = str(ib.reqCurrentTime())
            except Exception as exc:
                report["server_time_error"] = f"{type(exc).__name__}: {exc}"
            for root, month in specs:
                item: dict[str, object] = {"root": root, "month": month, "ok": False}
                try:
                    contract = Future(
                        symbol=root,
                        lastTradeDateOrContractMonth=month,
                        exchange="CBOT",
                        currency="USD",
                    )
                    qualified = ib.qualifyContracts(contract)
                    item["ok"] = bool(qualified)
                    if qualified:
                        q = qualified[0]
                        item.update(
                            {
                                "conId": getattr(q, "conId", 0),
                                "localSymbol": getattr(q, "localSymbol", ""),
                                "exchange": getattr(q, "exchange", ""),
                            }
                        )
                except Exception as exc:
                    item["error"] = f"{type(exc).__name__}: {exc}"
                report["contracts"].append(item)
        finally:
            if previous_timeout is not None:
                ib.RequestTimeout = previous_timeout
    print(json.dumps(report, ensure_ascii=False, indent=2))


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


def command_sync_carry_html(args: argparse.Namespace) -> None:
    paths = sync_carry_dashboard_files(
        args.positions,
        args.chain,
        bars_path=args.bars.strip() or None,
        output_dir=args.output_dir,
    )
    for name, path in paths.items():
        print(f"{name}: {path}")
    if args.summary_only or args.require_ready:
        report = validate_carry_dashboard_files(
            args.output_dir,
            expected_products=args.expected_products,
            min_chain_rows=args.min_chain_rows,
            min_bars_rows=args.min_bars_rows,
            max_chain_age_hours=args.max_chain_age_hours,
            max_bars_age_hours=args.max_bars_age_hours,
            as_of=args.as_of,
        )
        if args.summary_only:
            print(_format_carry_html_summary(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.require_ready:
            _raise_if_carry_html_not_ready(report)


def command_sync_latest_carry_html(args: argparse.Namespace) -> None:
    discovered = discover_latest_carry_dashboard_inputs(
        args.input_dir,
        output_dir=args.output_dir,
        products=args.products,
        positions_path=args.positions.strip() or None,
        chain_path=args.chain.strip() or None,
        bars_path=args.bars.strip() or None,
    )
    print(f"positions input: {discovered['positions']}")
    print("chain inputs:")
    for path in discovered["chain"]:
        print(f"  {path}")
    if discovered["bars"]:
        print(f"bars input: {discovered['bars']}")
    else:
        print("bars input: <none>")
    paths = sync_carry_dashboard_files(
        discovered["positions"],
        discovered["chain_arg"],
        bars_path=discovered["bars_arg"] or None,
        output_dir=args.output_dir,
    )
    for name, path in paths.items():
        print(f"{name}: {path}")

    expected = args.expected_products.strip() or str(discovered["expected_products"])
    if args.summary_only or args.require_ready:
        report = validate_carry_dashboard_files(
            args.output_dir,
            expected_products=expected,
            min_chain_rows=args.min_chain_rows,
            min_bars_rows=args.min_bars_rows,
            max_chain_age_hours=args.max_chain_age_hours,
            max_bars_age_hours=args.max_bars_age_hours,
            as_of=args.as_of,
        )
        if args.summary_only:
            print(_format_carry_html_summary(report))
        else:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.require_ready:
            _raise_if_carry_html_not_ready(report)


def command_validate_carry_html(args: argparse.Namespace) -> None:
    report = validate_carry_dashboard_files(
        args.data_dir,
        expected_products=args.expected_products,
        min_chain_rows=args.min_chain_rows,
        min_bars_rows=args.min_bars_rows,
        max_chain_age_hours=args.max_chain_age_hours,
        max_bars_age_hours=args.max_bars_age_hours,
        as_of=args.as_of,
    )
    if args.summary_only:
        print(_format_carry_html_summary(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_ready:
        _raise_if_carry_html_not_ready(report)


def command_refresh_carry_html(args: argparse.Namespace) -> None:
    ib_settings = _ib_settings(args)
    if getattr(args, "no_client_lock", False):
        _run_refresh_carry_html(args, ib_settings)
        return

    try:
        with acquire_ib_client_lock(
            ib_settings.host,
            ib_settings.port,
            ib_settings.client_id,
            purpose="refresh-carry-html",
        ):
            _run_refresh_carry_html(args, ib_settings)
    except IbClientLockBusy as exc:
        raise SystemExit(str(exc)) from exc


def _run_refresh_carry_html(args: argparse.Namespace, ib_settings: IBSettings) -> None:
    chain_specs = _parse_chain_specs(args.chain_specs)
    chain_specs.update(_parse_chain_specs(args.zc_chain_specs))
    working_dir = Path(args.working_dir)
    working_dir.mkdir(parents=True, exist_ok=True)
    fast_refresh = bool(getattr(args, "fast_refresh", False))
    effective_quote_wait = min(float(args.quote_wait_seconds), 2.0) if fast_refresh else float(args.quote_wait_seconds)
    effective_wait_seconds = min(float(args.wait_seconds), 5.0) if fast_refresh else float(args.wait_seconds)
    effective_stable_seconds = min(float(args.stable_seconds), 0.75) if fast_refresh else float(args.stable_seconds)
    effective_inter_batch_pause = min(float(args.inter_batch_pause_seconds), 0.25) if fast_refresh else float(args.inter_batch_pause_seconds)
    effective_near_dte = max(int(args.near_dte_days), 10) if fast_refresh else int(args.near_dte_days)
    effective_near_width = min(float(args.near_strike_width), 0.75) if fast_refresh else float(args.near_strike_width)
    effective_far_width = min(float(args.far_strike_width), 1.25) if fast_refresh else float(args.far_strike_width)
    effective_market_data_max_dte = int(args.market_data_max_dte or 0) or (10 if fast_refresh else 0)
    if fast_refresh:
        print(
            "fast refresh enabled: near/current-position contracts only; cached far-chain rows are preserved",
            flush=True,
        )
    print("refresh plan:", flush=True)
    print(f"  mode: {'fast' if fast_refresh else 'full'}", flush=True)
    print(f"  ib: {ib_settings.host}:{ib_settings.port}, client_id={ib_settings.client_id}, account={ib_settings.account or '<none>'}", flush=True)
    print(f"  chain specs: {chain_specs}", flush=True)
    print(
        "  effective timing: "
        f"quote_wait={effective_quote_wait}s, wait={effective_wait_seconds}s, "
        f"stable={effective_stable_seconds}s, request_interval={args.request_interval}s, "
        f"inter_batch_pause={effective_inter_batch_pause}s, timeout={args.timeout}s",
        flush=True,
    )
    print(
        "  effective filter: "
        f"near_dte_days={effective_near_dte}, near_width={effective_near_width}, "
        f"far_width={effective_far_width}, market_data_max_dte={effective_market_data_max_dte or '<none>'}, "
        f"moneyness_filter={not args.no_market_data_filter}",
        flush=True,
    )
    print(
        f"  cache/output: contract_cache={'off' if args.no_contract_cache else 'on'}, "
        f"rebuild_cache={bool(args.rebuild_contract_cache)}, working_dir={working_dir}, html_data_dir={args.html_data_dir}",
        flush=True,
    )

    if args.positions_csv.strip():
        position_frame = pd.read_csv(args.positions_csv)
        print(f"positions reused: {args.positions_csv} ({len(position_frame)} rows)", flush=True)
    else:
        if not ib_settings.account:
            raise SystemExit("--account is required unless --positions-csv is provided")
        dashboard_settings = AccountDashboardSettings(
            quote_wait_seconds=effective_quote_wait,
            infer_spreads=args.infer_spreads,
        )
        # The planner needs positions before anything else. Waiting for the
        # account-update streams here can time out and leave an empty position cache.
        fetch_fields = StartupFetch.POSITIONS
        print("refresh positions snapshot (startup fetch: POSITIONS only)", flush=True)
        try:
            with _time_limit(float(args.positions_timeout)):
                with ib_connection(ib_settings, fetch_fields=fetch_fields) as ib:
                    snapshot = fetch_account_dashboard(ib, ib_settings, dashboard_settings)
            position_frame = snapshot.position_frame
            if position_frame.empty and not getattr(args, "allow_empty_positions", False):
                cached_positions, cached_path = _nonempty_cached_positions(args)
                if cached_path is None:
                    raise SystemExit(
                        "positions snapshot returned 0 rows and no non-empty cached positions are available; "
                        "refusing to publish an empty inventory. Restore the IB API connection, or pass "
                        "--allow-empty-positions only when the account is intentionally flat."
                    )
                message = (
                    "positions snapshot returned 0 rows; "
                    f"preserving cached positions: {cached_path} ({len(cached_positions)} rows)"
                )
                if args.strict_positions:
                    raise SystemExit(message)
                position_frame = cached_positions
                print(message, flush=True)
            snapshot.account_summary.to_csv(working_dir / "dashboard_account_summary.csv", index=False, encoding="utf-8-sig")
            snapshot.greek_summary.to_csv(working_dir / "dashboard_greek_summary.csv", index=False, encoding="utf-8-sig")
            print(f"positions: {len(position_frame)}", flush=True)
        except Exception as exc:
            message = f"positions refresh failed ({type(exc).__name__}: {exc})"
            cached_positions, cached_path = _nonempty_cached_positions(args)
            if args.strict_positions or cached_path is None:
                raise SystemExit(message) from exc
            position_frame = cached_positions
            print(f"{message}; reusing cached positions: {cached_path} ({len(position_frame)} rows)", flush=True)
    position_frame.to_csv(working_dir / "dashboard_treasury_positions.csv", index=False, encoding="utf-8-sig")
    position_con_ids = _position_con_ids_by_root(position_frame)
    force_summary = {root: len(values) for root, values in position_con_ids.items() if values}
    print(f"current-position conIds forced into refresh: {force_summary or '<none>'}", flush=True)

    chain_frames: list[pd.DataFrame] = []
    print("refresh option chains", flush=True)
    existing_chain_path = Path(args.html_data_dir) / "carry_dashboard_chain.csv"
    existing_chain = _read_csv_if_exists(existing_chain_path)
    if not existing_chain.empty:
        print(f"existing HTML chain cache: {existing_chain_path} ({len(existing_chain)} rows)", flush=True)
    try:
        with ib_connection(ib_settings) as ib:
            previous_timeout = getattr(ib, "RequestTimeout", None)
            if previous_timeout is not None:
                ib.RequestTimeout = args.timeout
            try:
                for root, months in chain_specs.items():
                    print(f"refresh chain: {root} months={months}", flush=True)
                    settings = StaticChainSettings(
                        root=root,
                        future_months=months,
                        min_expiration=args.min_expiration.strip() or None,
                        max_expiration=args.max_expiration.strip() or None,
                        batch_size=args.batch_size,
                        wait_max_seconds=effective_wait_seconds,
                        wait_stable_seconds=effective_stable_seconds,
                        request_interval=args.request_interval,
                        inter_batch_pause_seconds=effective_inter_batch_pause,
                        empty_batch_retries=args.empty_batch_retries,
                        empty_batch_retry_pause_seconds=args.empty_batch_retry_pause_seconds,
                        output_dir=working_dir,
                        use_contract_cache=not args.no_contract_cache,
                        force_rebuild_contract_cache=args.rebuild_contract_cache,
                        filter_market_data_by_moneyness=not args.no_market_data_filter,
                        near_dte_days=effective_near_dte,
                        near_strike_width=effective_near_width,
                        far_strike_width=effective_far_width,
                        market_data_max_dte=effective_market_data_max_dte or None,
                        force_con_ids=position_con_ids.get(root, ()),
                        future_price_wait_seconds=float(args.future_price_wait_seconds),
                    )
                    future_prices, future_price_source, future_price_error, future_prices_path = refresh_future_prices_sidecar(ib, settings)
                    print(
                        f"{root} future prices sidecar: source={future_price_source}, "
                        f"path={future_prices_path}, prices={_future_price_summary(future_prices)}",
                        flush=True,
                    )
                    if future_price_error:
                        print(f"{root} future price refresh warning: {future_price_error}", flush=True)
                    try:
                        result = refresh_static_chain(ib, settings)
                        refreshed_frame = result.monitor_frame
                        raw = result.raw
                        print(
                            f"{root} universe: source={raw.get('universe_source', '<unknown>')}, "
                            f"contracts={raw.get('contract_count', len(raw.get('contracts', [])))}, "
                            f"selected_for_quotes={raw.get('selected_contract_count', 0)}, "
                            f"snapshot_rows={raw.get('snapshot_count', len(raw.get('snapshot', [])))}",
                            flush=True,
                        )
                        print(
                            f"{root} future prices used for filtering: "
                            f"source={raw.get('future_price_source', '<unknown>')}, "
                            f"prices={_future_price_summary(raw.get('future_prices', pd.DataFrame()))}",
                            flush=True,
                        )
                        if raw.get("future_price_error"):
                            print(f"{root} future price refresh warning: {raw.get('future_price_error')}", flush=True)
                        print(f"{root} contract cache: {raw.get('contract_cache_path', '<none>')}", flush=True)
                        if fast_refresh and not existing_chain.empty and "symbol" in existing_chain.columns:
                            existing_root = existing_chain[existing_chain["symbol"].astype(str).str.upper() == root].copy()
                            refreshed_count = len(refreshed_frame)
                            refreshed_frame = _merge_chain_rows(existing_root, refreshed_frame)
                            print(
                                f"{root} fast merge: existing_html_rows={len(existing_root)}, "
                                f"fresh_rows={refreshed_count}, merged_rows={len(refreshed_frame)}",
                                flush=True,
                            )
                        if not refreshed_frame.empty:
                            chain_frames.append(refreshed_frame)
                        print(
                            f"{root} chain rows: {len(refreshed_frame)} "
                            f"(refreshed {len(result.monitor_frame)}, selected {result.raw.get('selected_contract_count', 0)})",
                            flush=True,
                        )
                    except Exception as exc:
                        message = f"{root} chain refresh failed ({type(exc).__name__}); keeping existing HTML chain rows for this root if present"
                        if args.strict_chain:
                            raise SystemExit(message) from exc
                        print(message, flush=True)
                        if not existing_chain.empty and "symbol" in existing_chain.columns:
                            fallback = existing_chain[existing_chain["symbol"].astype(str).str.upper() == root].copy()
                            if not fallback.empty:
                                chain_frames.append(fallback)
                                print(f"{root} fallback chain rows: {len(fallback)}", flush=True)
            finally:
                if previous_timeout is not None:
                    ib.RequestTimeout = previous_timeout
    except Exception as exc:
        message = f"option chain IB connection failed ({type(exc).__name__}: {exc}); preserving existing HTML chain rows if present"
        if args.strict_chain:
            raise SystemExit(message) from exc
        print(message, flush=True)
        if not existing_chain.empty and "symbol" in existing_chain.columns:
            for root in chain_specs:
                fallback = existing_chain[existing_chain["symbol"].astype(str).str.upper() == root].copy()
                if not fallback.empty:
                    chain_frames.append(fallback)
                    print(f"{root} fallback chain rows: {len(fallback)}", flush=True)
        elif not existing_chain.empty:
            chain_frames.append(existing_chain)
            print(f"fallback chain rows: {len(existing_chain)}", flush=True)

    if chain_frames:
        combined_chain = pd.concat(chain_frames, ignore_index=True)
    elif not existing_chain.empty:
        print("all chain refreshes failed; preserving existing HTML chain file", flush=True)
        combined_chain = existing_chain
    else:
        combined_chain = pd.DataFrame()
    existing_bars_path = Path(args.html_data_dir) / "carry_dashboard_bars.csv"
    existing_bars = _read_csv_if_exists(existing_bars_path)
    bars_frame = existing_bars.copy()
    skip_bars = bool(args.skip_bars or (fast_refresh and not existing_bars.empty))
    if skip_bars and fast_refresh and not existing_bars.empty:
        print(f"fast refresh: preserving existing futures bars ({len(existing_bars)} rows)", flush=True)
    if not skip_bars:
        print("refresh futures bars", flush=True)
        bar_specs = parse_contract_specs(args.bars_contracts) if args.bars_contracts.strip() else _bar_specs_from_chain_specs(chain_specs)
        print(f"bar specs: {bar_specs}", flush=True)
        try:
            with ib_connection(ib_settings) as ib:
                fetched_bars = fetch_future_bars(
                    ib,
                    bar_specs,
                    bar_size=args.bar_size,
                    duration=args.duration,
                    what_to_show=args.what_to_show,
                    timeout=args.timeout,
                    cache_dir=working_dir,
                    strict=False,
                    prefer_local_symbol=args.prefer_local_symbol_bars,
                )
            if not fetched_bars.empty:
                bars_frame = fetched_bars
                save_future_bars(bars_frame, working_dir / "carry_dashboard_bars.csv")
                print(f"futures bars fetched: {len(fetched_bars)} rows", flush=True)
            elif not existing_bars.empty:
                print("future bars returned 0 rows; preserving existing HTML bars file", flush=True)
        except Exception as exc:
            message = f"future bars failed ({type(exc).__name__}); preserving existing HTML bars file if present"
            if args.strict_bars:
                raise SystemExit(message) from exc
            print(message, flush=True)

    paths = write_carry_dashboard_files(
        position_frame,
        combined_chain,
        bars=bars_frame,
        output_dir=args.html_data_dir,
    )
    print("published:", flush=True)
    for name, path in paths.items():
        print(f"  {name}: {path}", flush=True)
    report = validate_carry_dashboard_files(
        args.html_data_dir,
        expected_products=",".join(chain_specs),
        min_chain_rows=args.min_chain_rows,
        min_bars_rows=args.min_bars_rows,
        max_chain_age_hours=args.max_chain_age_hours,
        max_bars_age_hours=args.max_bars_age_hours,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.require_ready:
        _raise_if_carry_html_not_ready(report)


def command_serve_carry_html(args: argparse.Namespace) -> None:
    _serve_static_html(args, "carry_risk_dashboard.html")


def command_serve_inventory_planner(args: argparse.Namespace) -> None:
    _serve_static_html(args, "sell_side_inventory_planner.html", handler_factory=inventory_planner_handler)


def _serve_static_html(args: argparse.Namespace, html_file: str, handler_factory=None) -> None:
    directory = Path(args.directory).resolve()
    html_path = directory / html_file
    if not html_path.exists():
        raise SystemExit(f"{html_file} not found under {directory}")

    if handler_factory is None:
        handler = partial(SimpleHTTPRequestHandler, directory=str(directory))
    else:
        handler = handler_factory(directory)
    try:
        server = ThreadingHTTPServer((args.host, args.port), handler)
    except OSError as exc:
        raise SystemExit(f"cannot bind {args.host}:{args.port} ({exc}); try --port 0 or another port") from exc
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}/{html_file}"
    print(f"serving: {directory}", flush=True)
    print(f"open: {url}", flush=True)
    print("press Ctrl+C to stop", flush=True)
    if getattr(args, "open", False):
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "dashboard-snapshot":
        command_dashboard(args)
    elif args.command == "batch-chain":
        command_batch_chain(args)
    elif args.command == "live-chain":
        command_live_chain(args)
    elif args.command == "future-bars":
        command_future_bars(args)
    elif args.command == "ib-smoke":
        command_ib_smoke(args)
    elif args.command == "margin-whatif":
        command_margin_whatif(args)
    elif args.command == "quality-report":
        command_quality_report(args)
    elif args.command == "sync-carry-html":
        command_sync_carry_html(args)
    elif args.command == "sync-latest-carry-html":
        command_sync_latest_carry_html(args)
    elif args.command == "validate-carry-html":
        command_validate_carry_html(args)
    elif args.command == "serve-carry-html":
        command_serve_carry_html(args)
    elif args.command == "serve-inventory-planner":
        command_serve_inventory_planner(args)
    elif args.command == "refresh-carry-html":
        command_refresh_carry_html(args)


if __name__ == "__main__":
    main()
