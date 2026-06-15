from __future__ import annotations

import queue
import threading
from typing import Any

from .article_fetcher import ArticleFetcher, NoopArticleFetcher
from .bark_client import BarkClient
from .cleaner import clean_headline, parse_headline_metadata
from .config import SETTINGS, NewsSettings
from .deduplicator import StoryDeduplicator
from .importance_scorer import score_news
from .models import NewsHeadline
from .storage import SQLiteNewsStorage
from .watchlist import normalize_watchlist


class NewsService:
    """标题监听 -> 正文补全 -> 事件识别 -> 重要性评分 -> Bark 推送。"""

    def __init__(
        self,
        *,
        settings: NewsSettings = SETTINGS,
        watchlist: dict[str, dict[str, Any]] | None = None,
        storage: SQLiteNewsStorage | None = None,
        article_fetcher: ArticleFetcher | None = None,
        bark_client: BarkClient | None = None,
    ) -> None:
        self.settings = settings
        self.watchlist = normalize_watchlist(watchlist)
        self.storage = storage or SQLiteNewsStorage(settings.db_path)
        self.article_fetcher = article_fetcher or NoopArticleFetcher()
        self.bark_client = bark_client or BarkClient(
            key=settings.bark_key,
            base_url=settings.bark_base_url,
            dashboard_url=settings.dashboard_url,
        )

        self.queue: queue.Queue[NewsHeadline] = queue.Queue()
        self.story_dedup = StoryDeduplicator()
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

    def start(self) -> None:
        """启动后台处理线程。IB 回调只需要调用 ingest_tick_news。"""
        if self._worker and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._run_worker, daemon=True)
        self._worker.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker:
            self._worker.join(timeout=5)

    def ingest_tick_news(
        self,
        *,
        symbol: str,
        timestamp: str,
        provider: str,
        article_id: str,
        headline: str,
        ticker_id: int | None = None,
        extra_data: str = "",
    ) -> bool:
        """IB tickNews 回调入口：只清洗、入库、入队，不做重活。"""
        symbol = symbol.upper()
        parsed = parse_headline_metadata(headline)
        event = NewsHeadline(
            symbol=symbol,
            provider=provider,
            article_id=article_id,
            headline=clean_headline(headline),
            headline_raw=headline,
            publisher=parsed["publisher"],
            published_at=str(timestamp),
            ticker_id=ticker_id,
            extra_data=extra_data,
        )
        return self.ingest_headline(event)

    def ingest_headline(self, event: NewsHeadline) -> bool:
        if event.symbol not in self.watchlist:
            return False

        inserted = self.storage.save_raw(event)
        if not inserted:
            return False

        self.queue.put(event)
        self.storage.set_state(f"last_seen:{event.symbol}", event.published_at)
        return True

    def process_one(self, event: NewsHeadline) -> None:
        item = self.watchlist[event.symbol]
        priority = int(item.get("priority", 1))
        aliases = item.get("aliases", [event.symbol])
        push_score = (
            self.settings.portfolio_push_score
            if priority == 0
            else self.settings.default_push_score
        )

        analysis = score_news(
            event,
            aliases,
            article_fetch_score=self.settings.article_fetch_score,
            push_score=push_score,
        )

        if analysis.should_fetch_article:
            article = self.article_fetcher.fetch(event)
            self.storage.save_article(article)
            if article.article_text:
                analysis = score_news(
                    event,
                    aliases,
                    article_text=article.article_text,
                    article_fetch_score=self.settings.article_fetch_score,
                    push_score=push_score,
                )

        story_id, is_new_story = self.story_dedup.assign_story_id(event)
        analysis.story_id = story_id
        if not is_new_story:
            analysis.should_push = False
            analysis.reason_important = "疑似同一滚动新闻，已合并到已有 story。"

        self.storage.save_event(analysis)

        if analysis.should_push:
            status, response = self.bark_client.push(analysis, priority)
            self.storage.save_push_log(event.unique_key, "bark", status, response)

    def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                event = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                self.process_one(event)
            finally:
                self.queue.task_done()
