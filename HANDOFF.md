# 当前项目状态

> 盘点时间：2026-07-22（Asia/Shanghai）
> 分支：`main`
> HEAD：`93691c8`（`更新`）

本仓库当前的主线是 **IBKR 美债/玉米期货期权数据刷新 + 本地卖方库存规划器**。推荐入口是 `open_inventory_planner.sh`（macOS）或 `open_inventory_planner.ps1`（Windows），页面地址默认是 `http://127.0.0.1:8766/sell_side_inventory_planner.html`。

`93691c8` 已推送到 `origin/main`，本地 `main` 已于 2026-07-22 确认与远端一致。当前工作树另有尚未提交的权利金口径修复：卖方期权一旦 ITM，会继续保留在持仓节点、合约数和 Greeks/Delta 风险统计中，但从剩余权利金、Delta 加权权利金、目标压力和按到期日权利金总览中完全剔除；OTM 高 Delta 卖方期权仍按既定阈值折算。接手前先运行 `git status --short`，不要覆盖或回退这些改动。

2026-07-16 的验证结果：

- `conda run -n ib python -m unittest discover -v`：123/123 通过；新增覆盖 full 成功批次不得误用空批 5 秒暂停、动态行情线预算和 Gateway 换账号回退；`/api/margin-whatif` 安全回归固定返回 403，交易网关测试全部使用 FakeBroker，不连接 IBKR、不会发送订单。
- `test_inventory_planner_dashboard_smoke.js`、`test_carry_dashboard_render_smoke.js`、`A_Share_Option/test_dashboard_smoke.js`、`A_Share_Option/test_arbitrage_monitor.js`：全部通过。
- 主线 Python 文件 `py_compile` 通过；macOS 启停脚本 `bash -n` 通过。
- Windows PowerShell 启停脚本只做了静态审阅，尚未在 Windows 实机验证。
- 盘点时 `127.0.0.1:8766` 正在监听；这是运行态快照，不应假定下次接手时仍然在线。
- 最近一次 fast refresh 成功完成并发布 38 条持仓、611 条期权链和 2,661 条 K 线；`ready_for_full_view: true`，三品种链与 K 线均通过当前 freshness/row-count 校验。
- 2026-07-16 19:38 的真实持仓快刷墙钟用时为 `9.8秒`：持仓行情 `4.7秒`、期权链 `0.0秒`、期货数据 `3.7秒`、启动/连接/发布等其余开销 `1.4秒`；CLI 核心刷新 `8.6秒`。此前慢样本中期货数据可达到约 `9.3秒`，原因是 ZF/ZN/ZC 仍逐品种串行等待。代码和回归测试确认 fast 不会调用候选链报价扫描。

## 已完成

