# IB API 本地工作区

这个仓库主要用于 IBKR 国债期货/期权数据刷新、本地 HTML 看板、卖方期权库存规划，以及少量辅助模块。

当前最常用的页面是：

- `sell_side_inventory_planner.html`：卖方期权库存规划器。
- `refresh_inventory_data.py`：刷新 IB 数据，可同时启动本地 planner server。
- `open_inventory_planner.py`：只启动 planner 页面，不主动刷新 IB 数据。
- `target_treasury_monitor_clean/cli.py`：底层 CLI，负责 IB 连接、期权链、K 线、CSV 发布。

## 推荐启动与停止脚本

日常使用请只选一种启动方式。两个脚本都会启动 planner，并默认每 1 分钟执行一次“智能刷新”：按美国东部日期检查 ZF、ZN、ZC 的候选链，任一品种不是当天数据时先全量刷新；全部为当天数据时只刷新持仓期权和底层期货价。页面地址是：

```text
http://127.0.0.1:8766/sell_side_inventory_planner.html
```

macOS：

```bash
cd /Users/antony/Desktop/IB-API
./open_inventory_planner.sh
```

停止：

```bash
./stop_inventory_planner.sh
```

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\open_inventory_planner.ps1
```

停止：

```powershell
.\stop_inventory_planner.ps1
```

如果 Windows 因为执行策略拦截 `.ps1`，用下面这条一次性命令运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\open_inventory_planner.ps1
```

### macOS 与 Windows 的差别

| 项目 | macOS | Windows |
| --- | --- | --- |
| 启动脚本 | `open_inventory_planner.sh` | `open_inventory_planner.ps1` |
| 后台托管 | 当前登录用户的 `launchd` 任务 | 隐藏的后台 Python 进程 |
| 关闭当前终端后 | 继续运行 | 继续运行 |
| 注销或重启后 | 需要重新执行启动脚本 | 需要重新执行启动脚本 |
| 停止脚本 | 会移除 `launchd` 任务并停止 8766 | 会停止记录的 Python 进程和 8766 监听进程 |
| 日志 | `/tmp/ib_api_inventory_planner_8766.log` | `%TEMP%\ib_api_inventory_planner_8766.log` |

macOS 的脚本默认使用当前激活 Conda 环境中的 Python；若未激活环境，会依次寻找本机常见的 Conda 路径。也可以显式指定：

```bash
PLANNER_PYTHON=/path/to/python ./open_inventory_planner.sh
```

Windows 脚本固定使用仓库内的 `.venv\Scripts\python.exe`。

## 手动启动方式

下面的命令适合调试或只跑一次。它们以前台方式运行，关闭终端就会停止；需要常驻自动刷新时请使用上面的启动脚本。

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --open-browser
```

macOS：

```bash
conda run -n ib python refresh_inventory_data.py --serve-planner --open-browser
```

成功后浏览器打开：

```text
http://127.0.0.1:8766/sell_side_inventory_planner.html
```

这条手动命令会做三件事：

- 启动本地 planner server。
- 默认执行一次 `fast refresh`。
- 把最新 CSV 发布到 `data/planner/`，然后页面自动读取。

手动命令仍默认只执行一次 fast refresh；一键启动脚本使用的是 `scheduled` 模式和 1 分钟循环。

## 默认参数

当前默认账户已经固定为：

```text
U16251798
```

默认 IB 连接参数：

```text
host: 127.0.0.1
port: 4001
client-id: 7316
market-data-type: delayed
```

默认刷新参数：

```text
refresh-mode: fast
repeat-minutes: 0
planner-port: 8766
```

`repeat-minutes: 0` 的意思是：只刷新一次，不自动循环。

## Fast、Full 和 Scheduled Refresh

外层脚本 `refresh_inventory_data.py` 使用这个参数：

```text
--refresh-mode {fast,full,scheduled}
```

默认是：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode fast
```

