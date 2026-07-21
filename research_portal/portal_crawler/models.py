from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ResearchItem:
    portal: str
    external_id: str
    subscription: str
    title: str
    summary: str
    published_date: str
    url: str
    pages: int | None
    authors: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["authors"] = list(self.authors)
        return payload


@dataclass(frozen=True)
class CollectionResult:
    items: tuple[ResearchItem, ...]
    scrolls: int
    stop_reason: str