- 已形成一键主流程：启动 planner server、刷新 IB 持仓/期权链/K 线、发布稳定 CSV、页面轮询刷新状态。
- planner server 现使用独立 client-id 常驻订阅全部当前持仓及页面选中的 ZF/ZN/ZC 期货月份，内存快照通过 `/api/live-positions` 发布；页面每秒只重算持仓相关模块。组合资产总览标题旁显示具体采样时间及 IB 实际返回的实时/延迟/混合状态。2026-07-16 实测 40 条持仓 + 3 条 202609 期货均可在同一连接中订阅；流服务统一请求 delayed fallback，账户实时可用时 IB 仍返回 `marketDataType=1`，实时会话被占用或缺少权限时返回 `3`。10197 等实时会话冲突会触发 delayed 重订阅，1100/1101 等断线事件会重连/重订阅；配置账户不在新 Gateway 会话中时，会按 managed accounts 和实际 ZF/ZN/ZC 持仓自动选用当前登录账户，并通过 API 暴露切换状态。
- 2026-07-16 22:29 的 full 慢样本总计约 `1分55秒`，其中期权链约 `1分43秒`，持仓 `10.1秒`、期货 `0.6秒`。原因不是期货或发布卡住，而是约 617 个过滤候选分批获取；除了旧稳定判定过宽，还发现成功批次错误套用了“空批重试暂停 5 秒”，每批额外白等约 4.5 秒。现已改为只有真实空批重试才等 5 秒，成功批只等 0.5 秒；full 只以报价 + Greeks 判断稳定，默认最大等待/稳定/批间暂停为 `5.0/0.75/0.5秒`，并在日志打印每批结束是 stable 还是 timeout。planner 候选不再请求页面未使用的 option volume/OI generic ticks；逐合约 `10090` 在日志源头折叠为一次计数摘要。修复后的 40 条批次实测期权链 `81.6秒`、总计 `100.6秒`；动态 50 条批次最终实测期权链 `66.3秒`、总计 `84.2秒`，相对截图分别缩短约 36% 和 27%。请求批次默认 50，但运行时会按 `100 - 当前全部持仓订阅 - 期货订阅 - 5条预留` 自动缩小；当前 40+3 条常驻线时有效批次为 50，总线数约 93（另留 5 条安全余量）。
- 已区分 fast/full/scheduled refresh：fast 通过流式订阅更新持仓期权，并刷新底层期货价，完全跳过候选链报价扫描、保留候选链和 K 线；full 刷新既有 filter 选中的全部候选链，并在未指定 `--skip-bars` 时刷新 K 线；scheduled 按美国东部日期在两者间自动选择。
- 已支持循环 scheduled refresh；macOS 使用 `launchd` 常驻，Windows 使用隐藏后台 Python 进程，默认每 1 分钟启动一轮。循环按“本轮开始时间”计算间隔，不会额外叠加本轮耗时。
- scheduled 会分别检查 ZF、ZN、ZC 候选链行的 `snapshotTimeUtc`：任一品种不是美国东部当天或缺失就执行 full，全部为当天才 fast；不能用 fast 重新发布后的 CSV 修改时间代替快照日期。
- 页面顶部已精简为当前品种、可见的 `Delta 预警阈值`（默认 `0.40`）、三种刷新按钮和 ZF/ZN/ZC 合约月份；上传 CSV、加载样例、导出和链表颜色等低频控件不再展示。页面继续显示请求模式、实际模式和智能判定原因。
- 刷新进度增加真实分段耗时，明确拆成 `持仓行情`、`期权链`、`期货数据 = 底层期货报价 + K线`、`连接/发布` 和总计。快刷的期权链必须显示 `0.0秒`，不能再把持仓订阅时间归到期权链；状态接口返回 `phaseTimings`，旧进程未返回该字段时页面可从阶段日志兼容解析。浏览器本地计时在收到后端墙钟值后必须让位给后端值，避免轮询/CSV 重载延迟污染总计。
- 刷新日志默认显示摘要：展开的校验 JSON 不再写入刷新 stdout，完全相同的行会去重，IB `10090`/`200` 按错误码计数折叠；页面可切换完整原始日志，并提供复制按钮。按品种计时使用 `futures.ZF`/`options.ZF` 形式记录单段时间，避免原先的累计 `3/6/9秒` 被误解为重复执行。
- 已增加 `host + port + client-id` 本机进程锁，避免相同 IB client-id 并发刷新；外层持锁时底层 CLI 使用 `--no-client-lock`，避免自锁。
- `refresh_status.json` 已改为临时文件加 `os.replace` 的原子写入；服务端对短暂的 JSON 写入窗口和过期的 running 状态有容错。
- 持仓刷新现在只等待 `POSITIONS` 启动数据，减少账户更新流超时；空快照默认不会覆盖非空本地缓存，IB/链/K 线失败时可按 strict 参数决定失败或回退缓存。
- planner 页面已支持 ZF、ZN、ZC，候选 DTE 独立筛选；库存 DTE 只约束统计/规划范围，实际持仓节点仍展示全部到期日。
- 候选 DTE 列头和单元格已统一按 `America/New_York` 当日从 expiry 重算；过期行不会再被压入 0DTE。单边 Delta 缺失但同 expiry/strike 对侧 Delta 可用时，页面按 Put-Call parity 近似补全并明确显示 `Delta≈`。
- 完整合约缓存查找已排除 `*_selected_contracts.csv`，并优先使用带有效 chain summary 的完整宇宙，避免 fast refresh 把过滤子集反复写成主缓存、逐步丢失到期日和 Strike。
- 页面刷新已增加 ZF/ZN/ZC 合约月份选择，默认都只取 `202609`，可切到 `202612` 或两个月份；单月份刷新可复用并过滤双月份完整缓存。每个品种的期货价现在只获取一次并直接传给期权链筛选，不再在 sidecar 和 chain 阶段重复请求。早期 2026-07-16 实测 fast refresh 从双月份约 62 秒降到单月份约 43 秒，随后跳过候选链扫描后已进一步降到约 10 秒。
- fast refresh 的 Strike 窗口已按品种报价单位设置：ZF/ZN 使用点数，ZC 使用美分；ZC 实际刷新从 0 个报价恢复到 226 个。页面候选还会排除落后该品种最新快照超过 24 小时的报价，避免新旧报价混用造成跨 Strike 权利金倒挂。
- ZC 候选按每条期权的 `undPrice` 判断 OTM，支持同一品种不同期货月份；默认 Strike 窗口覆盖完整 ZC 链，不再把有效候选全部挡掉。
- 组合资产总览已增加全部剩余权利金、按精确到期日的权利金总览，以及分品种 `Delta 加权期权金`：卖方期权一旦 ITM，全部权利金统计直接按 0 处理，但持仓节点和风险敞口仍保留；其余 OTM 仓位中，阈值位于页面顶部当前品种右侧，默认 `0.40`，优先使用持仓快照 Delta，缺失时才按同一 `conId` 从链数据补齐；当 `|Delta|` 四舍五入到两位小数后达到阈值时，按实际 Delta 以 `权利金 × (1 - |Delta|)` 折算。总体四项统计和按到期日表格在宽屏下左右排列，窄屏自动改为单列。
- 分品种组合资产卡已移除保证金字段，并把“期货”改为 `等效期货`：实际期货 Delta 加上符合条件的深度实值多头期权 `position × Delta`。`期权Delta` 只统计 short 期权；`组合Delta = short期权Delta + 等效期货`，普通多头期权不进入组合 Delta。页面顶部现可调三项识别参数：Delta 下限默认 `0.90`、最小实值默认 `1 tick`、最大时间价值默认 `2 ticks`；判断 Delta 时仍按两位小数显示值，等价张数仍使用实际 Delta。未通过的多头实值期权会显示“等效候选未通过参数”，悬停可查看具体失败条件。2026-07-16 真实页面已重新识别 `HY3N6 C1087`，当次快照约等价 `+0.92` 张期货、时间价值约 `1.5 ticks`；把最大时间价值改为 `1 tick` 后会明确显示 `1.5ticks > 1.0ticks`。
- “底层资产走势”模块默认折叠，可通过标题右侧按钮展开或重新折叠；折叠不停止数据计算，展开时会按当前配置重绘日线与 30 分钟图。
- “当前库存行权节点”会按同一个可调 Delta 阈值，把达到阈值的卖方持仓显示为红色单元格并附加“高Delta”徽标；阈值变化时标记必须同步更新。
- planner 的候选过滤继续执行 DTE、OTM、Strike、Delta 和 `bid > 0` 约束；候选排序仍是收益、分布、风险、目标贴合的综合分。
- ZC 单位已统一：页面展示的 1 美分/蒲式耳对应每张 50 美元；同时保留 IB 原始 `contractMultiplier=5000`。采集层、Python 规划器和浏览器端都会用 `position × quote × 50` 对异常 `marketValue` 做比率校验，发现类似 100 倍放大时改用现金口径估值。IB 返回的 `-1`/`-100` 无效期权价格必须跳过并回退到非负报价；2026-07-16 真实复测确认错误值曾把 ZC 权利金放大到约 `$10,003`，修复后恢复为 `$19`。
- DTE 统一使用 `America/New_York` 交易日，并优先按到期日重算，避免复用 CSV 中已经过期的 DTE 值。
- 已完成公网暴露静态安全审计，结论和整改清单见 `security_audit_report.md`；目前只是审计完成，安全整改尚未完成。
- 已新增独立 Dashboard trade gateway：日常 8766 planner 的 `/api/margin-whatif` 仍在连接 IB 前固定返回 403；订单能力只存在于显式前台启动的 127.0.0.1:8767。trade gateway 默认 Paper，Live 需要启动参数、账户精确确认、正数 minimum reserve，以及所选合约 `marketDataType=1` 的有效实时 bid/ask/last；第一版只允许 CBOT FUT/FOP、DAY、LMT。浏览器先用随机进程码解锁，再生成短时 What-If 预览、SHA-256 订单指纹和动态确认词，提交前对存储的同一订单再次 What-If。preview-id 发送前即消费，网络结果不确定时禁止重试；发送/撤单后自动锁定，只管理 `IBDASH:` orderRef。自动化测试全部使用 FakeBroker，从未连接或发送真实 IB 订单。

