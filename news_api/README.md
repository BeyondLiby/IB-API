# IBKR 新闻模块

这是一个面向 IBKR 新闻源的轻量流水线：

```text
实时标题监听 -> 正文补全 -> 事件识别 -> 重要性评分 -> Bark 推送
```

第一版重点不是做大全市场系统，而是先把 30-80 只重点股票稳定跑起来。P0/P1 股票走单股实时新闻订阅，P2 后续可以接 BroadTape 或历史新闻补拉。

## 目录结构

```text
news_api/
├─ config.py                # 运行配置和阈值
├─ watchlist.py             # 股票池和别名
├─ models.py                # 标题、正文、分析结果的数据结构
├─ cleaner.py               # 标题和正文清洗
├─ event_classifier.py      # 本地关键词事件分类
├─ relevance.py             # 股票相关性评分
├─ importance_scorer.py     # 重要性总分
├─ deduplicator.py          # 文章级和故事级去重
├─ storage.py               # SQLite 表结构和写入
├─ article_fetcher.py       # 正文补全接口
├─ bark_client.py           # Bark 推送
├─ service.py               # 流水线编排
├─ ib_client.py             # IB API 回调适配
├─ subscription_manager.py  # 统一订阅管理
└─ news_module_validation.ipynb
```

## 设计要点

1. 不为每只股票单独跑进程。`SubscriptionManager` 统一订阅，`NewsService` 统一处理队列。
2. `tickNews()` 只做轻量工作：清洗标题、保存原始记录、放入队列。
3. 先用本地规则判断是否值得读取正文，避免每条新闻都请求 `reqNewsArticle()`。
4. 去重分两层：`provider + article_id` 做强去重，标题相似度做故事级去重。
5. SQLite 保存四类信息：原始标题、正文、结构化事件、推送日志。
6. Bark 只推摘要和入口，不推完整正文。

## 快速离线校验

在项目父目录运行：

```powershell
python -m compileall news_api
python -m pip install ipykernel
```

然后打开：

```text
news_api/news_module_validation.ipynb
```

notebook 不需要连接 TWS，会用模拟新闻验证：

- 标题元数据清洗；
- 关键词事件识别；
- 重要性评分；
- SQLite 去重；
- `NewsService` 队列处理。

## 配置股票池

编辑 `watchlist.py`：

```python
WATCHLIST = {
    "ORCL": {
        "exchange": "NYSE",
        "priority": 0,
        "aliases": ["Oracle", "Oracle Corp", "OCI"],
    },
    "SMCI": {
        "exchange": "NASDAQ",
        "priority": 0,
        "aliases": ["Super Micro Computer", "Supermicro", "SMCI"],
    },
}
```

`priority` 建议这样用：

```text
0 = 当前持仓或期权仓位，低阈值推送
1 = 重点研究池，正常阈值推送
2 = 广泛观察池，后续更适合 BroadTape 或历史补拉
```

## Bark 推送

设置环境变量：

```powershell
$env:BARK_KEY="wF6yZEVtrVLqL7h2cnekwA"
$env:NEWS_DASHBOARD_URL="http://127.0.0.1:8501"
```

没有配置 `BARK_KEY` 时，模块会把推送状态记为 `skipped`，方便本地调试。

## 接入 IBKR 实时新闻

示例入口：

```python
from news_api.config import SETTINGS
from news_api.ib_client import IBNewsClient
from news_api.service import NewsService
from news_api.subscription_manager import SubscriptionManager
from news_api.watchlist import normalize_watchlist

service = NewsService(settings=SETTINGS)
client = IBNewsClient(service)
client.start_api(
    SETTINGS.host,
    SETTINGS.port,
    SETTINGS.client_id,
    timeout=15,
)

watchlist = normalize_watchlist()
manager = SubscriptionManager(client)
manager.subscribe_watchlist(watchlist, SETTINGS.provider_codes)
```

`start_api()` 会等待 IB 返回 `nextValidId` 后才返回；如果只调用了
`connect()` 但还没有等到 `nextValidId`，后续 `reqMktData()` 很容易被发早，
表现就是“API 已连接，但没有任何 tickNews 回来”。

如果你想先验证新闻权限和 provider codes 是否正常，可以在建立连接后补拉
历史新闻：

```python
client.request_watchlist_historical_news(
    watchlist,
    SETTINGS.provider_codes,
    total_results=50,
)
```

历史标题会进入同一套 `NewsService` 队列和 SQLite 表；如果历史新闻能入库但
实时新闻没回来，优先检查 market data line、TWS 新闻订阅权限和 provider
code；如果历史新闻也没有，优先看 `reqContractDetails`/`reqHistoricalNews`
对应的 IBKR error。

IBKR/TWS 侧需要确保：

- TWS 或 IB Gateway 已启动；
- API 端口正确，纸账户常见是 `4002`，实盘 Gateway 常见是 `4001`；
- 已订阅对应新闻权限；
- 实时订阅额度不要把 TWS 和 API 共享的 market data line 用满。

## 后续扩展

- 增加 `IBArticleFetcher`，把 `reqNewsArticle()` 接到 `ArticleFetcher` 协议；
- 增加 `reqHistoricalNews()` 启动补拉，使用 `news_state` 中的 `last_seen:{symbol}`；
- 接入大模型结构化 JSON 输出，覆盖或增强 `NewsAnalysis`；
- 增加网页看板和每小时摘要。
