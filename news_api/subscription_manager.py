from __future__ import annotations

from itertools import count

from .ib_client import IBNewsClient, broadtape_news_contract, stock_contract


class SubscriptionManager:
    """统一管理所有股票订阅，避免每只股票单独跑进程。"""

    def __init__(self, client: IBNewsClient, start_ticker_id: int = 7000) -> None:
        self.client = client
        self._ids = count(start_ticker_id)

    def subscribe_watchlist(
        self,
        watchlist: dict[str, dict],
        provider_codes: str,
    ) -> dict[str, int]:
        """对 P0/P1 股票建立实时新闻标题订阅。"""
        result: dict[str, int] = {}
        generic_ticks = f"mdoff,292:{provider_codes}"

        for symbol, item in watchlist.items():
            if int(item.get("priority", 1)) > 1:
                continue

            ticker_id = next(self._ids)
            contract = stock_contract(
                symbol,
                item.get("exchange", "SMART"),
                currency=item.get("currency", "USD"),
                sec_type=item.get("sec_type", "STK"),
            )
            self.client.ticker_id_to_symbol[ticker_id] = symbol
            self.client.reqMktData(
                ticker_id,
                contract,
                generic_ticks,
                False,
                False,
                [],
            )
            result[symbol] = ticker_id

        return result

    def subscribe_broadtape(
        self,
        provider: str,
        *,
        symbol_alias: str = "ALL",
        contract_symbol: str | None = None,
    ) -> int:
        """订阅某个新闻源的 BroadTape 全量新闻流。"""
        ticker_id = next(self._ids)
        contract = broadtape_news_contract(provider, contract_symbol)
        self.client.ticker_id_to_symbol[ticker_id] = symbol_alias
        self.client.reqMktData(
            ticker_id,
            contract,
            "mdoff,292",
            False,
            False,
            [],
        )
        return ticker_id