## 当前架构

```text
open_inventory_planner.sh / .ps1
                |
                v
refresh_inventory_data.py              外层编排、循环、状态文件、client-id 锁
                |
                v
target_treasury_monitor_clean.cli
  refresh-carry-html                    持仓 -> 链 -> K线 -> 发布 -> 完整性校验
      |               |            |
      v               v            v
account_dashboard  chain_batch   future_bars
      |               |
      +-------+-------+
              v
target_treasury_account_monitor/        仍在使用的旧底层层
treasury_fop_chain.py                   合约发现、行情订阅、ticker 转表
              |
              v
data/planner/
  carry_dashboard_positions.csv
  carry_dashboard_chain.csv
  carry_dashboard_bars.csv
  refresh_status.json
              |
              v
inventory_planner_server.py             本地静态服务 + manifest/status/refresh API
inventory_market_stream.py              持仓 + 指定期货常驻 reqMktData 订阅、重连和实时/延迟判定
margin_whatif.py                         IB账户级预检与容量算法；仅由显式trade gateway调用
trade_gateway.py / trade_gateway_server.py  独立Paper/Live交易状态机、HTTP边界和IB适配器
              |
              v
sell_side_inventory_planner.html        中文卖方库存规划、实时行情、保证金预检与双确认交易台
```

