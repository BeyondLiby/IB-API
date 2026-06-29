from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROVIDER_CODES = (
    "BRFG+BRFUPDN+DJ-N+DJ-RTA+DJ-RTE+DJ-RTG+DJ-RTPRO+DJNL"
)

# 股票合约新闻：用于 reqMktData(stock_contract, "mdoff,292:...")。
DEFAULT_CONTRACT_NEWS_PROVIDER_CODES = "BRFG+BRFUPDN+DJNL"

# 全市场 BroadTape：用于 NEWS 合约，例如 BZ:BZ_ALL、FLY:FLY_ALL。
DEFAULT_BROADTAPE_PROVIDER_CODES = "BRF+BZ+FLY"

# 兼容旧代码；新代码应优先使用上面两个更明确的配置。
DEFAULT_REALTIME_PROVIDER_CODES = DEFAULT_CONTRACT_NEWS_PROVIDER_CODES


def split_provider_codes(value: str) -> list[str]:
    """支持用 + 或逗号分隔 provider code。"""
    normalized = value.replace(",", "+")
    return [item.strip() for item in normalized.split("+") if item.strip()]


@dataclass(slots=True)
class NewsSettings:
    """运行参数集中放在这里，避免散落在业务代码里。"""

    host: str = "127.0.0.1"
    port: int = 4001
    client_id: int = 91
    provider_codes: str = DEFAULT_CONTRACT_NEWS_PROVIDER_CODES
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
