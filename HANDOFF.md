# 当前项目状态

> 盘点时间：2026-07-15（Asia/Shanghai）
> 分支：`main`
> HEAD：`9c272b1`（`2026-07-14更新`）

本仓库当前的主线是 **IBKR 美债/玉米期货期权数据刷新 + 本地卖方库存规划器**。推荐入口是 `open_inventory_planner.sh`（macOS）或 `open_inventory_planner.ps1`（Windows），页面地址默认是 `http://127.0.0.1:8766/sell_side_inventory_planner.html`。

当前工作树不是干净状态：主线刷新、UI、ZC 报价单位、DTE 时区和启动脚本等改动尚未提交；`open_inventory_planner.ps1`、`stop_inventory_planner.ps1`、`security_audit_report.md`、`test_zc_quote_units.py` 和本文件是新增文件。接手前先运行 `git status --short`，不要覆盖或回退这些改动。

2026-07-15 的验证结果：

- `conda run -n ib python -m unittest discover -v`：67/67 通过。
- `test_inventory_planner_dashboard_smoke.js`、`test_carry_dashboard_render_smoke.js`、`A_Share_Option/test_dashboard_smoke.js`、`A_Share_Option/test_arbitrage_monitor.js`：全部通过。
- 主线 Python 文件 `py_compile` 通过；macOS 启停脚本 `bash -n` 通过。
- Windows PowerShell 启停脚本只做了静态审阅，尚未在 Windows 实机验证。
- 盘点时 `127.0.0.1:8766` 正在监听；这是运行态快照，不应假定下次接手时仍然在线。
- 最近一次 fast refresh 成功完成并发布 36 条持仓、1,150 条期权链和 2,678 条 K 线，但 `require-ready` 仍为 false：ZF、ZN、ZC 的链是新鲜完整的，K 线最新停在 2026-07-09，约 123 小时，超过 72 小时阈值。

## 已完成

- 已形成一键主流程：启动 planner server、刷新 IB 持仓/期权链/K 线、发布稳定 CSV、页面轮询刷新状态。
- 已区分 fast/full refresh：fast 只刷新近端、现价附近及持仓相关合约，保留缓存的远端链和已有 K 线；full 刷新更广的配置链，并在未指定 `--skip-bars` 时刷新 K 线。
- 已支持循环 fast refresh；macOS 使用 `launchd` 常驻，Windows 使用隐藏后台 Python 进程，默认每 3 分钟刷新。
- 已增加 `host + port + client-id` 本机进程锁，避免相同 IB client-id 并发刷新；外层持锁时底层 CLI 使用 `--no-client-lock`，避免自锁。
- `refresh_status.json` 已改为临时文件加 `os.replace` 的原子写入；服务端对短暂的 JSON 写入窗口和过期的 running 状态有容错。
- 持仓刷新现在只等待 `POSITIONS` 启动数据，减少账户更新流超时；空快照默认不会覆盖非空本地缓存，IB/链/K 线失败时可按 strict 参数决定失败或回退缓存。
- planner 页面已支持 ZF、ZN、ZC，候选 DTE 独立筛选；库存 DTE 只约束统计/规划范围，实际持仓节点仍展示全部到期日。
- 候选 DTE 列头和单元格已统一按 `America/New_York` 当日从 expiry 重算；过期行不会再被压入 0DTE。单边 Delta 缺失但同 expiry/strike 对侧 Delta 可用时，页面按 Put-Call parity 近似补全并明确显示 `Delta≈`。
- planner 的候选过滤继续执行 DTE、OTM、Strike、Delta 和 `bid > 0` 约束；候选排序仍是收益、分布、风险、目标贴合的综合分。
- ZC 单位已统一：页面展示的 1 美分/蒲式耳对应每张 50 美元；同时保留 IB 原始 `contractMultiplier=5000`，避免再把 ZC 市值放大 100 倍。
- DTE 统一使用 `America/New_York` 交易日，并优先按到期日重算，避免复用 CSV 中已经过期的 DTE 值。
- 已完成公网暴露静态安全审计，结论和整改清单见 `security_audit_report.md`；目前只是审计完成，安全整改尚未完成。

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
              |
              v
