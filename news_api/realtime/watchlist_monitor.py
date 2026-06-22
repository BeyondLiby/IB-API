from __future__ import annotations

import argparse
import time
from typing import Any

from news_api.config import SETTINGS
from news_api.ib_client import IBNewsClient
from news_api.realtime.probe import fetch_news_providers
from news_api.subscription_manager import SubscriptionManager
from news_api.watchlist import normalize_watchlist


class CapturingService:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def start(self) -> None:
        print("service_start")

    def stop(self) -> None:
        print("service_stop")

    def ingest_tick_news(self, **kwargs: Any) -> bool:
        self.events.append(kwargs)
        print(
            "tick_news",
            kwargs.get("symbol"),
            kwargs.get("provider"),
            kwargs.get("article_id"),
            str(kwargs.get("headline"))[:100],
        )
        return True


def run_watchlist_probe(
    *,
    host: str = SETTINGS.host,
    port: int = SETTINGS.port,
    client_id: int = SETTINGS.client_id + 100,
    seconds: int = 30,
    market_data_type: int | None = SETTINGS.market_data_type,
    require_news_providers: bool = True,
) -> CapturingService:
    service = CapturingService()
    if require_news_providers:
        providers = fetch_news_providers(
            host=host,
            port=port,
            client_id=client_id + 1000,
        )
        print("news_providers", providers)
        if not providers:
            print(
                "No API news providers are available in this IB Gateway session; "
                "generic tick 292 is not legal for STK subscriptions."
            )
            print("final_events", len(service.events))
            return service

    client = IBNewsClient(service)  # type: ignore[arg-type]
    client.start_api(host, port, client_id)
    time.sleep(2)
    try:
        subscribed = SubscriptionManager(client).subscribe_watchlist(
            normalize_watchlist(),
            SETTINGS.provider_codes,
            market_data_type,
        )
        print("settings_market_data_type", market_data_type)
        print("subscriptions", subscribed)
        print("ticker_map", client.ticker_id_to_symbol)
        for sec in range(1, seconds + 1):
            time.sleep(1)
            if sec in {5, 15, seconds}:
                print(
                    "heartbeat",
                    sec,
                    "connected",
                    client.isConnected(),
                    "thread_alive",
                    client._api_thread.is_alive() if client._api_thread else None,
                    "events",
                    len(service.events),
                )
    finally:
        client.stop_api()
    print("final_events", len(service.events))
    return service


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe watchlist contract-specific news ticks.")
    parser.add_argument("--host", default=SETTINGS.host)
    parser.add_argument("--port", type=int, default=SETTINGS.port)
    parser.add_argument("--client-id", type=int, default=SETTINGS.client_id + 100)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--market-data-type", type=int, default=SETTINGS.market_data_type)
    parser.add_argument("--skip-provider-check", action="store_true")
    args = parser.parse_args()
    run_watchlist_probe(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        seconds=args.seconds,
        market_data_type=args.market_data_type,
        require_news_providers=not args.skip_provider_check,
    )


if __name__ == "__main__":
    main()
