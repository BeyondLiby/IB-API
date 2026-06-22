from __future__ import annotations

import argparse
import threading
import time
from typing import Any

from ibapi.client import EClient
from ibapi.wrapper import EWrapper

from news_api.config import SETTINGS


class NewsProviderProbeClient(EWrapper, EClient):
    def __init__(self) -> None:
        EClient.__init__(self, self)
        self.providers: list[tuple[str, str]] = []
        self.errors: list[dict[str, Any]] = []
        self._done = threading.Event()
        self._api_thread: threading.Thread | None = None

    def start_api(self, host: str, port: int, client_id: int) -> None:
        self.connect(host, port, clientId=client_id)
        self._api_thread = threading.Thread(target=self.run, daemon=True)
        self._api_thread.start()

    def stop_api(self) -> None:
        if self.isConnected():
            self.disconnect()
        if self._api_thread:
            self._api_thread.join(timeout=5)

    def newsProviders(self, newsProviders: list[Any]) -> None:
        self.providers = [
            (provider.code, provider.name)
            for provider in newsProviders
        ]
        self._done.set()

    def error(self, reqId: int, *args: Any) -> None:
        if len(args) == 2:
            error_code, error_string = args
        elif len(args) == 3:
            if isinstance(args[1], int):
                _, error_code, error_string = args
            else:
                error_code, error_string, _ = args
        elif len(args) >= 4:
            _, error_code, error_string, _ = args[:4]
        else:
            error_code = -1
            error_string = "Unknown IBKR error callback payload"

        if error_code in {2104, 2106, 2107, 2108, 2119, 2158}:
            return
        self.errors.append(
            {"req_id": reqId, "code": error_code, "message": str(error_string)}
        )


def fetch_news_providers(
    *,
    host: str = SETTINGS.host,
    port: int = SETTINGS.port,
    client_id: int = SETTINGS.client_id + 300,
    timeout: float = 8.0,
) -> list[tuple[str, str]]:
    client = NewsProviderProbeClient()
    client.start_api(host, port, client_id)
    time.sleep(1)
    try:
        client.reqNewsProviders()
        client._done.wait(timeout=timeout)
        if client.errors and not client.providers:
            print("provider_errors", client.errors)
        return client.providers
    finally:
        client.stop_api()


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
    from news_api.realtime.broadtape_monitor import run_broadtape_probe

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