sell_side_inventory_planner.html        中文卖方库存规划与手动 what-if
```

核心 IB 会话通过 `ib_async` 以 `readonly=True` 连接；当前主线没有下单或撤单调用。`carry_risk_dashboard.html` 是旧的 carry 风险页面，`target_treasury_monitor_clean/app.py` 是 Streamlit 三页签入口，两者仍可用，但不是当前推荐的日常 planner 入口。

`A_Share_Option/`、`news_api/`、`prediction_market/` 和 `macro_calendar.py` 是独立旁支模块，不在这次美债库存规划器整理范围内。生成数据位于被 `.gitignore` 忽略的 `data/`，不能依赖 Git 保存或恢复运行快照。

## 关键文件

| 文件 | 作用 | 接手注意点 |
| --- | --- | --- |
| `README.md` | 当前日常启动、刷新模式、故障处理和测试说明 | 修改默认参数或入口时同步更新 |
| `refresh_inventory_data.py` | 推荐外层入口；负责 server、循环刷新、状态和 client-id 锁 | 不要让子 CLI 重复获取同一把锁 |
| `open_inventory_planner.sh` / `stop_inventory_planner.sh` | macOS `launchd` 启停 | 停止时必须先移除 job，否则会被自动拉起 |
| `open_inventory_planner.ps1` / `stop_inventory_planner.ps1` | Windows 后台启停 | 依赖仓库内 `.venv\Scripts\python.exe`，待实机验证 |
| `sell_side_inventory_planner.html` | 当前主 UI，JavaScript 全内嵌 | UI 行为由 Node smoke test 固化 |
| `target_treasury_monitor_clean/cli.py` | 所有 clean workflow 的 CLI 和 `refresh-carry-html` 实现 | 负责缓存回退、fast/full、发布与 readiness |
| `target_treasury_monitor_clean/inventory_planner_server.py` | 8766 HTTP 服务、manifest、刷新 API 和状态 API | 当前不适合公网暴露 |
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
3. 当前稳定 CSV 未达到完整就绪：ZF/ZN/ZC 的期权链新鲜，但三者 K 线均过期。fast refresh 有意保留已有 K 线，所以需要 full refresh 或单独刷新 bars 才能修复。
   另外，fast refresh 只主动刷新近端 DTE，远端缓存仍可能缺一侧 Greeks；页面可以用对侧 Delta 做明确标记的 parity 近似，但两侧 Delta 都缺失时仍不会进入候选。
4. 最近一次刷新日志出现 `ZF 202607` futures contract 的 “No security definition” 错误；流程仍靠后续持仓/缓存完成。需要清理已失效月份的合约引用或提高合约月份选择的稳健性。
5. 三个稳定 CSV 仍是直接覆盖写入，可能被并发读取到截断文件，或产生 positions/chain/bars 跨版本混合；目前只有 `refresh_status.json` 是原子写入。
6. HTTP 服务缺少认证、CSRF/Origin 校验、body/频率/任务数量限制、安全响应头和日志轮转；状态 API 还会返回 stdout、账户、路径及错误细节。
7. `target_treasury_monitor_clean/` 还没有真正消除旧层依赖；直接删除 `target_treasury_account_monitor/` 或 `treasury_fop_chain.py` 会破坏主流程。
8. 默认账户、月份（当前为 202609/202612）、端口和 client-id 偏运行环境化且会过期；接手时必须先检查，不要长期把当前月份当常量。
9. 依赖只有未锁版本的 `requirements_dashboard.txt`，没有完整 lockfile、CI、秘密扫描或依赖审计。
10. full refresh、PowerShell 后台生命周期和安全整改后的公网访问尚无端到端自动化验证；现有测试主要覆盖纯逻辑、本地 HTTP 和缓存降级。

## 下一步任务

按优先级建议：

1. 完成 `security_audit_report.md` 的 P0：停止无认证 Tunnel、轮换 FMP Key、将静态根改成最小发布目录、增加认证和刷新权限、限制请求/并发，并从状态响应中移除敏感信息。
2. 在 IB Gateway 可用时执行 full refresh 或单独刷新 K 线，再运行：

   ```bash
   conda run -n ib python -m target_treasury_monitor_clean.cli validate-carry-html \
     --data-dir data/planner --expected-products ZF,ZN,ZC --require-ready
   ```

   目标是 `ready_for_full_view: true`，同时排查 `ZF 202607` 无合约定义错误。
3. 将 positions/chain/bars 改为同一版本目录内的原子发布，再一次性切换 manifest；保留上一版本以便回滚。
4. 在 Windows 实机验证 `.ps1` 的启动、关闭、端口占用、日志、PID 文件和终端关闭后的常驻行为。
5. 审阅当前未提交差异，按“刷新可靠性 / ZC+DTE+planner UI / 跨平台脚本 / 安全审计”拆分提交；不要把 `data/` 或账户快照加入 Git。
6. 逐步把旧层底层函数迁入 `target_treasury_monitor_clean/`，每迁移一组先更新导入并跑全套测试，最后再删除旧目录。
7. 补齐固定版本依赖、CI（Python + Node smoke）、秘密扫描和安全回归测试；月份和账户配置改为环境变量或显式配置。

## 不得破坏的行为

- **绝不自动下单。** planner 只展示、筛选和做手动 what-if；IB 连接保持 `readonly=True`，不得加入 `placeOrder`/`cancelOrder` 调用。
- 核心库存只统计 `position < 0` 的期权；多头期权和非期权不进入核心卖方库存计算。
- 0DTE 只是普通 DTE 桶，不因来源被特殊分类，也不能自动标成高风险。
- Put 和 Call 必须分开统计与筛选；库存 DTE 只约束统计/规划，不能把范围外的真实当前持仓从“当前库存行权节点”隐藏。
- 候选 DTE 只控制候选矩阵；用户选中的 DTE 列即使没有合约也应保留。候选必须继续满足所选 DTE、OTM、Strike、Delta 和可卖 `bid > 0` 条件。
- 过期 expiry 不得折叠成 0DTE；DTE 列头日期和候选单元格必须使用同一套 expiry 重算结果。Parity 补全的 Delta 必须显示 `≈`，不得冒充 IB 直接 Greeks；bid 缺失仍不能成为可卖候选。
- 候选排序必须保留收益、分布、风险和目标贴合的综合评分，不能退化为只按最高权利金排序。
- 节点集中度只告警，不禁止用户覆盖；手动数量变化必须即时重算 Before / Added / After、压力场景和节点暴露。
- fast refresh 必须强制包含当前持仓 conId，只刷新近端/近价合约，并保留缓存中的远端链和已有 K 线；IB 暂时失败时，只能在有有效缓存且未启用 strict 参数时降级。
- 空持仓快照默认不得覆盖非空缓存；只有账户确认空仓并显式使用 `--allow-empty-positions` 时才允许发布空持仓。
- full refresh 必须覆盖更广的配置链，并在没有 `--skip-bars` 时尝试更新 K 线。
- 相同 `host + port + client-id` 不得并发刷新；状态文件必须保持完整 JSON，重复页面请求应复用正在运行的任务。
- 稳定数据文件名和 manifest/status API 兼容性不得随意改动：页面默认读取 `data/planner/carry_dashboard_{positions,chain,bars}.csv`。
- ZC 行权价按美分/蒲式耳展示；估值现金乘数必须是 50 美元/显示单位，同时保留 IB 原始 `contractMultiplier=5000`。ZF/ZN 的现有 1000 乘数语义不能受影响。
- DTE 必须以 `America/New_York` 交易日计算，并优先根据到期日重算，不能信任缓存中的旧 DTE。
- ZF、ZN、ZC 都是当前主流程支持品种；不能为了修一个品种破坏另外两个品种的解析、筛选或页面标签。
- `sell_side_inventory_planner.html` 与旧 `carry_risk_dashboard.html` 保持独立；未经明确设计不要把两套页面强行合并。
- 在 clean 层完全消除导入并通过全套测试前，不得删除 `target_treasury_account_monitor/` 或 `treasury_fop_chain.py`。
- 服务默认只绑定 `127.0.0.1`。安全 P0 完成前，不得改为 `0.0.0.0`，也不得通过无认证公网 Tunnel 暴露。