核心 IB 会话通过 `ib_async` 以 `readonly=True` 连接；日常 planner 不调用 IB 订单协议。`margin_whatif.py` 保留的是未接入页面的算法研究代码，运行原生 What-If 前必须明确知道它会经过订单协议。`carry_risk_dashboard.html` 是旧的 carry 风险页面，`target_treasury_monitor_clean/app.py` 是 Streamlit 三页签入口，两者仍可用，但不是当前推荐的日常 planner 入口。

`A_Share_Option/`、`news_api/`、`prediction_market/` 和 `macro_calendar.py` 是独立旁支模块，不在这次美债库存规划器整理范围内。生成数据位于被 `.gitignore` 忽略的 `data/`，不能依赖 Git 保存或恢复运行快照。

## 关键文件

| 文件 | 作用 | 接手注意点 |
| --- | --- | --- |
| `README.md` | 当前日常启动、刷新模式、故障处理和测试说明 | 修改默认参数或入口时同步更新 |
| `refresh_inventory_data.py` | 推荐外层入口；负责 server、循环刷新、状态和 client-id 锁 | 不要让子 CLI 重复获取同一把锁 |
| `open_inventory_planner.sh` / `stop_inventory_planner.sh` | macOS `launchd` 启停 | 停止时必须先移除 job，否则会被自动拉起 |
| `open_inventory_planner.ps1` / `stop_inventory_planner.ps1` | Windows 后台启停 | 依赖仓库内 `.venv\Scripts\python.exe`，待实机验证 |
| `sell_side_inventory_planner.html` | 当前主 UI，JavaScript 全内嵌 | UI 行为由 Node smoke test 固化 |
| `target_treasury_monitor_clean/cli.py` | 所有 clean workflow 的 CLI 和 `refresh-carry-html` 实现 | fast 不得调用候选链报价扫描；full 负责完整 filter、发布与 readiness |
| `target_treasury_monitor_clean/inventory_planner_server.py` | 8766 HTTP 服务、manifest、刷新 API 和状态 API | 当前不适合公网暴露 |
| `target_treasury_monitor_clean/inventory_market_stream.py` | 独立只读 IB 会话，常驻订阅当前持仓与指定期货并发布内存快照 | client-id 默认是刷新 id + 2；断线后必须自动重连/重订阅 |
| `target_treasury_monitor_clean/inventory_planner.py` | 可测试的库存解析、候选评分、敞口和压力计算 | 与 HTML 中的同类逻辑保持一致 |
| `target_treasury_monitor_clean/ib_client_lock.py` | 跨进程 IB client-id 锁及 owner 元数据 | 锁粒度是 host/port/client-id |
| `target_treasury_monitor_clean/carry_dashboard_sync.py` | 稳定 CSV 发布、发现和完整性校验 | readiness 阈值的唯一后端来源 |
| `target_treasury_monitor_clean/chain_batch.py` | 静态链缓存、筛选、行情批次刷新 | fast refresh 会合并旧远端链 |
| `target_treasury_monitor_clean/future_bars.py` | 期货 K 线获取与保存 | 当前 readiness 的主要阻塞点 |
| `target_treasury_monitor_clean/settings.py` | IB 和链配置 dataclass | 当前仍硬编码默认账户 |
| `target_treasury_account_monitor/` | clean 层仍依赖的旧底层实现 | 迁移完成前不得删除 |
| `treasury_fop_chain.py` | 合约发现、批量 `reqMktData`、ticker 转表等 | 迁移完成前不得删除 |
| `data/planner/` | planner 当前实际输入和刷新状态 | 生成数据、含账户信息、被 Git 忽略 |
| `test_refresh_inventory_data.py` | 外层编排、锁、server、缓存回退测试 | 部分测试需绑定本地随机端口 |
| `test_inventory_planner.py` / `test_inventory_planner_dashboard_smoke.js` | 规划计算和主 UI 行为契约 | 改策略或 UI 时必须同步验证 |
| `test_zc_quote_units.py` | ZC 现金乘数回归测试 | 防止 50/5000 单位再次混淆 |
| `security_audit_report.md` | 8766 公网暴露安全审计 | P0/P1 尚未整改 |

