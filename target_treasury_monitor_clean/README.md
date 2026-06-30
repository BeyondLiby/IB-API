# Target Treasury Monitor Clean

这是对当前美债期权账户监控需求的第一版整理目录。旧代码暂时不删，这个目录先作为你核验用的干净入口层。

## 当前需求梳理

1. 账户 dashboard：连接 IB，读取目标账户持仓、账户资金、持仓行情、PnL、Greeks，并生成可看的账户风险表。
2. 期权链批量更新：按指定 ZF 底层期货月份，一次性刷新并保存静态合约、期权链快照、monitor frame。
3. 期权链实时监控：先发现关注范围内的近月/近 DTE/近价格合约，然后保持实时订阅，后续刷新只读 ticker 对象，减少重复请求。
4. 代码入口简洁：新目录只保留核心 workflow；旧代码里的调试脚本、notebook、历史 CSV/XLSX 等先不动。
5. 验证通过后再删：`news_api` 和 `prediction_market` 不纳入本次整理；其余无意义文件等你核验后再清理。

## 文件说明

```text
settings.py            统一配置 dataclass
ib_session.py          IB 连接和自动断开
account_dashboard.py   账户 dashboard 快照
chain_batch.py         期权链一次性批量刷新
chain_realtime.py      期权链实时订阅监控
app.py                 Streamlit 三页签入口
cli.py                 命令行入口
```

## Streamlit 看板

从仓库根目录运行：

```powershell
streamlit run .\target_treasury_monitor_clean\app.py --server.address 127.0.0.1 --server.port 8503
```

## CLI 示例

账户快照：

```powershell
python -m target_treasury_monitor_clean.cli dashboard-snapshot --account U1234567 --market-data-type live
```

批量刷新 ZF 链：

```powershell
python -m target_treasury_monitor_clean.cli batch-chain --market-data-type delayed --months 202609,202612
```

第一次运行会向 IB 发现并确认有效合约，生成 `*_contracts.csv`。之后相同 `root/months/min_expiration/max_expiration/output_dir` 会默认复用这个合约缓存，跳过合约发现和 qualify，只重新拉行情。

强制重建有效合约缓存：

```powershell
python -m target_treasury_monitor_clean.cli batch-chain --market-data-type delayed --months 202609,202612 --rebuild-contract-cache
```

行情批量参数建议从稳妥值开始：

```text
batch_size=100-150
request_interval=0.02-0.03
wait_max_seconds=8-12
wait_stable_seconds=1-2
inter_batch_pause_seconds=1-2
```

IB 没有提供“1830 个期权一次性返回一张行情表”的接口；这里的批量是代码逐个 `reqMktData` 订阅、等待、读取、取消，再进入下一批。`request_interval` 太小或 `batch_size` 太大时，后续批次可能被 pacing 或订阅线路释放延迟影响，出现整批 quote/Greeks 为空。

默认在订阅行情前会过滤有效合约池：

```text
DTE <= 7: 只订阅距离对应期货价格 1 点以内的 call/put
DTE > 7 : 只订阅距离对应期货价格 3 点以内的 call/put
```

全量有效合约仍保存在 `*_contracts.csv`；实际订阅的子集另存为 `*_selected_contracts.csv`。如果确实需要订阅全链，可在 CLI 加 `--no-market-data-filter`，或在 notebook 里设置 `filter_market_data_by_moneyness=False`。

`monitor_frame` 会增加质量标签：

```text
ok
ask_only_tiny
no_quote_far_otm
no_quote_0dte
no_quote
missing_greeks
greeks_only
partial
```

实时监控关注链：

```powershell
python -m target_treasury_monitor_clean.cli live-chain --market-data-type live --months 202609,202612 --poll-seconds 1
```

## 当前仍复用的旧代码

为了避免重写后引入不必要风险，这一版仍复用：

- `target_treasury_account_monitor`：账户持仓、Greeks、展示 frame、现有静态链发现逻辑。
- `treasury_fop_chain.py`：IB 期权链发现、批量订阅、ticker 转表、成交量差分事件。

下一步验证通过后，可以把这些底层函数迁入本目录，再删除旧 notebook、旧 dashboard、历史快照和 `__pycache__` 等无意义内容。
