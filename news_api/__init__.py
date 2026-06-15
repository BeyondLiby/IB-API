"""IBKR 新闻流水线模块。

第一版重点是：实时标题入队、SQLite 去重、规则评分、按阈值 Bark 推送。
真正连接 IBKR 的代码放在 ``ib_client`` 和 ``subscription_manager`` 中，便于离线测试核心逻辑。
"""

from .models import ArticleContent, NewsAnalysis, NewsHeadline
from .service import NewsService
from .storage import SQLiteNewsStorage

__all__ = [
    "ArticleContent",
    "NewsAnalysis",
    "NewsHeadline",
    "NewsService",
    "SQLiteNewsStorage",
]
