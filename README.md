# IB API 本地工作区

这个仓库主要用于 IBKR 国债期货/期权数据刷新、本地 HTML 看板、卖方期权库存规划，以及少量新闻和 prediction market 辅助模块。

当前最常用的是：

- `sell_side_inventory_planner.html`：卖方期权库存规划器，浏览器页面。
- `refresh_inventory_data.py`：一键刷新 IB 数据，并可同时启动规划器本地服务。
- `open_inventory_planner.py`：只启动规划器页面，不刷新 IB 数据。
- `target_treasury_monitor_clean/cli.py`：底层 CLI，负责 IB 连接、期权链、K 线、CSV 发布。

## 一句话启动

macOS 上日常使用优先跑这个。本机当前有 `conda` 环境 `ib`，没有 `.venv` 目录：

```bash
cd /Users/antony/Desktop/IB-API
conda run -n ib python refresh_inventory_data.py --serve-planner --open-browser
```

如果你之后改用项目内 `.venv`，macOS/Linux 的 Python 路径是 `.venv/bin/python`：

```bash
cd /Users/antony/Desktop/IB-API
.venv/bin/python refresh_inventory_data.py --serve-planner --open-browser
```

Windows PowerShell 才使用下面这个：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --open-browser
```

成功后浏览器打开：

```text
http://127.0.0.1:8766/sell_side_inventory_planner.html
```

如果日志里看到下面这类请求，说明页面通信和数据加载是通的：

```text
"POST /api/refresh-inventory-data HTTP/1.1" 200
"GET /inventory-planner-defaults.json HTTP/1.1" 200
"GET /data/planner/carry_dashboard_chain.csv?... HTTP/1.1" 200
```

## 启动前准备

1. 先启动 TWS 或 IB Gateway。
2. 确认 IB API 已开启。
3. 默认连接参数：

```text
host: 127.0.0.1
port: 4001
client-id: 7316
market-data-type: delayed
```

4. 如果 `.venv` 不存在或依赖不完整：

macOS/Linux：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements_dashboard.txt
```

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe -m pip install -r .\requirements_dashboard.txt
```

## 当前完整启动脚本

### 1. 启动页面并刷新一次数据

macOS，本机推荐：

```bash
cd /Users/antony/Desktop/IB-API
conda run -n ib python refresh_inventory_data.py \
  --serve-planner \
  --open-browser \
  --planner-port 8766
```

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --planner-port 8766
```

这个命令会做三件事：

- 启动本地 planner server。
- 调用 IBKR 刷新持仓、ZF/ZN/ZC 期权链、期货 K 线。
- 发布 CSV 到 `data/planner/`，供 HTML 页面读取。

页面打开后，如果后台刷新仍在运行，会自动显示刷新进度和最近日志；从页面点 `刷新底层数据` 也会显示同样的进度。

### 2. 每 30 分钟自动刷新

macOS：

```bash
conda run -n ib python refresh_inventory_data.py \
  --serve-planner \
  --open-browser \
  --repeat-minutes 30
```

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --serve-planner `
  --open-browser `
  --repeat-minutes 30
```

停止时在终端按 `Ctrl+C`。

### 3. 只打开页面，不连接 IB

已有 CSV 数据时，用这个最快：

macOS，本机推荐：

```bash
./open_inventory_planner.sh
```

或者直接用 Python：

```bash
conda run -n ib python open_inventory_planner.py --port 8766
```

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\open_inventory_planner.py --port 8766
```

或者走底层 CLI：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe -m target_treasury_monitor_clean.cli serve-inventory-planner `
  --directory . `
  --host 127.0.0.1 `
  --port 8766 `
  --open
```

### 4. 没有 IB 连接，只复用已有持仓 CSV 调试

macOS：

```bash
conda run -n ib python refresh_inventory_data.py \
  --positions-csv data/planner/carry_dashboard_positions.csv \
  --serve-planner \
  --open-browser
```

Windows PowerShell：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --positions-csv data\planner\carry_dashboard_positions.csv `
  --serve-planner `
  --open-browser
```

## 数据输出位置

刷新后，HTML 默认读取：

```text
data/planner/carry_dashboard_positions.csv
data/planner/carry_dashboard_chain.csv
data/planner/carry_dashboard_bars.csv
```

调试过程中的中间文件默认写到：

```text
data/planner/debug/
```

页面会请求：

```text
inventory-planner-defaults.json
```

这个 JSON 由本地 server 动态生成，里面包含当前默认 CSV 路径和 `dataUpdatedAt` 数据刷新时间。

## 页面按钮说明

- `读取默认CSV`：重新读取 `data/planner/` 下的最新 CSV。
- `刷新底层数据`：从页面发起 `POST /api/refresh-inventory-data`，后端执行 `refresh_inventory_data.py`。
- `加载样例`：加载页面内置样例，不依赖 IB。
- `导出JSON / CSV / Markdown`：导出当前手动规划结果。

如果点击 `刷新底层数据` 出现 `501 Unsupported method ('POST')`，说明你用普通静态服务器打开了页面。请改用：

```powershell
.\.venv\Scripts\python.exe .\open_inventory_planner.py --port 8766
```

