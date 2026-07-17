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
batch_size=50（请求值；运行时按常驻订阅和 100-line 预算自动缩小）
request_interval=0.02-0.03
wait_max_seconds=5
wait_stable_seconds=0.75
inter_batch_pause_seconds=0.5
```

单独运行 `batch-chain` 且没有其他行情订阅时可按账户 line 配额适当放大；planner server 会长期占用“当前持仓数 + 指定期货数”的行情线，因此 full refresh 请求值默认 50，实际使用 `min(请求值, 100 - 持仓线 - 期货线 - 5条预留)`，不会为提速暂停持仓流。

IB 没有提供“1830 个期权一次性返回一张行情表”的接口；这里的批量是代码逐个 `reqMktData` 订阅、等待、读取、取消，再进入下一批。full 只请求标准报价/option computations，并用报价和 model Greeks 判断一批是否稳定；planner 页面未使用的 OI/成交量 generic ticks 不再请求，避免部分权限账户产生逐合约 `10090`。`request_interval` 太小或 `batch_size` 太大时，后续批次仍可能被 pacing 或订阅线路释放延迟影响，出现整批 quote/Greeks 为空。

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

## Carry HTML 同步

`carry_risk_dashboard.html` 会自动读取仓库根目录下的稳定 CSV；当前按 `ZF` / `ZN` / `ZC` 三个资产识别和切换：

```text
data/carry_dashboard_positions.csv
data/carry_dashboard_chain.csv
data/carry_dashboard_bars.csv
```

建议通过本地 HTTP 打开页面，这样浏览器能稳定自动读取 `data/*.csv`：

```powershell
python -m target_treasury_monitor_clean.cli serve-carry-html `
  --directory . `
  --host 127.0.0.1 `
  --port 8765
```

如果端口被占用，可以改成其他端口，或用 `--port 0` 让系统自动分配；命令会打印实际 URL。

然后打开：

```text
http://127.0.0.1:8765/carry_risk_dashboard.html
```

从 notebook 或其他脚本生成 CSV 后，可以让 CLI 自动挑 `data/clean_verify` 下面最新的持仓和各资产 `*_monitor_frame.csv` 发布到 HTML：

```powershell
python -m target_treasury_monitor_clean.cli sync-latest-carry-html `
  --input-dir data/clean_verify `
  --output-dir data `
  --products ZF,ZN,ZC `
  --summary-only `
  --require-ready
```

自动发现规则：

```text
positions : data/clean_verify/dashboard_treasury_positions.csv
chain     : data/clean_verify/{ZF,ZN,ZC}_FOP_Static_*_monitor_frame.csv 中每个资产最新文件
bars      : 优先 data/clean_verify/carry_dashboard_bars.csv，否则复用 data/carry_dashboard_bars.csv
```

如果想手动指定 CSV，也可以发布到 HTML：

```powershell
python -m target_treasury_monitor_clean.cli sync-carry-html `
  --positions data/clean_verify/dashboard_treasury_positions.csv `
  --chain data/clean_verify/ZF_FOP_Static_202609_202612_from_20260630_to_all_monitor_frame.csv `
  --bars data/carry_dashboard_bars.csv `
  --output-dir data `
  --summary-only `
  --require-ready
```

`--positions`、`--chain`、`--bars` 支持逗号或分号分隔的多个文件，也支持 pandas notebook 直接复制出来的 HTML dataframe 文本文件；pandas 截断表里的 `...` 省略号行会被过滤。若输入文件名包含 `ZF` / `ZN` / `ZC`，且部分行内部无法识别品种，发布时会用文件名补 `product`，例如 `ZF_chain.csv,ZN_chain.csv,ZC_chain.csv`。
`--summary-only` 会在发布后打印简洁状态；`--require-ready` 会复用后面的完整性规则，在缺新鲜完整链或 K 线时返回非 0。

一键刷新 ZF/ZN 持仓、期权链和 30 分钟 K 线；`ZF/ZN` 共用 `--chain-specs`，`ZC` 预留 `--zc-chain-specs` 单独配置：

```powershell
python -m target_treasury_monitor_clean.cli refresh-carry-html `
  --host 127.0.0.1 --port 4001 --market-data-type delayed `
  --positions-csv data/carry_dashboard_positions.csv `
  --chain-specs "ZF=202609,202612;ZN=202609,202612" `
  --zc-chain-specs "" `
  --bars-contracts "ZF:202609,ZN:202609" `
  --html-data-dir data `
  --min-chain-rows 50 `
  --min-bars-rows 100 `
  --max-chain-age-hours 24 `
  --max-bars-age-hours 72 `
  --require-ready
```

`--require-ready` 会在发布后检查有持仓的资产是否都有新鲜完整期权链和 K 线；如果 IB 请求超时导致数据不完整，命令会返回非 0。等 ZC 参数确认后，把 `--zc-chain-specs` 改成类似 `"ZC=202609,202612"` 即可纳入同一流程。

只刷新 K 线时：

```powershell
python -m target_treasury_monitor_clean.cli future-bars `
  --host 127.0.0.1 --port 4001 --market-data-type delayed `
  --contracts ZF:202609,ZN:202609 `
  --bar-size "30 mins" --duration "1 M" --keep-going `
  --prefer-local-symbol `
  --cache-dir data/clean_verify `
  --output data/carry_dashboard_bars.csv
```

`--prefer-local-symbol` 会在没有缓存 conId 时直接构造 `ZFU6` / `ZNU6` 这类标准 localSymbol，减少对 `qualifyContracts` 的依赖；如果 IB 历史数据请求本身超时，这个参数也无法绕过。

如果 `future-bars` 或 `refresh-carry-html` 卡住，先跑轻量诊断：

```powershell
python -m target_treasury_monitor_clean.cli ib-smoke `
  --host 127.0.0.1 --port 4001 --market-data-type delayed `
  --contracts ZF:202609,ZN:202609 `
  --timeout 6
```

`connected=true` 且 `server_time` 有值说明 socket 和 IB server 时间正常；如果 `contracts[].error` 是 `TimeoutError`，说明合约详情/qualify 请求没有返回，期权链发现和 K 线拉取也会被卡住。

检查 HTML 当前数据是否完整：

```powershell
python -m target_treasury_monitor_clean.cli validate-carry-html `
  --data-dir data `
  --expected-products ZF,ZN,ZC `
  --min-chain-rows 50 `
  --min-bars-rows 100 `
  --max-chain-age-hours 24 `
  --max-bars-age-hours 72
```

如果要在脚本里强制拦住不完整数据，加 `--require-ready`；当任一有持仓资产缺新鲜完整链或 K 线时，命令会返回非 0。
如果只想快速看结论，加 `--summary-only` 输出简洁状态。

重点看 `readiness`：

```text
criteria           : 判定完整链/K线的最小行数阈值
missing_full_chain : 还有哪些品种缺完整且新鲜的期权链；低于 min_chain_rows 或超过 max_chain_age_hours 都会算缺
missing_bars       : 还有哪些品种缺 30 分钟 K 线；低于 min_bars_rows 或超过 max_bars_age_hours 都会算缺
chain_view         : standard_chain 标准且新鲜；stale_chain 过期链；partial_chain 截断/样本链；position_fallback 持仓腿报价兜底
```

## 卖方期权库存规划器

`sell_side_inventory_planner.html` 是一个新的独立 dashboard，暂时不和旧 `carry_risk_dashboard.html` 合并。它把当前卖方期权库存、候选链、目标压力、节点集中度、保证金预检和人工双确认交易放在一个中文看板里；交易能力来自另行显式启动的 loopback trade gateway，不由日常 planner server 自动开启。

默认读取：

```text
data/carry_dashboard_positions.csv
data/carry_dashboard_chain.csv
```

启动本地服务：

```powershell
python -m target_treasury_monitor_clean.cli serve-inventory-planner `
  --directory . `
  --host 127.0.0.1 `
  --port 8766
```

然后打开：

```text
http://127.0.0.1:8766/sell_side_inventory_planner.html
```

这个新 planner 的核心约束：

- 核心库存只统计 `position < 0` 的期权；多头期权不参与核心计算。
- 0DTE 只作为普通 DTE 桶，不按来源分类，也不会自动标高风险。
- Put / Call 分开看，并按用户配置的 DTE 窗口、支撑区/压力区、Delta 区间扫描候选。
- 候选排序使用收益、分布平衡、风险、目标贴合的综合分；不会只按最高权利金排序。
- 手动填写候选数量后，页面会即时重算 Before / Added / After、压力场景和节点集中度。
- 候选期权卡片和当前品种期货合约可进入订单草稿；候选卡保留本地估算保证金，实时行情由 planner 的独立订阅流更新。
- 日常 planner 的 `/api/margin-whatif` 继续固定返回 403。另行启动的 8767 trade gateway 才能调用 IB 原生 What-If 和订单接口，并且默认 Paper、仅 loopback、随机解锁码、短时会话和一次性发送。
- 第一版只允许 CBOT FUT/FOP 的 DAY 限价单；提交前必须通过账户级保证金缓冲、订单指纹、动态确认词和最终同指纹 What-If 复核。市价单、普通 OPT、组合单和自动下单均未开放。
- Live 模式必须由交易服务为所选合约取得 `marketDataType=1` 的有效实时 bid/ask/last；延迟、冻结或缺失行情会 fail closed。
- 交易服务对重复 preview-id、过期预览、IB warning 和不确定网络结果使用 fail-closed；发送或撤单后自动锁定。只管理带 `IBDASH:` orderRef 的本 Dashboard 订单。
- 节点集中度只给提示，不会禁止用户覆盖。
- Put / Call 候选热力图按“行权价 x DTE 桶”展示；选中的手动计划可导出 JSON、CSV 和 Markdown。

## 当前仍复用的旧代码

为了避免重写后引入不必要风险，这一版仍复用：

- `target_treasury_account_monitor`：账户持仓、Greeks、展示 frame、现有静态链发现逻辑。
- `treasury_fop_chain.py`：IB 期权链发现、批量订阅、ticker 转表、成交量差分事件。

下一步验证通过后，可以把这些底层函数迁入本目录，再删除旧 notebook、旧 dashboard、历史快照和 `__pycache__` 等无意义内容。
