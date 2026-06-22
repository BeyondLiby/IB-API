from __future__ import annotations

import argparse
from typing import Any

from ib_async import IB

from news_api.config import SETTINGS
from news_api.realtime.broadtape_monitor import run_broadtape_probe


def fetch_news_providers(
    *,
    host: str = SETTINGS.host,
    port: int = SETTINGS.port,
    client_id: int = SETTINGS.client_id + 300,
) -> list[tuple[str, str]]:
    ib = IB()
    ib.connect(host, port, clientId=client_id, readonly=True, timeout=8)
    try:
        return [(provider.code, provider.name) for provider in ib.reqNewsProviders()]
    finally:
        ib.disconnect()


def run_capability_probe(
    *,
    host: str = SETTINGS.host,
    port: int = SETTINGS.port,
    provider_client_id: int = SETTINGS.client_id + 300,
    broadtape_client_id: int = SETTINGS.client_id + 301,
    seconds: int = 30,
    specs: tuple[str, ...] | list[str] | str = SETTINGS.broadtape_specs,
    market_data_type: int | None = SETTINGS.market_data_type,
) -> dict[str, Any]:
    providers = fetch_news_providers(
        host=host,
        port=port,
        client_id=provider_client_id,
    )
    print("news_providers", providers)
    broadtape_client = run_broadtape_probe(
        host=host,
        port=port,
        client_id=broadtape_client_id,
        seconds=seconds,
        specs=specs,
        market_data_type=market_data_type,
    )
    return {
        "providers": providers,
        "broadtape_events": broadtape_client.events,
        "broadtape_errors": broadtape_client.errors,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe IBKR news provider and BroadTape capabilities.")
    parser.add_argument("--host", default=SETTINGS.host)
    parser.add_argument("--port", type=int, default=SETTINGS.port)
    parser.add_argument("--provider-client-id", type=int, default=SETTINGS.client_id + 300)
    parser.add_argument("--broadtape-client-id", type=int, default=SETTINGS.client_id + 301)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--market-data-type", type=int, default=SETTINGS.market_data_type)
    parser.add_argument("--specs", default=",".join(SETTINGS.broadtape_specs))
    args = parser.parse_args()
    run_capability_probe(
        host=args.host,
        port=args.port,
        provider_client_id=args.provider_client_id,
        broadtape_client_id=args.broadtape_client_id,
        seconds=args.seconds,
        specs=args.specs,
        market_data_type=args.market_data_type,
    )


if __name__ == "__main__":
    main()
