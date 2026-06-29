from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from news_api.bark_client import BarkClient
from news_api.config import (
    DEFAULT_BROADTAPE_PROVIDER_CODES,
    DEFAULT_CONTRACT_NEWS_PROVIDER_CODES,
    SETTINGS,
    NewsSettings,
    split_provider_codes,
)
from news_api.ib_client import IBArticleFetcher, IBNewsClient
from news_api.portfolio_watchlist import ALL_NEWS_WATCHLIST, PORTFOLIO_WATCHLIST
from news_api.service import NewsService
from news_api.storage import SQLiteNewsStorage
from news_api.subscription_manager import SubscriptionManager


def fetch_rows(db_path: Path, limit: int = 20) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []

    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT
                r.received_at,
                r.published_at,
                r.symbol,
                r.provider,
                r.article_id,
                r.publisher,
                r.headline,
                COALESCE(a.fetch_status, '') AS fetch_status,
                COALESCE(a.error, '') AS article_error,
                COALESCE(a.article_text, '') AS article_text,
                COALESCE(e.event_type, '') AS event_type,
                COALESCE(e.importance_score, '') AS importance_score,
                COALESCE(e.should_push, '') AS should_push,
                COALESCE(p.push_status, '') AS push_status,
                COALESCE(p.response, '') AS push_response
            FROM news_raw r
            LEFT JOIN news_articles a ON a.unique_key = r.unique_key
            LEFT JOIN news_events e ON e.unique_key = r.unique_key
            LEFT JOIN news_push_log p ON p.unique_key = r.unique_key
            ORDER BY r.received_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    return [dict(row) for row in rows]