## 已知问题

1. **禁止直接公网暴露 8766。** 当前服务把仓库根目录交给 `SimpleHTTPRequestHandler`，没有认证；匿名用户可读取源码、`.git`、数据和日志，并可 POST 触发本地刷新。无认证 Cloudflare Quick Tunnel 的综合风险为 Critical。
2. `macro_calendar.py` 中存在已进入 Git 历史的硬编码 FMP API Key；必须撤销/轮换并改成只从环境变量或密钥存储读取。README 和配置里也有硬编码账户标识，需要脱敏。
3. 当前稳定 CSV 已通过完整就绪校验，但 fast 有意保留已有 K 线和候选链，随着时间推移仍会再次变旧；候选链只在 full 中更新。落后该品种最新快照 24 小时以上的报价不会作为候选。页面可以用对侧 Delta 做明确标记的 parity 近似，但两侧 Delta 都缺失时仍不会进入候选。
4. 最近一次刷新日志出现 `ZF 202607` futures contract 的 “No security definition” 错误；流程仍靠后续持仓/缓存完成。需要清理已失效月份的合约引用或提高合约月份选择的稳健性。
5. 三个稳定 CSV 仍是直接覆盖写入，可能被并发读取到截断文件，或产生 positions/chain/bars 跨版本混合；目前只有 `refresh_status.json` 是原子写入。
6. HTTP 服务缺少认证、CSRF/Origin 校验、body/频率/任务数量限制、安全响应头和日志轮转；状态 API 还会返回 stdout、账户、路径及错误细节。
7. `target_treasury_monitor_clean/` 还没有真正消除旧层依赖；直接删除 `target_treasury_account_monitor/` 或 `treasury_fop_chain.py` 会破坏主流程。
8. 默认账户、月份（当前为 202609/202612）、端口和 client-id 偏运行环境化且会过期；接手时必须先检查，不要长期把当前月份当常量。
9. 依赖只有未锁版本的 `requirements_dashboard.txt`，没有完整 lockfile、CI、秘密扫描或依赖审计。
10. full refresh、PowerShell 后台生命周期和安全整改后的公网访问尚无端到端自动化验证；当前持仓快刷已实测，scheduled 的日期分流由自动化测试和 dry-run 覆盖。
11. 常驻行情占用的 market-data lines 随持仓数变化；2026-07-16 实测为 43 条（40 持仓 + 3 期货）。候选临时报价请求批次默认 50，但会按当前常驻订阅和 5 条预留动态封顶，需继续保持总数不超过 IB 常见的 100-line 限额。账户实时会话被手机端或其他终端占用时，IB 可能把连接从 `marketDataType=1` 切为 `3`；这是权限/会话状态变化，不应伪装成实时。IB 官方把 delayed streaming 描述为通常延迟约 15–20 分钟；页面显示的时间是本机采样/获取时间，不是精确交易所行情时间，不能机械减一个固定分钟数后冒充精确时点。

## 下一步任务

按优先级建议：

