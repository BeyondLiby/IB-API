from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROVIDER_CODES = (
    "BRFG+BRFUPDN+DJ-N+DJ-RTA+DJ-RTE+DJ-RTG+DJ-RTPRO+DJNL"
)

DEFAULT_BROADTAPE_SPECS = (
    "BRF:BRF_ALL@BRF",
    "BZ:BZ_ALL@BZ",
    "FLY:FLY_ALL@FLY",
)


def _env_tuple(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    if not raw.strip():
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(slots=True)
class NewsSettings:
    """运行参数集中放在这里，避免散落在业务代码里。"""

    host: str = "127.0.0.1"
    port: int = 4001
    client_id: int = 91
    provider_codes: str = DEFAULT_PROVIDER_CODES
    market_data_type: int = int(os.getenv("NEWS_MARKET_DATA_TYPE", "3"))
    broadtape_specs: tuple[str, ...] = _env_tuple(
        "NEWS_BROADTAPE_SPECS",
        DEFAULT_BROADTAPE_SPECS,
    )
    local_timezone: str = "Asia/Shanghai"

    db_path: Path = Path(__file__).resolve().parent / "data" / "news.sqlite"
    history_buffer_minutes: int = 5
    max_historical_results: int = 300

    # 标题规则分超过该值才补正文，避免每条新闻都请求 article。
    article_fetch_score: int = 40
    default_push_score: int = 70
    portfolio_push_score: int = 60

    bark_key: str = os.getenv("BARK_KEY", "")
    bark_base_url: str = os.getenv("BARK_BASE_URL", "https://api.day.app")
    dashboard_url: str = os.getenv("NEWS_DASHBOARD_URL", "")


SETTINGS = NewsSettings()
