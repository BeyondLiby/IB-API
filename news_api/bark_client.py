from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from .models import NewsAnalysis


@dataclass(slots=True)
class BarkClient:
    """Bark 只负责提醒，完整正文留在数据库或看板里。"""

    key: str = ""
    base_url: str = "https://api.day.app"
    dashboard_url: str = ""
    timeout: float = 8.0
    retries: int = 1
    retry_interval: float = 1.5

    def push(self, analysis: NewsAnalysis, priority: int) -> tuple[str, str]:
        if not self.key:
            return "skipped", "BARK_KEY 未配置"

        title = f"[P{priority}][{analysis.symbol}] {analysis.event_type}"
        body_lines = [
            analysis.headline,
            "",
            f"重要性：{analysis.importance_score}/100",
            f"情绪：{analysis.sentiment:+.2f}",
            "",
            *[f"- {item}" for item in analysis.summary_zh[:4]],
        ]
        payload = {
            "title": title,
            "body": "\n".join(body_lines),
            "group": "美股重要新闻",
            "isArchive": 1,
            "level": "timeSensitive" if analysis.importance_score >= 85 else "active",
        }
        if self.dashboard_url:
            payload["url"] = self.dashboard_url

        url = f"{self.base_url.rstrip('/')}/{self.key}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        last_error = ""
        for attempt in range(self.retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return "ok", response.read().decode("utf-8", errors="replace")
            except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    time.sleep(self.retry_interval)

        return "error", last_error