1. 完成 `security_audit_report.md` 的 P0：停止无认证 Tunnel、轮换 FMP Key、将静态根改成最小发布目录、增加认证和刷新权限、限制请求/并发，并从状态响应中移除敏感信息。
2. 在 IB Gateway 可用时定期执行 full refresh，并运行：

   ```bash
   conda run -n ib python -m target_treasury_monitor_clean.cli validate-carry-html \
     --data-dir data/planner --expected-products ZF,ZN,ZC --require-ready
   ```

   目标是持续保持 `ready_for_full_view: true`，同时排查 `ZF 202607` 无合约定义错误。
3. 观察常驻行情流在 Gateway 自动重启、手机端抢占实时会话、1100/1101/1102 重连和持仓增减时的长期稳定性；记录订阅总数，避免常驻线数加候选批次超过账户额度。
4. 将 positions/chain/bars 改为同一版本目录内的原子发布，再一次性切换 manifest；保留上一版本以便回滚。
5. 在 Windows 实机验证 `.ps1` 的启动、关闭、端口占用、日志、PID 文件和终端关闭后的常驻行为。
6. 审阅并提交当前 10 个代码/测试文件及 `HANDOFF.md` 的 ZC 单位/哨兵价、ZN 深度实值识别、紧凑 UI、刷新分段计时和日志面板改动；不要把 `data/` 或账户快照加入 Git。
7. 逐步把旧层底层函数迁入 `target_treasury_monitor_clean/`，每迁移一组先更新导入并跑全套测试，最后再删除旧目录。
8. 补齐固定版本依赖、CI（Python + Node smoke）、秘密扫描和安全回归测试；月份和账户配置改为环境变量或显式配置。

## 不得破坏的行为

