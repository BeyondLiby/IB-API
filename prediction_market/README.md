# IBKR Prediction Market / Event Contract Probe

这个小框架用于通过 IBKR TWS/IB Gateway 探测 ForecastTrader/ForecastEx 事件合约，并短暂订阅行情来观察报价和流动性。

## 结论

IBKR 平台端有 ForecastTrader/ForecastEx 事件合约。TWS API 文档把 ForecastEx 合约建模为 `secType=OPT`、`exchange=FORECASTX`，注意不是 `EVENT`，也不是 `FORECASTEX`。IBKR 还说明 Market Scanner 不支持 Event Contracts，所以这里没有“全量 prediction market universe endpoint”；通常需要先从 ForecastTrader 发现 product symbol / localSymbol，再用 `reqContractDetails` 和 `reqMktData` 查询。

## 扫描事件合约

也可以直接打开 `prediction_market/prediction_market_probe.ipynb` 跑 notebook 测试；默认 `DRY_RUN = True`，不会连接 IBKR。

```powershell
cd E:\策略\IB-API
& 'C:\Users\Beyond\.conda\envs\pylib\python.exe' -m prediction_market.cli scan `
  --host 127.0.0.1 `
  --port 4002 `
  --client-id 601 `
  --market-data-type 1 `
  --out data/prediction_market_contracts.csv
```

用 IBKR 官方 ForecastEx 示例的精确字段测试：

```powershell
& 'C:\Users\Beyond\.conda\envs\pylib\python.exe' -m prediction_market.cli scan `
  --symbols GCE `
  --expirations 20251212 `
  --strikes 6395 `
  --rights C `
  --sec-types OPT `
  --exchanges FORECASTX `
  --out data/prediction_market_contracts.csv
```

如果你已经在 TWS 里看到了某些事件合约，可以把 `localSymbol` 放到 CSV：

```csv
localSymbol
YOUR_LOCAL_SYMBOL_HERE
```

然后精确探测：

```powershell
& 'C:\Users\Beyond\.conda\envs\pylib\python.exe' -m prediction_market.cli scan `
  --local-symbol-file data/event_local_symbols.csv `
  --symbols "" `
  --out data/prediction_market_contracts.csv
```

## 看报价和流动性

```powershell
& 'C:\Users\Beyond\.conda\envs\pylib\python.exe' -m prediction_market.cli quote `
  --contracts data/prediction_market_contracts.csv `
  --wait 5 `
  --out data/prediction_market_quotes.csv
```

输出字段包括：

- `bid` / `ask` / `mid` / `spread` / `spreadPct`
- `bidSize` / `askSize` / `topSize` / `notionalTop`
- `last` / `volume` / `tradeCount` / `tradeRate` / `volumeRate`
- `quoteStatus`：`top_of_book` 表示拿到 bid/ask；`reference_only` 表示只有 close/mark 等参考价，不能当成可交易盘口
- `liquidityScore`：简单排序指标，越高表示盘口和成交活跃度越好

## Notebook 测试流程

`prediction_market_probe.ipynb` 现在按三层测试：

1. 事件层：汇总已发现的 tradable event，例如 `GCE - Global Carbon Dioxide Emissions`。
2. 合约链：按 `expiry / strike / YES-NO` 展开该事件关联的所有 ForecastEx 合约。
3. 数据层：对 `SELECTED_EVENT_SYMBOL` 拉报价流动性，或用 `reqHistoricalData` 拉历史 bars。

如果报价只有 `close` 没有 `bid/ask`，这通常只是参考价或上一收盘概率，不代表当前有可交易流动性。

## 常见问题

- 扫描结果为空：先确认 TWS/Gateway 已登录、API enabled、端口正确、账户有 ForecastTrader/ForecastEx 权限；然后从 TWS 手工复制几个 `localSymbol` 用 `--local-symbol-file` 精确探测。
- IB 错误会写到 `data/prediction_market_ib_errors.csv`，重点看 `200` 合约不存在、`354` 无行情权限、`10197` 行情会话冲突。
- 若只想看延迟行情，可以把 `--market-data-type 3` 传给 CLI。
