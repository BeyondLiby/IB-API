from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(slots=True)
class NewsHeadline:
    """IB tickNews / historicalNews 统一后的标题事件。"""

    symbol: str
    provider: str
    article_id: str
    headline: str
    published_at: str
    received_at: str = field(default_factory=utc_now_iso)

    ticker_id: int | None = None
    con_id: int | None = None
    headline_raw: str = ""
    publisher: str = ""
    extra_data: str = ""

    @property
    def unique_key(self) -> str:
        return f"{self.provider}:{self.article_id}"


@dataclass(slots=True)
class ArticleContent:
    """正文补全结果。正文失败也会记录状态，便于后续排查。"""

    provider: str
    article_id: str
    article_text: str = ""
    article_html: str = ""
    publisher: str = ""
    fetch_status: str = "skipped"
    fetched_at: str = field(default_factory=utc_now_iso)
    error: str = ""


@dataclass(slots=True)
class NewsAnalysis:
    """规则层或模型层输出的结构化事件。"""

    symbol: str
    provider: str
    article_id: str
    headline: str

    event_type: str = "OTHER"
    event_subtypes: list[str] = field(default_factory=list)
    relevance_score: int = 0
    event_score: int = 0
    novelty_score: int = 10
    source_score: int = 5
    market_impact_score: int = 0
    importance_score: int = 0
    sentiment: float = 0.0

    summary_zh: list[str] = field(default_factory=list)
    reason_important: str = ""
    story_id: str = ""
    should_fetch_article: bool = False
    should_push: bool = False