- **绝不自动下单。** 日常 planner server 不得获得普通订单入口，`/api/margin-whatif` 必须继续 403。trade gateway 只能显式前台启动，默认 Paper；任何真实发送必须经过进程解锁、短时预览、保证金通过、订单指纹、动态人工确认词和最终同指纹复核，发送/撤单后自动锁定。不得增加定时/策略触发下单、自动重试、不经确认的 quick trade、市价单或公网监听。
- **不要把 `IB.connect(readonly=True)` 当权限控制。** 它只改变启动同步行为；Gateway/TWS 的 Read-Only 设置才是全局硬阻断。Live 关闭该设置后，同一端口上的所有 API clients 都失去券商端硬阻断；生产应优先用独立 IB 用户/会话隔离行情和交易权限。
- 核心库存只统计 `position < 0` 的期权；多头期权和非期权不进入核心卖方库存计算。
- `期权Delta` 必须只统计 `position < 0` 的 short 期权；`等效期货 = 实际期货 position × Delta + 深度实值多头期权 position × Delta`；`组合Delta = 期权Delta + 等效期货`。普通多头期权不得进入组合 Delta。
- 深度实值多头期权的期货等价张数固定为 `position × Delta`。识别条件由页面顶部参数控制：`|Delta|` 按两位小数显示达到 Delta 下限、实值 ticks 达到最小值、时间价值 ticks 不超过最大值；默认分别为 `0.90`、`1`、`2`。计算张数仍使用实际 Delta，不使用四舍五入值。short 深度实值期权已经属于期权 Delta，不得再作为等效期货重复计算。
- 组合资产总览卡不显示保证金；候选期权矩阵中的单笔保证金估算仍保留，除非另有明确需求。
- 0DTE 只是普通 DTE 桶，不因来源被特殊分类，也不能自动标成高风险。
- Put 和 Call 必须分开统计与筛选；库存 DTE 只约束统计/规划，不能把范围外的真实当前持仓从“当前库存行权节点”隐藏。
- 候选 DTE 只控制候选矩阵；用户选中的 DTE 列即使没有合约也应保留。候选必须继续满足所选 DTE、OTM、Strike、Delta 和可卖 `bid > 0` 条件。
- 过期 expiry 不得折叠成 0DTE；DTE 列头日期和候选单元格必须使用同一套 expiry 重算结果。Parity 补全的 Delta 必须显示 `≈`，不得冒充 IB 直接 Greeks；bid 缺失仍不能成为可卖候选。
- `*_selected_contracts.csv` 只是报价过滤结果，绝不能作为完整合约缓存复用；候选报价不得混用与该品种最新快照相差超过 24 小时的旧行。
- ZC 的 OTM 判断必须使用期权行对应的 `undPrice`，不能把 202609 与 202612 的期权都套在同一个期货价上。
- 权利金统计必须先剔除所有 ITM 卖方期权：剩余权利金和 Delta 加权期权金都记为 0，但不得隐藏持仓、合约数或 Greeks/Delta 风险。对剩余 OTM 仓位，`Delta 加权期权金` 阈值由页面参数控制、默认 `0.40`；优先使用持仓快照 Delta，缺失时才按同一 `conId` 从链数据补齐；`|Delta|` 四舍五入到两位小数后达到阈值时乘以 `1 - 实际|Delta|`，显示值低于阈值则不折减；它只是风险观察指标，不能在文案中表述为确定胜率。
- 候选排序必须保留收益、分布、风险和目标贴合的综合评分，不能退化为只按最高权利金排序。
- 节点集中度只告警，不禁止用户覆盖；手动数量变化必须即时重算 Before / Added / After、压力场景和节点暴露。
- fast refresh 必须用持仓快照中的 conId 更新持仓期权行情，完全跳过候选链报价扫描，并保留已有候选链和 K 线；IB 暂时失败时，只能在有有效缓存且未启用 strict 参数时降级。
- planner server 的常驻行情只能订阅当前持仓和用户选中的期货月份；不得把整条候选期权链改成长久在线订阅。页面可以每秒读取内存快照，但不能每秒创建 IB 请求或重扫 CSV/候选链。实时/延迟标签必须取自 IB `marketDataType`，Gateway 断开后显示离线并自动重连，不能继续把旧报价标成实时；配置账户不在当前 Gateway managed accounts 时必须回退到当前登录且持有相关头寸的账户，不能发布一个看似成功的空持仓。
- full 的候选报价分批稳定判定必须保留报价和 Greeks；planner 候选不请求页面未使用的 OI/成交量 generic ticks。不能为了加速突破账户行情线额度，也不能暂停持仓常驻订阅。
- 合约月份选择约束 full 的普通候选行情订阅和每轮底层期货价；不能排除月份外的当前持仓 conId。full 中已取得的同品种期货价必须复用于当轮期权筛选，不能在 sidecar 和 chain 阶段重复请求。
- 空持仓快照默认不得覆盖非空缓存；只有账户确认空仓并显式使用 `--allow-empty-positions` 时才允许发布空持仓。
- full refresh 必须覆盖更广的配置链，并在没有 `--skip-bars` 时尝试更新 K 线。
- scheduled 必须以 `America/New_York` 日期逐品种检查候选链的实际快照时间；任一配置品种缺失或非当天都要 full，全部为当天才 fast。
- 相同 `host + port + client-id` 不得并发刷新；状态文件必须保持完整 JSON，重复页面请求应复用正在运行的任务。
- 刷新分段耗时必须来自实际调用点计时：持仓行情、期权链、期货数据、连接/发布四段加总应与后端墙钟总耗时一致；fast 的期权链时间应为零，不得把持仓时间并入期权链，也不得用浏览器轮询延迟或固定百分比伪造耗时。旧状态无 `phaseTimings` 时允许从 `phase timing:` 日志兼容解析。
- 稳定数据文件名和 manifest/status API 兼容性不得随意改动：页面默认读取 `data/planner/carry_dashboard_{positions,chain,bars}.csv`。
- ZC 行权价按美分/蒲式耳展示；估值现金乘数必须是 50 美元/显示单位，同时保留 IB 原始 `contractMultiplier=5000`。IB 或旧缓存若返回与 `position × quote × 50` 相差超过 10 倍的 ZC `marketValue`，必须按现金口径归一化；IB 的 `-1`/`-100` 期权价格哨兵不得进入权利金计算；ZF/ZN 的现有 1000 乘数语义不能受影响。
- DTE 必须以 `America/New_York` 交易日计算，并优先根据到期日重算，不能信任缓存中的旧 DTE。
- ZF、ZN、ZC 都是当前主流程支持品种；不能为了修一个品种破坏另外两个品种的解析、筛选或页面标签。
- `sell_side_inventory_planner.html` 与旧 `carry_risk_dashboard.html` 保持独立；未经明确设计不要把两套页面强行合并。
- 在 clean 层完全消除导入并通过全套测试前，不得删除 `target_treasury_account_monitor/` 或 `treasury_fop_chain.py`。
- 服务默认只绑定 `127.0.0.1`。安全 P0 完成前，不得改为 `0.0.0.0`，也不得通过无认证公网 Tunnel 暴露。
