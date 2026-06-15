from __future__ import annotations

from .cleaner import normalize_text


HIGH_PRIORITY_PATTERNS: dict[str, list[str]] = {
    "EARNINGS": ["earnings", "quarterly results", "eps", "revenue"],
    "GUIDANCE": [
        "raises guidance",
        "cuts guidance",
        "lowers outlook",
        "withdraws guidance",
    ],
    "MERGER": ["acquire", "acquisition", "merger", "takeover", "strategic review"],
    "FINANCING": ["offering", "convertible notes", "share sale", "debt offering"],
    "REGULATORY": ["investigation", "sec probe", "doj", "ftc", "approval", "rejected"],
    "MANAGEMENT": ["ceo resigns", "cfo resigns", "appoints ceo"],
    "CONTRACT": ["wins contract", "awarded contract", "government contract"],
    "ANALYST": ["upgraded", "downgraded", "price target", "initiates coverage"],
}

LOW_VALUE_PATTERNS = [
    "shareholder alert",
    "class action deadline",
    "encourages investors to contact",
    "reminds investors",
    "dow jones futures",
    "stock market today",
    "weekly review",
]

EVENT_BASE_SCORE = {
    "EARNINGS": 26,
    "GUIDANCE": 28,
    "MERGER": 25,
    "REGULATORY": 23,
    "CONTRACT": 22,
    "MANAGEMENT": 20,
    "FINANCING": 18,
    "ANALYST": 12,
    "OTHER": 4,
}


def classify_headline(headline: str, publisher: str = "") -> tuple[str, list[str], int]:
    """返回事件类型、命中的关键词、事件基础分。"""
    text = normalize_text(f"{headline} {publisher}")
    hits: list[str] = []

    for event_type, patterns in HIGH_PRIORITY_PATTERNS.items():
        matched = [pattern for pattern in patterns if pattern in text]
        if matched:
            return event_type, matched, EVENT_BASE_SCORE[event_type]

    low_hits = [pattern for pattern in LOW_VALUE_PATTERNS if pattern in text]
    if low_hits:
        # 律师广告类新闻保存但降权；真实 SEC/DOJ 调查在高优先级规则里已提前命中。
        return "LOW_VALUE", low_hits, 0

    return "OTHER", hits, EVENT_BASE_SCORE["OTHER"]