def print_news(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No news received yet.")
        return

    for index, row in enumerate(rows, 1):
        body = (row.get("article_text") or "").replace("\n", " ").strip()
        if len(body) > 500:
            body = body[:500] + "..."

        print("=" * 100)
        print(
            f"#{index} {row['received_at']} "
            f"(ib_time={row.get('published_at', '')}) "
            f"[{row['symbol']}] {row['provider']} {row['publisher']}"
        )
        print("headline:", row["headline"])
        print(
            "article:",
            row["fetch_status"] or "<not requested>",
            row["article_error"] or "",
        )
        print("article_text:", body or "<empty>")
        print(
            "event/score/push:",
            row["event_type"],
            row["importance_score"],
            "should_push=",
            row["should_push"],
            "push_status=",
            row["push_status"],
        )
        if row.get("push_response"):
            print("push_response:", row["push_response"][:300])


def build_watchlist(mode: str) -> dict[str, dict]:
    if mode == "all":
        return dict(ALL_NEWS_WATCHLIST)
    if mode == "portfolio":
        return dict(PORTFOLIO_WATCHLIST)
    if mode == "both":
        return {**ALL_NEWS_WATCHLIST, **PORTFOLIO_WATCHLIST}
    raise ValueError(f"unknown mode: {mode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="IBKR live news monitor: all-news BroadTape plus portfolio fallback."
    )
    parser.add_argument(
        "--mode",
        choices=["all", "portfolio", "both"],
        default="both",
        help="all=BroadTape only, portfolio=stock list only, both=both.",
    )
    parser.add_argument("--host", default=SETTINGS.host)
    parser.add_argument("--port", type=int, default=SETTINGS.port)
    parser.add_argument("--client-id", type=int, default=SETTINGS.client_id)
    parser.add_argument("--seconds", type=int, default=300)
    parser.add_argument("--print-every", type=int, default=10)
    parser.add_argument(
        "--db",
        default=str(Path(__file__).resolve().parent / "data" / "live_news.sqlite"),
    )
    parser.add_argument(
        "--provider-codes",
        default=DEFAULT_CONTRACT_NEWS_PROVIDER_CODES,
        help=(
            "Contract-specific stock news providers for reqMktData(stock, ...). "
            "Default: BRFG+BRFUPDN+DJNL."
        ),
    )
    parser.add_argument(
        "--broadtape-providers",
        default=DEFAULT_BROADTAPE_PROVIDER_CODES,
        help=(
            "BroadTape NEWS contract providers to try. Default: BRF+BZ+FLY. "
            "Do not put BRFG/DJNL here unless IB confirms a matching NEWS contract exists."
        ),
    )
    parser.add_argument(
        "--broadtape-symbol-template",
        default="{provider}:{provider}_ALL",
        help='Template for BroadTape contract symbol. Default: "{provider}:{provider}_ALL".',
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Send real Bark pushes. Without this, monitor only prints and stores.",
    )
    parser.add_argument(
        "--fetch-article",
        action="store_true",
        help="Request article text after each headline.",
    )
    parser.add_argument(
        "--list-providers",
        action="store_true",
        help="Request and print news providers visible to this account.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_path = Path(args.db).resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    bark_key = os.getenv("BARK_KEY", "")
    if args.push and not bark_key:
        raise RuntimeError("You enabled --push. Please set BARK_KEY first.")

    push_score = 0 if args.push else 101
    article_fetch_score = 0 if args.fetch_article else 101
    watchlist = build_watchlist(args.mode)

    settings = NewsSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        provider_codes=args.provider_codes,
        db_path=db_path,
        article_fetch_score=article_fetch_score,
        portfolio_push_score=push_score,
        default_push_score=push_score,
        bark_key=bark_key,
        bark_base_url=SETTINGS.bark_base_url,
        dashboard_url=SETTINGS.dashboard_url,
    )

    storage = SQLiteNewsStorage(db_path)
    service = NewsService(
        settings=settings,
        watchlist=watchlist,
        storage=storage,
        bark_client=BarkClient(
            key=bark_key,
            base_url=settings.bark_base_url,
            dashboard_url=settings.dashboard_url,
            timeout=30,
            retries=2,
        ),
    )
    client = IBNewsClient(service)
    service.article_fetcher = IBArticleFetcher(client, timeout=20)

    provider_codes = split_provider_codes(args.provider_codes)
    broadtape_providers = split_provider_codes(args.broadtape_providers)

    print("startup:")
    print(
        {
            "mode": args.mode,
            "host": args.host,
            "port": args.port,
            "client_id": args.client_id,
            "db": str(db_path),
            "push": args.push,
            "fetch_article": args.fetch_article,
            "contract_news_provider_codes": provider_codes,
            "broadtape_providers": broadtape_providers,
            "symbols": list(watchlist),
        }
    )

    client.start_api(args.host, args.port, args.client_id)
    manager = SubscriptionManager(client)

    if args.list_providers:
        client.reqNewsProviders()
        time.sleep(2)
        print("visible_news_providers:", client.news_providers)
        print(
            "note: visible_news_providers are article/provider channels. "
            "Contract-specific stock news uses mdoff,292:BRFG+BRFUPDN+DJNL. "
            "BroadTape NEWS contracts are separate, commonly BRF/BZ/FLY."
        )

    if args.mode in {"all", "both"}:
        broadtape_subscribed: dict[str, int] = {}
        for provider in broadtape_providers:
            contract_symbol = args.broadtape_symbol_template.format(provider=provider)
            ticker_id = manager.subscribe_broadtape(
                provider,
                symbol_alias="ALL",
                contract_symbol=contract_symbol,
            )
            broadtape_subscribed[provider] = ticker_id
        print("BroadTape subscriptions sent:", broadtape_subscribed)

    if args.mode in {"portfolio", "both"}:
        subscribed = manager.subscribe_watchlist(
            PORTFOLIO_WATCHLIST,
            args.provider_codes,
        )
        print("Portfolio stock news subscriptions sent:", subscribed)

    deadline = time.time() + args.seconds
    try:
        while time.time() < deadline:
            print("\n" + "#" * 40, datetime.now().strftime("%H:%M:%S"), "#" * 40)
            if client.errors:
                print("IB errors:", client.errors[-10:])
            print_news(fetch_rows(db_path, limit=10))
            time.sleep(args.print_every)
    finally:
        print("Stopping monitor and disconnecting IB.")
        client.stop_api()
        print("database:", db_path)
        print_news(fetch_rows(db_path, limit=50))


if __name__ == "__main__":
    main()
