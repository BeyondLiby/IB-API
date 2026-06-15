from __future__ import annotations

import hashlib
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Deque

from .cleaner import normalize_text
from .models import NewsHeadline


def headline_similarity(left: str, right: str) -> float:
    """标题相似度，用于滚动新闻故事级去重。"""
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    if not left_norm or not right_norm:
        return 0.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


@dataclass(slots=True)
class StoryCandidate:
    symbol: str
    provider: str
    headline: str
    story_id: str


class StoryDeduplicator:
    """内存版故事去重；进程重启后的强去重由 SQLite 的 provider+article_id 负责。"""

    def __init__(self, max_items: int = 500, threshold: float = 0.85) -> None:
        self.max_items = max_items
        self.threshold = threshold
        self._items: Deque[StoryCandidate] = deque(maxlen=max_items)

    def assign_story_id(self, event: NewsHeadline) -> tuple[str, bool]:
        """返回 story_id 和是否为新故事。"""
        for item in self._items:
            if item.symbol != event.symbol or item.provider != event.provider:
                continue
            if headline_similarity(item.headline, event.headline) >= self.threshold:
                return item.story_id, False

        raw = f"{event.symbol}:{event.provider}:{normalize_text(event.headline)}"
        suffix = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
        story_id = f"{event.symbol}_{event.provider}_{suffix}"
        self._items.append(
            StoryCandidate(
                symbol=event.symbol,
                provider=event.provider,
                headline=event.headline,
                story_id=story_id,
            )
        )
        return story_id, True
