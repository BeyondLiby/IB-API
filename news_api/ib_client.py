from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any

from .cleaner import clean_article_text
from .models import ArticleContent, NewsHeadline
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
        self.news_providers: list[dict[str, str]] = []
        self.errors: list[dict[str, Any]] = []
        self._api_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.next_order_id: int | None = None
        self._next_article_req_id = 900000
        self._article_lock = threading.Lock()
        self._article_waiters: dict[int, dict[str, Any]] = {}

    def start_api(
        self,
        host: str,
        port: int,
        client_id: int,
        *,
        wait_ready_seconds: float = 15.0,
    ) -> None:
        self.service.start()
        self.connect(host, port, clientId=client_id)
        self._api_thread = threading.Thread(target=self.run, daemon=True)
        self._api_thread.start()
        if not self.wait_until_ready(wait_ready_seconds):
            raise TimeoutError(
                "IB API handshake did not finish. "
                "Check TWS/Gateway port, API setting, and client_id."
            )

    def stop_api(self) -> None:
        if self.isConnected():
            self.disconnect()
        self.service.stop()

    def nextValidId(self, orderId: int) -> None:
        self.next_order_id = orderId
        self._ready.set()

    def wait_until_ready(self, timeout: float = 15.0) -> bool:
        """Wait until nextValidId arrives so serverVersion is available."""
        return self._ready.wait(timeout=timeout)

    def newsProviders(self, newsProviders: list[Any]) -> None:
        """兼容不同 ibapi 版本的新闻源字段名。"""
        self.news_providers = []
        for provider in newsProviders:
            code = (
                getattr(provider, "providerCode", None)
                or getattr(provider, "code", None)
                or ""
            )
            name = (
                getattr(provider, "providerName", None)
                or getattr(provider, "name", None)
                or ""
            )
            self.news_providers.append(
                {
                    "code": str(code),
                    "name": str(name),
                    "raw": repr(provider),
                }
            )

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

    def newsArticle(
        self,
        reqId: int,
        articleType: int,
        articleText: str,
    ) -> None:
        waiter = self._article_waiters.get(reqId)
        if waiter is None:
            return

        waiter["article_type"] = articleType
        waiter["article_html"] = articleText or ""
        waiter["article_text"] = (
            clean_article_text(articleText)
            if articleType == 0
            else articleText or ""
        )
        waiter["status"] = "ok"
        waiter["event"].set()

    def fetch_article_sync(
        self,
        provider: str,
        article_id: str,
        *,
        timeout: float = 15.0,
    ) -> ArticleContent:
        """同步读取正文，给 NewsService 的 ArticleFetcher 使用。"""
        with self._article_lock:
            req_id = self._next_article_req_id
            self._next_article_req_id += 1

        done = threading.Event()
        self._article_waiters[req_id] = {
            "event": done,
            "status": "timeout",
            "article_html": "",
            "article_text": "",
            "article_type": None,
            "error": "",
        }

        self.reqNewsArticle(
            reqId=req_id,
            providerCode=provider,
            articleId=article_id,
            newsArticleOptions=[],
        )

        done.wait(timeout=timeout)
        waiter = self._article_waiters.pop(req_id, None) or {}
        status = waiter.get("status", "timeout")
        error = waiter.get("error", "")

        if status == "timeout":
            error = f"reqNewsArticle timeout after {timeout}s"

        return ArticleContent(
            provider=provider,
            article_id=article_id,
            article_html=waiter.get("article_html", ""),
            article_text=waiter.get("article_text", ""),
            fetch_status=status,
            error=error,
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

        self.errors.append(
            {
                "req_id": reqId,
                "code": errorCode,
                "message": errorString,
            }
        )

        waiter = self._article_waiters.get(reqId)
        if waiter is not None:
            waiter["status"] = "error"
            waiter["error"] = f"{errorCode}: {errorString}"
            waiter["event"].set()
            return

        print(f"IBKR error reqId={reqId} code={errorCode}: {errorString}")


def stock_contract(
    symbol: str,
    primary_exchange: str = "",
    *,
    currency: str = "USD",
    sec_type: str = "STK",
) -> Any:
    """创建美股 STK 合约。"""
    if Contract is None:
        raise RuntimeError("未安装 ibapi，无法创建 Contract。")
    contract = Contract()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.exchange = "SMART"
    contract.currency = currency
    if primary_exchange and primary_exchange.upper() != "SMART":
        contract.primaryExchange = primary_exchange
    return contract


def broadtape_news_contract(
    provider: str,
    symbol: str | None = None,
) -> Any:
    """创建 BroadTape 新闻合约。

    BroadTape 合约和股票合约新闻不是一回事。IB 文档示例通常是
    BRF:BRF_ALL、BZ:BZ_ALL、FLY:FLY_ALL；BRFG/DJNL 更常用于股票合约的
    genericTickList，例如 "mdoff,292:BRFG+BRFUPDN+DJNL"。
    """
    if Contract is None:
        raise RuntimeError("未安装 ibapi，无法创建 Contract。")

    provider = provider.upper()
    contract = Contract()
    contract.symbol = symbol or f"{provider}:{provider}_ALL"
    contract.secType = "NEWS"
    contract.exchange = provider
    contract.currency = ""
    return contract


@dataclass(slots=True)
class IBArticleFetcher:
    """基于同一个 IB API 连接读取实时新闻正文。"""

    client: IBNewsClient
    timeout: float = 15.0

    def fetch(self, headline: NewsHeadline) -> ArticleContent:
        article = self.client.fetch_article_sync(
            headline.provider,
            headline.article_id,
            timeout=self.timeout,
        )
        article.publisher = headline.publisher
        return article