或：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --open-browser
```

## 常用刷新参数

默认刷新。项目默认账户已经固定为 `U16251798`：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py
```

指定 IB Gateway 端口：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --ib-port 4002
```

指定期权链月份：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --chain-specs "ZF=202609,202612;ZN=202609,202612" `
  --zc-chain-specs "ZC=202609,202612"
```

跳过 K 线刷新：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --skip-bars
```

刷新前先打印命令，不真正执行：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py `
  --dry-run
```

## Carry 风险看板

旧的 carry/risk HTML 看板仍然可以单独启动：

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe -m target_treasury_monitor_clean.cli serve-carry-html `
  --directory . `
  --host 127.0.0.1 `
  --port 8765 `
  --open
```

打开：

```text
http://127.0.0.1:8765/carry_risk_dashboard.html
```

## Streamlit 监控页

Streamlit 入口：

```powershell
cd E:\策略\IB-API
.\run_clean_treasury_monitor.ps1
```

默认打开：

```text
http://127.0.0.1:8503
```

## CLI 子命令

查看全部命令：

```powershell
.\.venv\Scripts\python.exe -m target_treasury_monitor_clean.cli --help
```

当前包含：

```text
dashboard-snapshot        抓一次账户快照
batch-chain               批量刷新静态期权链
live-chain                持续订阅近期期权链
future-bars               抓期货 OHLCV K 线
ib-smoke                  测试 IB 连接和合约识别
quality-report            检查期权链 CSV 质量
sync-carry-html           发布 CSV 给 carry HTML
sync-latest-carry-html    发布最新 notebook 输出给 carry HTML
validate-carry-html       检查 carry HTML 输入数据
serve-carry-html          启动 carry HTML 本地服务
serve-inventory-planner   启动库存规划器本地服务
refresh-carry-html        刷新持仓、期权链、K 线并发布 CSV
```

## 常见问题

### 1. 页面能打开，但刷新按钮报 501

原因：页面不是通过 inventory planner server 打开的，而是普通静态服务器。

解决：

```powershell
.\.venv\Scripts\python.exe .\open_inventory_planner.py --port 8766
```

### 2. 端口 8766 被占用

换端口：

```powershell
.\.venv\Scripts\python.exe .\open_inventory_planner.py --port 8767
```

或：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --serve-planner --planner-port 8767
```

### 3. 账户号怎么改

当前项目默认账户号是：

```text
U16251798
```

临时改成其他账户，可以传 `--account`：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --account 其他IB账户号
```

也可以用已有持仓 CSV 调试：

```powershell
.\.venv\Scripts\python.exe .\refresh_inventory_data.py --positions-csv data\planner\carry_dashboard_positions.csv
```

### 4. IB 连接失败

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

### 5. 提示 client-id 已经在刷新

刷新底层数据时会按 `host + port + client-id` 加本机进程锁。比如默认是：

```text
127.0.0.1:4001 client-id 7316
```

如果一个刷新还没结束，又从页面按钮或另一个终端发起同样 `client-id` 的刷新，新的刷新会直接退出并提示当前已有刷新在跑。处理方式：

- 等当前刷新完成后再点。
- 或者临时换一个 `--client-id`。
- 如果你明确要绕过本机锁，可以传 `--no-client-lock`，但 IB 仍然可能返回 `Error 326`。

### 6. 刷新耗时痛点

最近一次完整刷新样本：

```text
20:41:05 -> 20:43:22，约 137 秒
```

主要耗时来自期权链 market data 批量请求：

- ZF：576 张合约，4 个 batch。
- ZN：480 张合约，4 个 batch。
- ZC：72 张合约，1 个 batch。
- 期货 K 线：ZF/ZN/ZC 各 1 个月 30min bars，当前不是最大瓶颈。

后续可迭代方向：

- 对页面默认视图只刷新近端 DTE 和当前持仓相关 strike，远端链用缓存。
- ZF/ZN 分品种增量刷新，避免每次全量刷新 1000+ 张合约。
- 当前持仓合约强制加入刷新 universe，避免深 OTM 当前仓位不在候选链中。
- 将期权链刷新和 K 线刷新拆成独立按钮，日内频繁刷新时只跑必要部分。

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

注意：当前这个 smoke test 可能会因为默认 CSV 路径断言过旧而失败，和页面服务是否能启动不是同一类问题。

## 目录速查

```text
sell_side_inventory_planner.html                 卖方期权库存规划器页面
carry_risk_dashboard.html                        Carry 风险看板页面
refresh_inventory_data.py                        一键刷新数据/可同时启动 planner server
open_inventory_planner.py                        只启动 planner server
run_clean_treasury_monitor.ps1                   Streamlit 启动脚本
target_treasury_monitor_clean/cli.py             底层 CLI 入口
target_treasury_monitor_clean/inventory_planner_server.py  planner 本地 HTTP/API server
target_treasury_monitor_clean/inventory_planner.py         规划计算逻辑
data/planner/                                    HTML 默认读取的数据目录
data/planner/debug/                              刷新调试输出目录
news_api/                                        新闻相关模块
prediction_market/                               prediction market 相关模块
```
