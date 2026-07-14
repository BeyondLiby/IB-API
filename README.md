# IB API 本地工作区

这个仓库主要用于 IBKR 国债期货/期权数据刷新、本地 HTML 看板、卖方期权库存规划，以及少量辅助模块。

当前最常用的页面是：

- `sell_side_inventory_planner.html`：卖方期权库存规划器。
- `refresh_inventory_data.py`：刷新 IB 数据，可同时启动本地 planner server。
- `open_inventory_planner.py`：只启动 planner 页面，不主动刷新 IB 数据。
- `target_treasury_monitor_clean/cli.py`：底层 CLI，负责 IB 连接、期权链、K 线、CSV 发布。

## 最常用启动方式

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --open-browser
```

macOS/Linux：

```bash
cd /Users/antony/Desktop/IB-API
conda run -n ib python refresh_inventory_data.py --serve-planner --open-browser
```

成功后浏览器打开：

```text
http://127.0.0.1:8766/sell_side_inventory_planner.html
```

这条命令会做三件事：

- 启动本地 planner server。
- 默认执行一次 `fast refresh`。
- 把最新 CSV 发布到 `data/planner/`，然后页面自动读取。

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

## Fast Refresh 和 Full Refresh

外层脚本 `refresh_inventory_data.py` 使用这个参数：

```text
--refresh-mode {fast,full}
```

默认是：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode fast
```

也就是你不写 `--refresh-mode`，它也是 fast。

### Fast Refresh

用于日内高频更新。特点：

- 只请求近端 DTE、现价附近、当前持仓相关合约。
- 当前持仓 conId 会强制加入刷新 universe。
- 保留缓存里的远端期权链。
- 如果已有 K 线数据，默认保留旧 K 线，不每次重刷 bars。
- 适合页面按钮刷新、每几分钟自动刷新。

命令：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode fast
```

### Full Refresh

用于盘前、缓存不可信、或者需要重建更完整期权链时。特点：

- 请求更完整的 configured option chain。
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

日常使用只需要记住外层的 `--refresh-mode fast/full`。

## 自动刷新时间间隔

`fast refresh` 本身不是时间间隔，它只是刷新模式。

循环间隔由 `--repeat-minutes` 控制。

只刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --refresh-mode fast
```

每 3 分钟快速刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --refresh-mode fast `
  --repeat-minutes 3
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
- `快速刷新`：从页面发起 `POST /api/refresh-inventory-data`，后端执行 `refresh_inventory_data.py --refresh-mode fast`。
- `全量刷新`：后端执行 `refresh_inventory_data.py --refresh-mode full`。
- `加载样例`：加载页面内置样例，不依赖 IB。
- `导出JSON / CSV / Markdown`：导出当前手动规划结果。

页面刷新按钮不是定时器。点一次就刷新一次。

如果你要定时刷新，需要在启动命令里使用 `--repeat-minutes`。

## macOS 一键脚本

`open_inventory_planner.sh` 会启动 planner server，打开页面，并按间隔自动快速刷新。

默认配置：

```text
REFRESH_MINUTES=3
IB_CLIENT_ID=7316
PORT=8766
```

启动：

```bash
./open_inventory_planner.sh
```

自定义每 5 分钟刷新一次：

```bash
REFRESH_MINUTES=5 IB_CLIENT_ID=7316 ./open_inventory_planner.sh
```

停止后台 server 和自动刷新循环：

```bash
./stop_inventory_planner.sh
```

## 只打开页面，不刷新 IB

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\open_inventory_planner.py --port 8766
```

macOS/Linux：

```bash
conda run -n ib python open_inventory_planner.py --port 8766
```

这个方式只提供页面和 API server，不会主动连接 IB 刷新数据。

## 常用刷新命令

默认快速刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py
```

启动页面并快速刷新一次：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --open-browser
```

启动页面并每 3 分钟快速刷新：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --repeat-minutes 3
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

点击页面 `快速刷新` 后，终端或日志里会看到类似：

```text
refresh request:
  mode: fast
refresh positions/account snapshot
refresh option chains
refresh chain: ZF months=...
refresh chain: ZN months=...
published: data/planner/carry_dashboard_chain.csv
refresh finished
```

页面会轮询：

```text
/api/refresh-inventory-data/status
```

如果 `data/planner/refresh_status.json` 里是：

```json
{
  "ok": true,
  "running": false,
  "returncode": 0,
  "progress": 100
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
open_inventory_planner.sh                        macOS/Linux 后台启动和自动 fast refresh 脚本
stop_inventory_planner.sh                        停止后台 planner 刷新脚本
target_treasury_monitor_clean/cli.py             底层 CLI 入口
target_treasury_monitor_clean/ib_client_lock.py  IB client-id 本机进程锁
target_treasury_monitor_clean/inventory_planner_server.py  planner 本地 HTTP/API server
target_treasury_monitor_clean/inventory_planner.py         规划计算逻辑
data/planner/                                    HTML 默认读取的数据目录
data/planner/debug/                              刷新调试输出目录
news_api/                                        新闻相关模块
prediction_market/                               prediction market 相关模块
```
