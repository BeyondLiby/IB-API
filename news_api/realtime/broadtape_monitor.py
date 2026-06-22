from __future__ import annotations

import argparse
import threading
import time
from dataclasses import dataclass
from itertools import count
from typing import Any

from news_api.cleaner import clean_headline, parse_headline_metadata
from news_api.config import SETTINGS
from news_api.models import NewsHeadline
from news_api.storage import SQLiteNewsStorage

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper
except ImportError:
    class EClient:  # type: ignore[no-redef]
        pass

    class EWrapper:  # type: ignore[no-redef]
        pass

    Contract = None  # type: ignore[assignment]


STATUS_CODES = {2104, 2106, 2107, 2108, 2119, 2158}


@dataclass(frozen=True, slots=True)
class BroadTapeSpec:
    symbol: str
    exchange: str

    @property
    def key(self) -> str:
        return f"{self.symbol}@{self.exchange}"


@dataclass(slots=True)
class CapturedNewsTick:
    source: str
    ticker_id: int
    timestamp: int
    provider: str
    article_id: str
    headline: str
    extra_data: str = ""


def parse_broadtape_specs(values: tuple[str, ...] | list[str] | str) -> list[BroadTapeSpec]:
    if isinstance(values, str):
        items = [item.strip() for item in values.split(",") if item.strip()]
    else:
        items = list(values)

    result: list[BroadTapeSpec] = []
    for item in items:
        if "@" in item:
            symbol, exchange = item.rsplit("@", 1)
        else:
            symbol = item
            exchange = item.split(":", 1)[0]
        symbol = symbol.strip()
        exchange = exchange.strip()
        if not symbol or not exchange:
            raise ValueError(f"Invalid BroadTape spec: {item!r}")
        result.append(BroadTapeSpec(symbol=symbol, exchange=exchange))
    return result


def broadtape_contract(spec: BroadTapeSpec) -> Any:
    if Contract is None:
        raise RuntimeError("ibapi is not installed; cannot create NEWS contract.")
    contract = Contract()
    contract.symbol = spec.symbol
    contract.secType = "NEWS"
    contract.exchange = spec.exchange
    return contract


class BroadTapeNewsClient(EWrapper, EClient):  # type: ignore[misc]
    def __init__(self, storage: SQLiteNewsStorage | None = None) -> None:
        if Contract is None:
            raise RuntimeError("ibapi is not installed; install Interactive Brokers Python API.")
        EClient.__init__(self, self)
        self.storage = storage
        self.ticker_id_to_source: dict[int, str] = {}
        self.events: list[CapturedNewsTick] = []
        self.errors: list[dict[str, Any]] = []
        self._api_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start_api(self, host: str, port: int, client_id: int) -> None:
        self.connect(host, port, clientId=client_id)
        self._api_thread = threading.Thread(target=self.run, daemon=True)
        self._api_thread.start()

    def stop_api(self) -> None:
        if self.isConnected():
            self.disconnect()
        if self._api_thread:
            self._api_thread.join(timeout=5)

    def subscribe_broadtape(
        self,
        specs: list[BroadTapeSpec],
        *,
        market_data_type: int | None = 3,
        start_ticker_id: int = 8000,
    ) -> dict[str, int]:
        if market_data_type is not None:
            self.reqMarketDataType(market_data_type)

        ids = count(start_ticker_id)
        subscribed: dict[str, int] = {}
        for spec in specs:
            ticker_id = next(ids)
            self.ticker_id_to_source[ticker_id] = spec.key
            self.reqMktData(
                ticker_id,
                broadtape_contract(spec),
                "mdoff,292",
                False,
                False,
                [],
            )
            subscribed[spec.key] = ticker_id
        return subscribed

    def tickNews(
        self,
        tickerId: int,
        timeStamp: int,
        providerCode: str,
        articleId: str,
        headline: str,
        extraData: str,
    ) -> None:
        source = self.ticker_id_to_source.get(tickerId, f"ticker:{tickerId}")
        tick = CapturedNewsTick(
            source=source,
            ticker_id=tickerId,
            timestamp=timeStamp,
            provider=providerCode,
            article_id=articleId,
            headline=headline,
            extra_data=extraData,
        )
        with self._lock:
            self.events.append(tick)
        if self.storage is not None:
            self.storage.save_raw(self._to_headline(tick))

    def _to_headline(self, tick: CapturedNewsTick) -> NewsHeadline:
        parsed = parse_headline_metadata(tick.headline)
        return NewsHeadline(
            symbol=tick.source,
            provider=tick.provider,
            article_id=tick.article_id,
            headline=clean_headline(tick.headline),
            headline_raw=tick.headline,
            publisher=parsed["publisher"],
            published_at=str(tick.timestamp),
            ticker_id=tick.ticker_id,
            extra_data=tick.extra_data,
        )

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

        if error_code in STATUS_CODES:
            return
        error = {"req_id": reqId, "code": error_code, "message": str(error_string)}
        with self._lock:
            self.errors.append(error)
        print(f"IBKR error reqId={reqId} code={error_code}: {error_string}")


def run_broadtape_probe(
    *,
    host: str = SETTINGS.host,
    port: int = SETTINGS.port,
    client_id: int = SETTINGS.client_id + 200,
    seconds: int = 30,
    specs: tuple[str, ...] | list[str] | str = SETTINGS.broadtape_specs,
    market_data_type: int | None = SETTINGS.market_data_type,
) -> BroadTapeNewsClient:
    client = BroadTapeNewsClient()
    parsed_specs = parse_broadtape_specs(specs)
    client.start_api(host, port, client_id)
    time.sleep(2)
    try:
        subscribed = client.subscribe_broadtape(
            parsed_specs,
            market_data_type=market_data_type,
        )
        print("broadtape_specs", [spec.key for spec in parsed_specs])
        print("market_data_type", market_data_type)
        print("subscriptions", subscribed)
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
                    len(client.events),
                    "errors",
                    len(client.errors),
                )
    finally:
        client.stop_api()
    print("final_events", len(client.events))
    print("final_errors", len(client.errors))
    return client


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe IBKR BroadTape NEWS subscriptions.")
    parser.add_argument("--host", default=SETTINGS.host)
    parser.add_argument("--port", type=int, default=SETTINGS.port)
    parser.add_argument("--client-id", type=int, default=SETTINGS.client_id + 200)
    parser.add_argument("--seconds", type=int, default=30)
    parser.add_argument("--market-data-type", type=int, default=SETTINGS.market_data_type)
    parser.add_argument(
        "--specs",
        default=",".join(SETTINGS.broadtape_specs),
        help="Comma-separated NEWS specs, e.g. BRF:BRF_ALL@BRF,BZ:BZ_ALL@BZ.",
    )
    args = parser.parse_args()
    client = run_broadtape_probe(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        seconds=args.seconds,
        specs=args.specs,
        market_data_type=args.market_data_type,
    )
    for event in client.events[:20]:
        print("tick_news", event.source, event.provider, event.article_id, event.headline[:120])


if __name__ == "__main__":
    main()
