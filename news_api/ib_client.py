from __future__ import annotations

import threading
from typing import Any

from .cleaner import (
    clean_article_text,
    clean_headline,
    parse_headline_metadata,
    parse_ib_time,
)
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


class _PendingArticle:
    def __init__(self, provider: str, article_id: str, publisher: str) -> None:
        self.provider = provider
        self.article_id = article_id
        self.publisher = publisher
        self.done = threading.Event()
        self.content: ArticleContent | None = None


class IBNewsClient(EWrapper, EClient):  # type: ignore[misc]
    """IBKR 适配层：把 tickNews 转成 NewsService 的入队事件。"""

    def __init__(self, service: NewsService) -> None:
        if Contract is None:
            raise RuntimeError("未安装 ibapi，请先安装 Interactive Brokers Python API。")
        EClient.__init__(self, self)
        self.service = service
        self.service.article_fetcher = self
        self.ticker_id_to_symbol: dict[int, str] = {}
        self._api_thread: threading.Thread | None = None
        self._ready = threading.Event()
        self.news_providers: list[tuple[str, str]] = []

        self._lock = threading.Lock()
        self._next_req_id = 9000
        self._contract_candidates: dict[int, list[Any]] = {}
        self._contract_requests: dict[int, dict[str, Any]] = {}
        self._historical_requests: dict[int, dict[str, Any]] = {}
        self._article_requests: dict[int, _PendingArticle] = {}

    def _next_id(self) -> int:
        with self._lock:
            req_id = self._next_req_id
            self._next_req_id += 1
            return req_id

    def start_api(
        self,
        host: str,
        port: int,
        client_id: int,
        *,
        timeout: float = 15.0,
        request_providers: bool = True,
    ) -> None:
        self.service.start()
        self.connect(host, port, clientId=client_id)
        self._api_thread = threading.Thread(target=self.run, daemon=True)
        self._api_thread.start()
        if not self._ready.wait(timeout=timeout):
            raise TimeoutError(
                f"IB API 已调用 connect，但 {timeout} 秒内没有收到 nextValidId。"
                "请检查 TWS/Gateway API 端口、clientId 是否冲突，以及 API 是否启用。"
            )
        if request_providers:
            self.reqNewsProviders()

    def stop_api(self) -> None:
        if self.isConnected():
            self.disconnect()
        self.service.stop()

    def wait_until_ready(self, timeout: float = 10.0) -> bool:
        return self._ready.wait(timeout=timeout)

    def nextValidId(self, orderId: int) -> None:  # noqa: N802 - IB API callback
        self._ready.set()

    def newsProviders(self, newsProviders: list[Any]) -> None:  # noqa: N802
        self.news_providers = [
            (
                str(getattr(provider, "code", "")),
                str(getattr(provider, "name", "")),
            )
            for provider in newsProviders
        ]

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

    def fetch(self, headline: NewsHeadline) -> ArticleContent:
        """同步补正文，供 NewsService 后台 worker 调用。"""
        if not self.isConnected():
            return ArticleContent(
                provider=headline.provider,
                article_id=headline.article_id,
                publisher=headline.publisher,
                fetch_status="error",
                error="IB API 未连接，无法 reqNewsArticle。",
            )

        req_id = self._next_id()
        pending = _PendingArticle(
            headline.provider,
            headline.article_id,
            headline.publisher,
        )
        self._article_requests[req_id] = pending
        self.reqNewsArticle(
            reqId=req_id,
            providerCode=headline.provider,
            articleId=headline.article_id,
            newsArticleOptions=[],
        )

        if not pending.done.wait(timeout=20.0):
            self._article_requests.pop(req_id, None)
            return ArticleContent(
                provider=headline.provider,
                article_id=headline.article_id,
                publisher=headline.publisher,
                fetch_status="timeout",
                error="reqNewsArticle 20 秒内没有返回。",
            )

        return pending.content or ArticleContent(
            provider=headline.provider,
            article_id=headline.article_id,
            publisher=headline.publisher,
            fetch_status="error",
            error="reqNewsArticle 返回为空。",
        )

    def newsArticle(  # noqa: N802
        self,
        reqId: int,
        articleType: int,
        articleText: str,
    ) -> None:
        pending = self._article_requests.pop(reqId, None)
        if pending is None:
            return

        if articleType == 0:
            article_html = articleText or ""
            article_text = clean_article_text(articleText)
        else:
            article_html = ""
            article_text = articleText or ""
        pending.content = ArticleContent(
            provider=pending.provider,
            article_id=pending.article_id,
            publisher=pending.publisher,
            article_html=article_html,
            article_text=article_text,
            fetch_status="ok",
        )
        pending.done.set()

    def request_historical_news(
        self,
        symbol: str,
        primary_exchange: str,
        provider_codes: str,
        *,
        total_results: int = 100,
        start_datetime: str = "",
        end_datetime: str = "",
    ) -> int:
        """按股票补拉历史标题；返回合约查询 reqId。"""
        if not self.wait_until_ready(timeout=10.0):
            raise TimeoutError("IB API 尚未就绪，不能请求历史新闻。")

        req_id = self._next_id()
        self._contract_candidates[req_id] = []
        self._contract_requests[req_id] = {
            "symbol": symbol.upper(),
            "primary_exchange": primary_exchange,
            "provider_codes": provider_codes,
            "total_results": total_results,
            "start_datetime": start_datetime,
            "end_datetime": end_datetime,
        }
        self.reqContractDetails(req_id, stock_contract(symbol, primary_exchange))
        return req_id

    def request_watchlist_historical_news(
        self,
        watchlist: dict[str, dict[str, Any]],
        provider_codes: str,
        *,
        total_results: int = 100,
    ) -> dict[str, int]:
        result: dict[str, int] = {}
        for symbol, item in watchlist.items():
            if int(item.get("priority", 1)) > 1:
                continue
            result[symbol] = self.request_historical_news(
                symbol,
                item.get("exchange", "SMART"),
                provider_codes,
                total_results=total_results,
            )
        return result

    def contractDetails(self, reqId: int, contractDetails: Any) -> None:  # noqa: N802
        if reqId in self._contract_candidates:
            self._contract_candidates[reqId].append(contractDetails)

    def contractDetailsEnd(self, reqId: int) -> None:  # noqa: N802
        request = self._contract_requests.pop(reqId, None)
        candidates = self._contract_candidates.pop(reqId, [])
        if request is None:
            return
        if not candidates:
            print(f"IBKR news: 未找到合约 {request['symbol']}")
            return

        selected = candidates[0]
        expected_exchange = str(request["primary_exchange"]).upper()
        for candidate in candidates:
            contract = candidate.contract
            primary_exchange = str(getattr(contract, "primaryExchange", "")).upper()
            if expected_exchange != "SMART" and primary_exchange == expected_exchange:
                selected = candidate
                break

        contract = selected.contract
        con_id = int(contract.conId)
        historical_req_id = self._next_id()
        self._historical_requests[historical_req_id] = {
            "symbol": request["symbol"],
            "con_id": con_id,
        }
        self.reqHistoricalNews(
            reqId=historical_req_id,
            conId=con_id,
            providerCodes=request["provider_codes"],
            startDateTime=request["start_datetime"],
            endDateTime=request["end_datetime"],
            totalResults=int(request["total_results"]),
            historicalNewsOptions=[],
        )

    def historicalNews(  # noqa: N802
        self,
        reqId: int,
        time: str,
        providerCode: str,
        articleId: str,
        headline: str,
    ) -> None:
        request = self._historical_requests.get(reqId)
        if request is None:
            return

        parsed = parse_headline_metadata(headline)
        published_at, _local_time = parse_ib_time(time)
        event = NewsHeadline(
            symbol=request["symbol"],
            provider=providerCode,
            article_id=articleId,
            headline=clean_headline(headline),
            headline_raw=headline,
            publisher=parsed["publisher"],
            published_at=published_at,
            con_id=request["con_id"],
        )
        self.service.ingest_headline(event)

    def historicalNewsEnd(self, reqId: int, hasMore: bool) -> None:  # noqa: N802
        request = self._historical_requests.pop(reqId, None)
        if request is not None:
            self.service.storage.set_state(
                f"historical_done:{request['symbol']}",
                {"req_id": reqId, "has_more": bool(hasMore)},
            )

    def error(self, *args: Any) -> None:
        """兼容不同 ibapi 版本的 error 回调签名。"""
        if len(args) >= 4 and isinstance(args[1], int) and isinstance(args[2], int):
            reqId, _errorTime, errorCode, errorString = args[:4]
        elif len(args) >= 3:
            reqId, errorCode, errorString = args[:3]
        else:
            print(f"IBKR error: {args}")
            return

        if int(errorCode) in STATUS_CODES:
            return

        pending = self._article_requests.pop(int(reqId), None)
        if pending is not None:
            pending.content = ArticleContent(
                provider=pending.provider,
                article_id=pending.article_id,
                publisher=pending.publisher,
                fetch_status="error",
                error=f"{errorCode}: {errorString}",
            )
            pending.done.set()
            return

        print(f"IBKR error reqId={reqId} code={errorCode}: {errorString}")


def stock_contract(symbol: str, primary_exchange: str = "") -> Any:
    """创建美股 STK 合约。"""
    if Contract is None:
        raise RuntimeError("未安装 ibapi，无法创建 Contract。")
    contract = Contract()
    contract.symbol = symbol.upper()
    contract.secType = "STK"
    contract.exchange = "SMART"
    contract.currency = "USD"
    if primary_exchange and primary_exchange.upper() != "SMART":
        contract.primaryExchange = primary_exchange
    return contract