也就是你不写 `--refresh-mode`，它也是 fast。

### Fast Refresh（持仓快刷）

用于日内高频更新。特点：

- 账户持仓期权通过 `reqMktData(..., snapshot=False)` 的流式行情订阅更新。
- 不扫描候选期权链，也不重新请求近端或近价候选合约。
- 刷新 ZF、ZN、ZC 所选期货月份的底层价格。
- 原样保留已有候选期权链和 K 线。
- 如果已有 K 线数据，默认保留旧 K 线，不每次重刷 bars。
- 适合页面“持仓快刷”和分钟级日常监控。

当前实现每轮会建立 IB 连接、订阅持仓行情，取得结果后关闭连接；它不是跨刷新周期永久保持的长连接。

命令：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode fast
```

### Full Refresh

用于盘前、缓存不可信、或者需要重建更完整期权链时。特点：

- 刷新配置月份内、经过 DTE/Strike/价内外等既有 filter 选中的全部候选期权。
- 同时刷新账户持仓与底层期货价。
- 会按配置刷新 bars，除非你显式传 `--skip-bars`。
- 耗时明显更长。

命令：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode full
```

等价快捷写法：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --full-refresh
```

### Scheduled Refresh（智能刷新）

适合常驻启动：

- 以 `America/New_York` 的当前日期作为“当天”，不使用北京时间判断。
- 分别读取 ZF、ZN、ZC 候选链行中的最新 `snapshotTimeUtc`；三个品种都为当天才执行 fast。
- 任一品种缺失或日期落后，执行一次 full；如果某个品种全量刷新失败、日期仍旧，下一轮会继续尝试 full。
- fast 虽然会重新发布缓存链，但不会用文件修改时间冒充数据日期。

命令：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode scheduled
```

### 外层参数和底层参数的关系

你可能会在不同文件里看到两个名字：

```text
refresh_inventory_data.py --refresh-mode fast
target_treasury_monitor_clean.cli refresh-carry-html --fast-refresh
```

关系是：

```text
外层 --refresh-mode fast
        ↓
自动转换成底层 --fast-refresh
```

日常使用只需要记住外层的 `--refresh-mode fast/full/scheduled`。

## 自动刷新时间间隔

刷新模式本身不是时间间隔；循环间隔由 `--repeat-minutes` 控制。

只刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode fast
```

每 1 分钟智能刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --refresh-mode scheduled `
  --repeat-minutes 1
```

每 30 分钟快速刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --refresh-mode fast `
  --repeat-minutes 30
