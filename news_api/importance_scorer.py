from __future__ import annotations

from .event_classifier import classify_headline
from .models import NewsAnalysis, NewsHeadline
from .relevance import score_relevance


TRUSTED_SOURCE_KEYWORDS = {
    "dow jones": 10,
    "dj": 9,
    "reuters": 10,
    "barron": 8,
    "briefing": 7,
}

NEGATIVE_WORDS = ["cuts", "lowers", "resigns", "probe", "investigation", "rejected"]
POSITIVE_WORDS = ["raises", "wins", "beats", "approval", "upgraded", "acquire"]


def score_news(
    headline: NewsHeadline,
    aliases: list[str],
    *,
    article_text: str = "",
    article_fetch_score: int = 40,
    push_score: int = 70,
) -> NewsAnalysis:
    """本地规则评分，总分 100。后续可把大模型结果覆盖到同一结构。"""
    event_type, subtypes, event_score = classify_headline(
        headline.headline,
        headline.publisher,
    )
    relevance_score = score_relevance(
        headline.symbol,
        aliases,
        headline.headline,
        article_text,
    )

    source_text = f"{headline.provider} {headline.publisher}".lower()
    source_score = 5
    for keyword, value in TRUSTED_SOURCE_KEYWORDS.items():
        if keyword in source_text:
            source_score = value
            break

    market_impact_score = min(10, event_score // 3)
    novelty_score = 15 if event_type not in {"OTHER", "LOW_VALUE"} else 8
    importance = min(
        100,
        relevance_score + event_score + novelty_score + source_score + market_impact_score,
    )

    text = f"{headline.headline} {article_text}".lower()
    sentiment = 0.0
    if any(word in text for word in NEGATIVE_WORDS):
        sentiment -= 0.4
    if any(word in text for word in POSITIVE_WORDS):
        sentiment += 0.4

    summary = [headline.headline]
    reason = "命中本地新闻规则，建议进入重点处理。" if importance >= push_score else "分数未达推送阈值，仅保存。"

    return NewsAnalysis(
        symbol=headline.symbol,
        provider=headline.provider,
        article_id=headline.article_id,
        headline=headline.headline,
        event_type=event_type,
        event_subtypes=subtypes,
        relevance_score=relevance_score,
        event_score=event_score,
        novelty_score=novelty_score,
        source_score=source_score,
        market_impact_score=market_impact_score,
        importance_score=importance,
        sentiment=sentiment,
        summary_zh=summary,
        reason_important=reason,
        should_fetch_article=importance >= article_fetch_score,
        should_push=importance >= push_score,
    )
