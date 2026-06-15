from __future__ import annotations

import threading
from typing import Any

from .service import NewsService

try:
    from ibapi.client import EClient
    from ibapi.contract import Contract
    from ibapi.wrapper import EWrapper
except ImportError:  # 允许 notebook 在未安装 ibapi 时验证规则层。
    class EClient:  # type: ignore[no-redef]
        pass

    class EWrapper:  # type: ignore[no-redef]
        pass

    Contract = None  # type: ignore[assignment]


STATUS_CODES = {2104, 2106, 2107, 2108, 2158}


class IBNewsClient(EWrapper, EClient):  # type: ignore[misc]
    """IBKR 适配层：把 tickNews 转成 NewsService 的入队事件。"""

    def __init__(self, service: NewsService) -> None:
        if Contract is None:
            raise RuntimeError("未安装 ibapi，请先安装 Interactive Brokers Python API。")
        EClient.__init__(self, self)
        self.service = service
        self.ticker_id_to_symbol: dict[int, str] = {}
        self._api_thread: threading.Thread | None = None

    def start_api(self, host: str, port: int, client_id: int) -> None:
        self.service.start()
        self.connect(host, port, clientId=client_id)
        self._api_thread = threading.Thread(target=self.run, daemon=True)
        self._api_thread.start()

    def stop_api(self) -> None:
        if self.isConnected():
            self.disconnect()
        self.service.stop()

    def tickNews(
        self,
        tickerId: int,
        timeStamp: int,
        providerCode: str,
        articleId: str,
        headline: str,
        extraData: str,
    ) -> None:
        symbol = self.ticker_id_to_symbol.get(tickerId)
        if not symbol:
            return
        self.service.ingest_tick_news(
            symbol=symbol,
            timestamp=str(timeStamp),
            provider=providerCode,
            article_id=articleId,
            headline=headline,
            ticker_id=tickerId,
            extra_data=extraData,
        )

    def error(
        self,
        reqId: int,
        errorTime: int,
        errorCode: int,
        errorString: str,
        advancedOrderRejectJson: str = "",
    ) -> None:
        if errorCode in STATUS_CODES:
            return
        print(f"IBKR error reqId={reqId} code={errorCode}: {errorString}")


def stock_contract(symbol: str, primary_exchange: str) -> Any:
    """创建美股 STK 合约。"""
    if Contract is None:
        raise RuntimeError("未安装 ibapi，无法创建 Contract。")
    contract = Contract()
    contract.symbol = symbol
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    contract.primaryExchange = primary_exchange
    return contract