```

终端里看到：

```text
sleeping 1800s; press Ctrl+C to stop
```

表示当前是每 30 分钟刷新一次，因为 1800 秒 = 30 分钟。

## 页面按钮说明

页面顶部有几个常用按钮：

- `读取默认CSV`：只重新读取 `data/planner/` 下的 CSV，不连接 IB。
- `持仓快刷`：刷新持仓期权的流式订阅行情和底层期货价，不扫描候选链。
- `全量刷新`：后端执行 `refresh_inventory_data.py --refresh-mode full`。
- `智能刷新`：按美国东部日期自动选择 fast 或 full。
- `刷新合约月份`：分别选择 ZF、ZN、ZC 要刷新的底层期货月份；默认三者都只刷新 `202609`，也可切换到 `202612` 或同时刷新9月和12月。
- `加载样例`：加载页面内置样例，不依赖 IB。
- `导出JSON / CSV / Markdown`：导出当前手动规划结果。

页面刷新按钮不是定时器。点一次就刷新一次。

所选月份只约束普通期权行情订阅；真实持仓的 conId 即使不在所选月份，也会继续强制刷新，避免月份选择隐藏当前风险。

### Dashboard 交易、保证金预检与安全边界

页面在“底层资产走势”下方提供独立交易台。行情和交易分成两个进程：8766 planner 继续负责持仓与实时/延迟行情；8767 trade gateway 只在用户显式启动后负责 IB 原生 What-If、最终复核、提交和撤单。

- 候选期权卡片中的 `选择预检` 或期货下拉框会建立订单草稿；第一版只允许 CBOT 的 `FUT/FOP`、`DAY`、`LMT`，市价单硬禁用。
- trade gateway 默认是 Paper 模式，只绑定 `127.0.0.1:8767`，不允许公网地址；浏览器来源只接受本机 8766 Dashboard。
- 每次进程生成随机解锁码。浏览器解锁会话只保存在内存，最多保持十分钟；一次提交或撤单后服务端立即自动锁定。
- 保证金预览生成 SHA-256 订单指纹、短时效 preview-id 和动态确认词。提交接口不接受新的合约、方向、数量或价格，只接受原 preview-id、指纹和逐字确认词，因此页面字段变更不能偷换最终订单。
- 最终发送前，服务端会对存储的同一订单重新执行一次 IB What-If。预留资金不足、IB warning、预览/会话过期或指纹变化都会阻止发送。
- Live 模式还要求交易服务为所选合约取得 `marketDataType=1` 的有效 bid/ask/last；延迟、冻结或缺失行情会阻止发送。页面原有行情状态仍会显示实时/延迟，但最终许可以交易服务自己的 IB 行情检查为准。
- 发送尝试会先消费 preview-id；网络结果不确定时自动锁定且禁止重试，避免重复订单。活动订单只显示并允许撤销带 `IBDASH:` orderRef、由本 Dashboard client-id 创建的订单。
- 所有解锁、预览、发送和撤单事件写入权限为 `0600` 的 `data/planner/trading_audit.jsonl`；解锁码和会话 token 不写日志。
- planner 自带的 `POST /api/margin-whatif` 仍固定返回 403；所有订单协议只存在于显式启动的独立 trade gateway。

先用 Paper Gateway/TWS 验证：

```bash
IB_ACCOUNT=你的Paper账户 ./open_trade_gateway.sh paper
```

脚本会要求再次输入 `PAPER <账户>`，随后在前台显示本次进程解锁码。把该码粘贴到 Dashboard，完成“生成保证金预览 → 核对指纹/保证金 → 逐字输入动态确认词 → 复核并发送”。Paper 账户是模拟环境，保证金或成交行为可能与 Live 不完全一致。

Live 模式必须显式配置正数资金缓冲，并再次输入 `LIVE <账户>`：

```bash
IB_ACCOUNT=你的Live账户 \
IB_MINIMUM_RESERVE_FUNDS=10000 \
IB_MAX_ORDER_QUANTITY=5 \
./open_trade_gateway.sh live
```

Live trade gateway 需要 Gateway/TWS 允许 API 订单。`ib_async.connect(readonly=True)` 只影响连接初始化时的订单同步，不是权限控制；Gateway 的 Read-Only 复选框才是全局硬阻断。关闭它以后，同一个 Gateway 端口上的所有 API client 都失去这层硬阻断。更强的生产隔离方式是为行情和交易使用不同 IB 用户/会话与不同端口，并只给交易用户必要的产品权限。不要把 trade gateway 作为 launchd 常驻任务，也不要将 8767 暴露到局域网或公网。

planner server 另用一个独立只读 client-id 常驻订阅当前持仓和指定期货月份；通过 `refresh_inventory_data.py --serve-planner` 启动时默认是刷新 client-id 加 2，单独启动 `open_inventory_planner.py` 时默认是 `7318`，可用 `--stream-client-id` 或 `IB_STREAM_CLIENT_ID` 修改。流服务请求 delayed fallback：账户具备实时权限时 IB 仍会返回 `marketDataType=1`，否则返回 `3` 延迟行情；发生实时会话冲突、Gateway 断线重连或登录账户变化时会自动重订阅，并在配置账户不可用时回退到当前 managed account 中实际持有 ZF/ZN/ZC 的账户。

如果你要定时刷新，需要在启动命令里使用 `--repeat-minutes`。

## 脚本参数

两套启动脚本的默认配置均为：

```text
REFRESH_MINUTES=1
CLIENT_ID=7316
PORT=8766
```

macOS 自定义每 5 分钟刷新一次：

```bash
REFRESH_MINUTES=5 IB_CLIENT_ID=7316 ./open_inventory_planner.sh
```

Windows 自定义每 5 分钟刷新一次：

```powershell
.\open_inventory_planner.ps1 -RefreshMinutes 5 -ClientId 7316
```

两边都支持把端口作为第一个参数或 `-Port` 参数传入；端口变更后，停止时也要传同一个端口。

## 只启动页面和持仓行情流，不执行批量刷新

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\open_inventory_planner.py --port 8766
```

