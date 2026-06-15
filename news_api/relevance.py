from __future__ import annotations

from .cleaner import normalize_text


def score_relevance(
    symbol: str,
    aliases: list[str],
    headline: str,
    article_text: str = "",
) -> int:
    """按股票代码、公司名和正文出现频率计算 0-30 的相关性。"""
    headline_norm = normalize_text(headline)
    body_norm = normalize_text(article_text)
    score = 0

    if symbol.lower() in headline_norm.split():
        score += 20

    for alias in aliases:
        alias_norm = normalize_text(alias)
        if alias_norm and alias_norm in headline_norm:
            score += 20
            break

    body_hits = 0
    for alias in aliases:
        alias_norm = normalize_text(alias)
        if alias_norm:
            body_hits += body_norm.count(alias_norm)

    if body_hits >= 5:
        score += 10
    elif body_hits == 1:
        score += 2
    elif body_hits > 1:
        score += 5

    return min(score, 30)
