from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_api.bark_client import BarkClient
from news_api.config import SETTINGS, NewsSettings, split_provider_codes
from news_api.portfolio_watchlist import PORTFOLIO_WATCHLIST
from news_api.service import NewsService
from news_api.storage import SQLiteNewsStorage
from news_api.verified_news_monitor import VerifiedIBNewsMonitor, monitoring_verdict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verified IBKR news monitor. A clock redraw is never reported as a news refresh; "
            "only a new article_id with a valid publication time is LIVE."
        )
    )
    parser.add_argument("--mode", choices=["all", "portfolio", "both"], default="both")
    parser.add_argument("--host", default=SETTINGS.host)
    parser.add_argument("--port", type=int, default=SETTINGS.port)
    parser.add_argument("--client-id", type=int, default=1198)
    parser.add_argument("--seconds", type=float, default=300)
    parser.add_argument("--heartbeat-seconds", type=float, default=10)
    parser.add_argument("--warmup-seconds", type=float, default=15)
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated stocks. Known portfolio metadata is reused; unknown symbols default to SMART/USD.",
    )
    parser.add_argument(
        "--provider-codes",
        default="auto",
        help="Stock-news providers joined by +, or auto to use reqNewsProviders().",
    )
    parser.add_argument(
        "--broadtape-providers",
        default="auto",
        help=(
            "Provider codes to map to known NEWS contracts, or auto to probe "
            "all visible providers (including Dow Jones classified channels)."
        ),
    )
    parser.add_argument("--history-results", type=int, default=50)
    parser.add_argument(
        "--history-audit-symbol",
        default="",
        help="Periodically cross-check this stock's historical headlines against its stream.",
    )
    parser.add_argument("--history-poll-seconds", type=float, default=60)
    parser.add_argument("--history-gap-grace-seconds", type=float, default=60)
    parser.add_argument(
        "--audit-log",
        default=str(Path(__file__).resolve().parent / "data" / "news_delivery_audit.jsonl"),
    )
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parent / "data" / "verified_live_news.sqlite"),
        help="Only verified LIVE headlines enter this existing news pipeline database.",
    )
    parser.add_argument("--push", action="store_true")
    parser.add_argument(
        "--require-live",
        action="store_true",
        help="Exit with status 2 if no subscription produced a verified LIVE headline.",
    )
    parser.add_argument(
        "--stop-on-live",
        action="store_true",
        help="End the monitor as soon as one verified LIVE headline arrives.",
    )
    return parser.parse_args()


def build_watchlist(mode: str, symbols_value: str) -> dict[str, dict[str, Any]]:
    if mode == "all":
        result: dict[str, dict[str, Any]] = {}
    else:
        result = dict(PORTFOLIO_WATCHLIST)
    if symbols_value:
        requested = [item.strip().upper() for item in symbols_value.split(",") if item.strip()]
        result = {
            symbol: dict(
                PORTFOLIO_WATCHLIST.get(
                    symbol,
                    {
                        "exchange": "SMART",
                        "currency": "USD",
                        "sec_type": "STK",
                        "priority": 0,
                        "aliases": [symbol],
                    },
                )
            )
            for symbol in requested
        }
    if mode in {"all", "both"}:
        result["ALL"] = {
            "exchange": "NEWS",
            "currency": "",
            "priority": 0,
            "aliases": ["ALL"],
        }
    return result


def main() -> int:
    args = parse_args()
    if args.push and not os.getenv("BARK_KEY", ""):
        raise RuntimeError("--push requires BARK_KEY.")

    watchlist = build_watchlist(args.mode, args.symbols)
    db_path = Path(args.db).resolve()
    settings = NewsSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        db_path=db_path,
        article_fetch_score=101,
        portfolio_push_score=0 if args.push else 101,
        default_push_score=0 if args.push else 101,
        bark_key=os.getenv("BARK_KEY", ""),
        bark_base_url=SETTINGS.bark_base_url,
        dashboard_url=SETTINGS.dashboard_url,
    )
    service = NewsService(
        settings=settings,
        watchlist=watchlist,
        storage=SQLiteNewsStorage(db_path),
        bark_client=BarkClient(
            key=settings.bark_key,
            base_url=settings.bark_base_url,
            dashboard_url=settings.dashboard_url,
        ),
    )
    monitor = VerifiedIBNewsMonitor(
        audit_path=Path(args.audit_log).resolve(),
        live_sink=service.ingest_tick_news,
        warmup_seconds=args.warmup_seconds,
        history_results=args.history_results,
    )
    service.start()

    try:
        monitor.connect(args.host, args.port, args.client_id)
        print("visible_news_providers:", monitor.news_providers, flush=True)
        provider_codes = (
            monitor.visible_provider_codes
            if args.provider_codes.lower() == "auto"
            else split_provider_codes(args.provider_codes)
        )
        provider_string = "+".join(provider_codes)
        if args.mode in {"portfolio", "both"}:
            for symbol, item in watchlist.items():
                if symbol == "ALL":
                    continue
                state = monitor.subscribe_stock(symbol, item, provider_string)
                print(
                    f"stock_subscription {symbol}: "
                    f"{state.req_id if state else '<qualification failed>'}",
                    flush=True,
                )

        if args.mode in {"all", "both"}:
            broadtape_codes = (
                monitor.visible_provider_codes
                if args.broadtape_providers.lower() == "auto"
                else split_provider_codes(args.broadtape_providers)
            )
            contracts = monitor.discover_broadtape_contracts(broadtape_codes)
            print(
                "broadtape_contracts:",
                [(provider, contract.symbol, contract.conId) for provider, contract in contracts],
                flush=True,
            )
            for provider, contract in contracts:
                monitor.subscribe_broadtape(provider, contract)

        monitor.run(
            seconds=args.seconds,
            heartbeat_seconds=args.heartbeat_seconds,
            history_audit_symbol=args.history_audit_symbol.upper(),
            history_poll_seconds=args.history_poll_seconds,
            gap_grace_seconds=args.history_gap_grace_seconds,
            stop_on_live=args.stop_on_live,
        )
    finally:
        monitor.stop()
        service.stop()

    live_total = sum(item.live_callbacks for item in monitor.subscriptions.values())
    verdict = monitoring_verdict(monitor.subscriptions.values())
    print(
        "FINAL_VERDICT:",
        verdict,
        f"live_total={live_total}",
        f"audit_log={Path(args.audit_log).resolve()}",
        f"db={db_path}",
        flush=True,
    )
    if args.require_live and live_total == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