macOS/Linux：

```bash
conda run -n ib python open_inventory_planner.py --port 8766
```

这个方式会启动页面/API server，并常驻订阅当前持仓和页面所选的 ZF/ZN/ZC 期货月份；不会执行候选期权链、K 线或 CSV 的批量刷新。页面每秒读取一次 `/api/live-positions`，只重算持仓相关模块；候选链与 K 线继续使用已发布 CSV。

## 常用刷新命令

默认快速刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py
```

启动页面并快速刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --open-browser
```

启动页面并每 1 分钟智能刷新：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --refresh-mode scheduled `
  --repeat-minutes 1
```

强制全量刷新：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --full-refresh
```

跳过 K 线刷新：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --skip-bars
```

指定 IB Gateway 端口：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --ib-port 4002
```

临时换账户：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --account 其他IB账户号
```

只复用已有持仓 CSV 调试：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --positions-csv data\planner\carry_dashboard_positions.csv `
  --serve-planner `
  --open-browser
```

打印将要执行的底层命令，但不真正刷新：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --dry-run
```

查看完整参数：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --help
```

## 数据输出位置

HTML 默认读取：

```text
data/planner/carry_dashboard_positions.csv
data/planner/carry_dashboard_chain.csv
data/planner/carry_dashboard_bars.csv
```

刷新状态文件：

```text
data/planner/refresh_status.json
```

调试输出目录：

```text
data/planner/debug/
```

页面会请求：

```text
/inventory-planner-defaults.json
```

这个 JSON 由本地 planner server 动态生成，里面包含当前默认 CSV 路径和 `dataUpdatedAt` 数据刷新时间。

## 如何判断刷新真的在跑

点击页面 `持仓快刷` 后，终端或日志里会看到类似：

```text
refresh request:
  requested_mode: fast
  effective_mode: fast
refresh positions/account snapshot
refresh option chains
ZF fast refresh: candidate-chain quote scan skipped
ZN fast refresh: candidate-chain quote scan skipped
ZC fast refresh: candidate-chain quote scan skipped
published: data/planner/carry_dashboard_chain.csv
refresh finished
```

页面会轮询：

```text
/api/refresh-inventory-data/status
/api/live-positions
```

`/api/live-positions` 返回持仓/指定期货内存快照、`sampledAt`、订阅数量、当前账户和 IB `marketDataType`。页面在“组合资产总览”标题旁显示具体本地获取时间及“实时行情 / 延迟行情 / 混合 / 已断开”；这个状态来自 IB 实际返回类型，不根据启动参数猜测。延迟行情会明确标注约 15–20 分钟；`sampledAt` 是程序收到/采样报价的时间，不是交易所成交时间，因此不会机械减去一个固定延迟值。

如果 `data/planner/refresh_status.json` 里是：

```json
{
  "ok": true,
  "running": false,
  "returncode": 0,
  "progress": 100,
  "durationSeconds": 9.3,
  "requestedMode": "fast",
  "effectiveMode": "fast"
}
```

说明刷新已经成功完成。

## 常见问题

### 1. 页面能打开，但刷新按钮报 501

原因：你用普通静态服务器打开了 HTML，那个服务器不支持 `POST /api/refresh-inventory-data`。

解决：

```powershell
.\.venv\Scripts\python.exe .\open_inventory_planner.py --port 8766
```

或者：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --open-browser
```

