from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


def parse_ib_time(value: str, local_timezone: str = "Asia/Shanghai") -> tuple[str, str]:
    """把 IBKR 新闻时间转成 UTC ISO 和本地 ISO。解析失败时原样返回。"""
    raw = (value or "").strip()
    formats = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")

    for fmt in formats:
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            return dt.isoformat(), dt.astimezone(ZoneInfo(local_timezone)).isoformat()
        except ValueError:
            continue

    return raw, raw


def parse_headline_metadata(raw_headline: str) -> dict[str, Any]:
    """解析 IBKR 标题前缀，例如 {A:800015:L:en}。"""
    decoded = html.unescape(raw_headline or "").strip()
    blocks = re.findall(r"^\{([^{}]+)\}", decoded)
    metadata: dict[str, str] = {}

    for block in blocks:
        parts = block.split(":")
        for index in range(0, len(parts) - 1, 2):
            metadata[parts[index]] = parts[index + 1]

    cleaned = re.sub(r"^(?:\{[^{}]*\})+", "", decoded).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)

    headline = cleaned
    publisher = ""
    if " -- " in cleaned:
        headline_part, publisher_part = cleaned.rsplit(" -- ", 1)
        if headline_part.strip() and publisher_part.strip():
            headline = headline_part.strip()
            publisher = publisher_part.strip()

    return {
        "headline": headline,
        "publisher": publisher,
        "language": metadata.get("L", ""),
        "metadata": metadata,
    }


def clean_headline(value: str) -> str:
    """清洗标题，用于展示和规则匹配。"""
    parsed = parse_headline_metadata(value)
    headline = parsed["headline"]
    headline = re.sub(r"\s+", " ", headline)
    return headline.strip()


def normalize_text(value: str) -> str:
    """规范化文本，用于去重和关键词匹配。"""
    text = html.unescape(value or "").lower()
    text = re.sub(r"^(?:\{[^{}]*\})+", "", text)
    text = re.sub(r"\s+--\s+.+$", "", text)
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def clean_article_text(raw_html: str) -> str:
    """轻量正文清洗。为了减少依赖，这里不用 BeautifulSoup。"""
    if not raw_html:
        return ""

    text = html.unescape(raw_html)
    text = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", text)
    text = re.sub(r"(?s)<br\s*/?>", "\n", text)
    text = re.sub(r"(?s)</p\s*>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)

    lines: list[str] = []
    previous = ""
    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line or line == previous:
            continue
        if re.search(r"^\(END\)|^Copyright \(c\)", line, re.IGNORECASE):
            break
        lines.append(line)
        previous = line

    return "\n\n".join(lines)
