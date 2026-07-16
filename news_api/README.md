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
├─ verified_news_monitor.py # ib_async 实时适配、刷新证明和审计日志
├─ live_news_monitor.py     # 实盘 CLI 入口
├─ ib_client.py             # 旧版原生 ibapi 适配（离线兼容）
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

实盘入口使用项目已有的 `conda ib` 环境；新环境可先安装：

```bash
python -m pip install -r requirements_news.txt
```

离线规则测试：

```bash
python -m compileall news_api
python -m unittest -v test_verified_news_monitor.py
```

如果只想验证最基本的“指定股票监控”和“实时新闻触发推送”，优先打开：

```text
news_api/basic_function_test.ipynb
```

如果想逐模块验收，再打开：

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
$env:BARK_KEY="你的 Bark key"
$env:NEWS_DASHBOARD_URL="http://127.0.0.1:8501"
```

没有配置 `BARK_KEY` 时，模块会把推送状态记为 `skipped`，方便本地调试。

## 接入 IBKR 实时新闻

IB API 新闻分成两类，不能混用：

- 股票合约新闻：`reqMktData(stock_contract, "mdoff,292:...", ...)`，适合持仓或自选股。
- BroadTape：订阅 `NEWS` 合约，例如 `BRFG:BRFG_ALL`、`DJNL:DJNL_ALL`、`BZ:BZ_ALL`。这里的“全量”只表示该 provider 的整条新闻流，不等于所有媒体的全球新闻。

`live_news_monitor.py` 默认先调用 `reqNewsProviders()`，股票源使用账户实际返回的 provider；BroadTape 则逐个验证对应的 `*_ALL` 合约。不要把“请求已经发出”当成“订阅已经生效”。

### 防止“假刷新”

每次运行先保存历史基线，再把 IB 回调分为：

```text
SNAPSHOT  = article_id 已在订阅前的历史基线中
WARMUP    = 刚订阅时补发的旧标题
BACKFILL  = 发布时间早于订阅开始
LIVE      = 新 article_id，且发布时间达到订阅开始时间
DUPLICATE = 同一订阅重复收到相同 provider + article_id
```

只有 `LIVE` 会进入 SQLite/评分/推送流水线。每个心跳还会请求 IB 服务器时间；心跳时间变化只证明连接活着，绝不计为新闻刷新。所有回调及判断都写入 append-only JSONL 审计日志。

指定股票验收（历史接口每 60 秒交叉检查一次）：

```bash
conda run --no-capture-output -n ib python news_api/live_news_monitor.py \
  --mode portfolio --symbols NVDA,AAPL,TSLA --seconds 600 \
  --history-audit-symbol NVDA --history-poll-seconds 60
```

当前账户内所有可识别 BroadTape：

```bash
conda run --no-capture-output -n ib python news_api/live_news_monitor.py \
  --mode all --broadtape-providers auto --seconds 600
```

持续等到第一条真实新增并以退出码验收：

```bash
conda run --no-capture-output -n ib python news_api/live_news_monitor.py \
  --mode all --broadtape-providers BRFG+DJNL --seconds 1200 \
  --stop-on-live --require-live
```

### 判读结果

- `VERIFIED_LIVE`：本次运行至少收到一条可验证的新标题。
- `CONNECTED_BUT_NO_LIVE_REFRESH_PROVEN`：连接/心跳可能正常，但窗口内没有新标题；不能声称数据已刷新。
- `REJECTED`：合约、provider 或订阅权限被服务器拒绝。
- `DEGRADED_HISTORY_GAP`：历史接口已经出现新 article_id，但实时流在宽限期内没有收到，属于漏流告警。
- `code=200` 在合约发现阶段通常表示该 `NEWS` 合约不存在；订阅权限错误通常会以 `321/354/10089` 等错误落入对应订阅状态。

IBKR/TWS 侧需要确保：

- TWS 或 IB Gateway 已启动；
- API 端口正确，纸账户常见是 `4002`，实盘 Gateway 常见是 `4001`；
- 已订阅对应新闻权限；
- 实时订阅额度不要把 TWS 和 API 共享的 market data line 用满。

## 后续扩展

- 增加 `IBArticleFetcher`，把 `reqNewsArticle()` 接到 `ArticleFetcher` 协议；
- 将历史交叉检查扩展到多只 canary 股票，并增加自动重连后的缺口补拉；
- 接入大模型结构化 JSON 输出，覆盖或增强 `NewsAnalysis`；
- 增加网页看板和每小时摘要。