### 2. 端口 8766 被占用

换一个 planner 端口：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --planner-port 8767
```

### 3. 提示 client-id 已经在刷新

刷新时会按 `host + port + client-id` 加本机进程锁。

默认锁对象是：

```text
127.0.0.1:4001 client-id 7316
```

如果一个刷新还没结束，又用同一个 client-id 发起刷新，新刷新会退出。

处理方式：

- 等当前刷新完成。
- 临时换一个 `--client-id`。
- 确认没有旧进程残留。

### 4. IB 连接失败

如果 TWS / IB Gateway 没启动，`fast refresh` 会尽量降级处理：

- positions 刷新失败时，复用已有 `carry_dashboard_positions.csv`。
- option chain 连接失败时，复用已有 `carry_dashboard_chain.csv`。
- fast 模式下如果已有 bars，继续保留 `carry_dashboard_bars.csv`。
- 终端会打印 warning，但不会因为 Gateway 没开就直接 traceback。

如果你希望这种情况直接失败，可以使用 `--strict-positions`、`--strict-chain` 或 `--strict-bars`。

先跑 smoke test：

```powershell
.\.venv\Scripts\python.exe -m target_treasury_monitor_clean.cli ib-smoke `
  --host 127.0.0.1 `
  --port 4001 `
  --client-id 7316
```

检查：

- TWS / IB Gateway 是否启动。
- API 是否启用。
- 端口是 `4001`、`4002`、`7496` 还是 `7497`。
- `client-id` 是否和其他程序冲突。

### 5. 页面数据没变

先看刷新状态：

```powershell
Get-Content .\data\planner\refresh_status.json
```

再看 CSV 修改时间：

```powershell
Get-ChildItem .\data\planner -File | Sort-Object LastWriteTime -Descending
```

如果 CSV 已更新，但页面没变，点击页面的 `读取默认CSV`，或者刷新浏览器标签页。

## 测试

Python 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest test_refresh_inventory_data.py -v
.\.venv\Scripts\python.exe -m unittest test_inventory_planner.py -v
```

HTML smoke test：

```powershell
node test_inventory_planner_dashboard_smoke.js
```

Python 编译检查：

```powershell
.\.venv\Scripts\python.exe -m py_compile `
  refresh_inventory_data.py `
  open_inventory_planner.py `
  target_treasury_monitor_clean\inventory_planner_server.py `
  target_treasury_monitor_clean\cli.py `
  target_treasury_monitor_clean\inventory_planner.py
```

## 目录速查

```text
sell_side_inventory_planner.html                 卖方期权库存规划器页面
refresh_inventory_data.py                        一键刷新数据，可同时启动 planner server
open_inventory_planner.py                        只启动 planner server
open_inventory_planner.sh                        macOS launchd 启动和自动 fast refresh 脚本
stop_inventory_planner.sh                        停止 macOS planner 刷新脚本
open_inventory_planner.ps1                       Windows 后台启动和自动 fast refresh 脚本
stop_inventory_planner.ps1                       停止 Windows planner 刷新脚本
target_treasury_monitor_clean/cli.py             底层 CLI 入口
target_treasury_monitor_clean/ib_client_lock.py  IB client-id 本机进程锁
target_treasury_monitor_clean/inventory_planner_server.py  planner 本地 HTTP/API server
target_treasury_monitor_clean/inventory_planner.py         规划计算逻辑
data/planner/                                    HTML 默认读取的数据目录
data/planner/debug/                              刷新调试输出目录
news_api/                                        新闻相关模块
prediction_market/                               prediction market 相关模块
```
