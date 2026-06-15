from __future__ import annotations

from typing import Protocol

from .models import ArticleContent, NewsHeadline


class ArticleFetcher(Protocol):
    """正文读取接口。IB 实现和测试假实现都遵守这个协议。"""

    def fetch(self, headline: NewsHeadline) -> ArticleContent:
        ...


class NoopArticleFetcher:
    """离线或尚未接 IB 时使用：不补正文，但保留状态。"""

    def fetch(self, headline: NewsHeadline) -> ArticleContent:
        return ArticleContent(
            provider=headline.provider,
            article_id=headline.article_id,
            publisher=headline.publisher,
            fetch_status="skipped",
        )
