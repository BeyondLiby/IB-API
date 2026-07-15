# 公网暴露安全审计报告

审计日期：2026-07-14
审计目录：`/Users/antony/Desktop/IB-API`
审计方式：只读静态代码审计 + 只读进程/监听端口检查。未启动或停止服务，未发送 HTTP 请求，未安装依赖，未访问外网，未修改文件。

说明：下文“Tunnel 可达：是*”表示一旦 Cloudflare Tunnel 按题设转发到 `127.0.0.1:8766` 即可触达。审计现场未观察到可确认的 `cloudflared` 进程，因此无法证明 Tunnel 此刻正在运行。

## 1. 结论

### 是否适合直接通过无登录的 Cloudflare Quick Tunnel 暴露

**不适合。**

8766 服务把整个项目根目录作为静态目录，公网用户可下载后端源码、`.git`、notebook、SQLite、CSV、Excel、配置和调试数据。同时存在无认证刷新接口，可启动固定本地 Python 刷新进程、访问本机 IB Gateway，并覆盖项目数据。

### 是否应立即停止公网访问

**是。**

如果当前存在指向 8766 的无认证 Tunnel，应立即停止，完成至少 P0 项目后再开放。

### 综合风险等级

**Critical**

### 最重要的三个风险

1. **整个仓库、Git 历史、密钥和业务数据可被匿名下载。**
2. **匿名 POST 可以启动本地刷新进程、访问 `127.0.0.1:4001` 并覆盖数据。**
3. **缺少请求体、并发、任务、日志和刷新频率限制，可能耗尽 Mac 的线程、内存、磁盘和 IB 连接资源。**

## 2. 项目架构与攻击面

### 2.1 服务架构

| 组件 | 入口/监听 | 行为 |
|---|---|---|
| 8766 主服务 | [open_inventory_planner.py](/Users/antony/Desktop/IB-API/open_inventory_planner.py:9) | 默认将脚本所在仓库根目录传给服务 |
| HTTP 实现 | [inventory_planner_server.py](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:174) | `SimpleHTTPRequestHandler` + `ThreadingHTTPServer` |
| 当前绑定 | `127.0.0.1:8766` | `lsof` 已确认，进程工作目录为项目根 |
| 定时刷新 | [open_inventory_planner.sh](/Users/antony/Desktop/IB-API/open_inventory_planner.sh:50) | 默认每 3 分钟启动一次刷新 |
| 本机 IB 服务 | `127.0.0.1:4001` | 项目客户端以 `readonly=True` 连接 |
| IB Gateway 实际绑定 | `*:4001` | 运行态检查发现为通配地址；外部可达性取决于防火墙和 Gateway 配置 |
| Streamlit 辅助服务 | 默认 `127.0.0.1:8503` | 本次未发现监听 |
| A 股期权服务 | 默认 `127.0.0.1:8777` | 本次未发现监听 |

### 2.2 8766 所有 HTTP 路由

| 方法 | 路径 | 行为 | 鉴权 | 副作用 |
|---|---|---|---|---|
| GET | `/inventory-planner-defaults.json` | 扫描数据目录并读取期权链 CSV | 无 | 读文件、消耗 CPU/I/O |
| GET | `/api/refresh-inventory-data/status?job=latest` | 返回当前状态、stdout、日志行、错误信息 | 无 | 无业务写入 |
| GET | `/api/refresh-inventory-data/status?job=<id>` | 返回内存中的刷新任务 | 无 | 无业务写入 |
| POST | `/api/refresh-inventory-data` | 启动 `fast` 或 `full` 刷新进程 | 无 | 启动本地命令、访问 IB、写 CSV/JSON |
| GET/HEAD | `/<任意现存文件或目录>` | 静态下载或目录列表 | 无 | GET 会写服务器访问日志 |
| POST | 其他路径 | 404 | 无 | 无 |
| PUT/PATCH/DELETE/OPTIONS | 任意路径 | 默认 501 | 无 | 无 |

可直接打开的 HTML 页面包括：

- `/sell_side_inventory_planner.html`
- `/carry_risk_dashboard.html`
- `/A_Share_Option/dashboard.html`
- `/A_Share_Option/arbitrage_monitor.html`

由于存在通配静态路由，以下也直接可下载或浏览：

- `/.git/`、`/.git/config`、`/.git/objects/...`
- `/data/`、`/data/planner/`、`/data/planner/debug/`
- 所有 `.py`、`.sh`、`.ps1`、`.js`、`.ipynb`
- SQLite、CSV、JSON、XLSX、`__pycache__` 和 `.DS_Store`

### 2.3 文件读写和数据库

**读取：**

- 整个仓库静态读取。
- `data/macro_calendar.sqlite` 可直接下载。
- 当前仓库包含 61 个数据 CSV、一个 SQLite、多个 JSON、一个 Excel 文件。
- 存在非空的历史持仓数据：`data/carry_dashboard_positions.csv`、`data/clean_verify/dashboard_treasury_positions.csv` 和 `A_Share_Option/positions.json`。

**写入：**

- `data/planner/refresh_status.json`：使用临时文件加 `os.replace`，是原子的。
- `data/planner/*.csv` 和 `data/planner/debug/*.csv`：直接 `to_csv` 覆盖，不是原子的。
- `/tmp/ib_api_client_locks/`：IB 客户端锁和元数据。
- `/tmp/ib_api_inventory_planner_8766*.log`：服务日志，无轮转。

**外部/本机调用：**

- 8766 的匿名刷新只会连接固定的 `127.0.0.1:4001`。
- 宏观日历、Bark、新闻等外部网络调用不在 8766 HTTP 请求调用链中。
- 未发现当前 HTTP 请求可控制任意 URL 的 SSRF。

## 3. 漏洞发现

### F-01：整个项目根目录被作为静态目录公开

- **严重程度：Critical**
- **置信度：High**
- **Tunnel 可达：是\***

**证据与行为：**

默认目录是 `open_inventory_planner.py` 所在仓库根目录：[open_inventory_planner.py:11](/Users/antony/Desktop/IB-API/open_inventory_planner.py:11)。随后该目录直接传给 `SimpleHTTPRequestHandler`：[inventory_planner_server.py:323](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:323)、[inventory_planner_server.py:333](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:333)。

标准处理器会列出没有 `index.html` 的目录，且不会过滤点文件。

**攻击请求：**

```text
GET /
GET /.git/HEAD
GET /.git/config
GET /macro_calendar.py
GET /data/macro_calendar.sqlite
GET /data/carry_dashboard_positions.csv
GET /verify_clean_workflows.ipynb
GET /A_Share_Option/A股期权信息.xlsx
```

**实际影响：**

- 下载全部后端源码和当前未提交源码。
- 下载完整 Git 对象和历史，重建历史版本。
- 下载业务规则、账户标识、持仓、期权链、调试数据和数据库。
- 下载硬编码 API Key。
- 未来若根目录出现 `.env`、日志、备份或私钥，也会自动暴露；`.gitignore` 不会阻止 HTTP 访问。

**能力判断：**

- 读取源码：**是**
- 修改源码：**否**
- 修改数据：**否，仅凭静态 GET 不行**
- 执行命令：**否**

**最小修复：**

不要把仓库根目录传给静态处理器。创建专用发布目录，只允许所需 HTML/CSS/JS 和经过确认的脱敏数据；禁止目录列表、点文件和未知路径。

**长期修复：**

使用明确路由和文件白名单；对每个路径做 `resolve()` 后的根目录包含校验；拒绝符号链接、目录请求和未声明扩展名。敏感数据必须经鉴权 API 返回，不应放在静态目录。

**验证修复：**

允许的页面应为 200；以下路径及大小写、编码、尾斜杠变体均应为 403/404：

```text
/
/.git/
/.git%2fHEAD
/%2e%2e/
/macro_calendar.py
/data/macro_calendar.sqlite
/__pycache__/
```

另外创建指向项目外测试文件的符号链接，确认无法读取。

---

### F-02：匿名刷新接口可启动本地命令并修改数据

- **严重程度：High**
- **置信度：High**
- **Tunnel 可达：是\***

**证据与行为：**

POST 路由没有认证检查：[inventory_planner_server.py:208](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:208)。

请求体解析失败会被当作空对象，默认执行 `fast`：[inventory_planner_server.py:218](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:218)。命令固定为当前 Python、固定脚本和白名单模式：[inventory_planner_server.py:238](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:238)，随后启动 subprocess：[inventory_planner_server.py:269](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:269)。

刷新会读取账户、行情并覆盖数据：[cli.py:711](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/cli.py:711)、[cli.py:728](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/cli.py:728)、[cli.py:894](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/cli.py:894)。

IB 客户端连接使用 `readonly=True`：[ib_session.py:12](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/ib_session.py:12)。全项目未发现 `placeOrder`、`cancelOrder` 等下单调用。

**攻击请求：**

```http
POST /api/refresh-inventory-data
Content-Length: 0
```

或：

```json
{"mode":"full"}
```

这也是 CSRF：第三方网页可用空的 `no-cors` POST 触发，不需要读取响应。

**实际影响：**

- 启动固定本地 Python 命令。
- 间接调用本机 IB Gateway。
- 刷新账户、行情和合约数据。
- 覆盖项目 CSV/JSON。
- 消耗行情订阅、CPU、网络和磁盘。
- 可能将刚刷新的敏感数据进一步暴露给公网静态路由。

**能力判断：**

- 读取源码：**否，需结合 F-01**
- 修改源码：**否**
- 修改数据：**是**
- 执行本地命令：**是，但仅固定刷新命令**
- 任意命令注入/RCE：**未发现**

**最小修复：**

立即禁用公网刷新接口，或要求强随机服务端密钥；严格要求 JSON Content-Type、合法 Origin 和 CSRF Token；解析失败必须返回 400，不能默认执行 fast。

**长期修复：**

将刷新 worker 与只读展示服务拆分；使用认证、角色权限、受控任务队列、审计日志、刷新配额和单任务锁。公网查看者不应拥有刷新权限。

**验证修复：**

匿名、无 Origin、错误 Content-Type、空 body、错误 CSRF Token 均应返回 401/403/415，且不得出现新进程、状态文件 mtime 变化或 CSV 变化。授权请求只能产生一个任务，重复请求应返回 409/429。

---

### F-03：硬编码 API Key 通过源码和 Git 历史泄露

- **严重程度：High**
- **置信度：High；密钥是否仍有效需要动态验证**
- **Tunnel 可达：是\***

**证据与行为：**

[macro_calendar.py:15](/Users/antony/Desktop/IB-API/macro_calendar.py:15) 包含一个 32 字符的 FMP API Key，脱敏片段为 `Ypb…oVL`。该文件被 Git 跟踪，历史扫描也确认该密钥模式存在于 Git 历史。

账户标识还硬编码于 [settings.py:14](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/settings.py:14) 和 [README.md:101](/Users/antony/Desktop/IB-API/README.md:101)。

**攻击请求：**

```text
GET /macro_calendar.py
GET /.git/objects/...
GET /README.md
```

**实际影响：**

密钥可能被用于消耗 API 配额、产生费用或访问对应服务。即使从当前文件删除，公开的 `.git` 仍可能恢复旧值。

**能力判断：**

- 读取源码/密钥：**是**
- 修改源码或数据：**否**
- 执行命令：**否**

**最小修复：**

立即在提供商处撤销并轮换该 Key；从代码中删除，改为环境变量。应按“已经泄露”处理，而不是只等待访问证据。

**长期修复：**

使用系统钥匙串或 Secret Manager；CI 加入 Git 历史秘密扫描；重写含密钥的 Git 历史并重新克隆部署副本。

**验证修复：**

旧 Key 必须失效；当前树和所有 Git revision 的秘密扫描均应无结果；HTTP 不能再访问 `.git` 或源文件。

---

### F-04：多种无界资源消耗可造成拒绝服务

- **严重程度：High**
- **置信度：High；Cloudflare 实际限额需要动态验证**
- **Tunnel 可达：是\***

**证据与行为：**

- 无请求体上限，直接按攻击者指定的 `Content-Length` 读取：[inventory_planner_server.py:220](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:220)。
- 每连接创建线程：[inventory_planner_server.py:333](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:333)。
- `jobs` 字典永久保存完成任务：[inventory_planner_server.py:174](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:174)。
- 每个任务无限保存全部 stdout：[inventory_planner_server.py:269](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:269)。
- 单次刷新可运行 900 秒：[inventory_planner_server.py:294](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:294)。
- 清单请求会反复扫描目录并读取 CSV：[inventory_planner_server.py:40](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:40)。
- 启动脚本将日志持续写入固定 `/tmp` 文件且无轮转：[open_inventory_planner.sh:16](/Users/antony/Desktop/IB-API/open_inventory_planner.sh:16)、[open_inventory_planner.sh:58](/Users/antony/Desktop/IB-API/open_inventory_planner.sh:58)。

审计时两个日志均已约 380 KB，并持续更新。

**攻击请求：**

- 多连接发送巨大 `Content-Length` 但缓慢上传。
- 并发下载 `.git` 和数据目录。
- 顺序重复触发 `full` 刷新。
- 高频请求目录、manifest 和 status。

**实际影响：**

线程耗尽、内存增长、日志占满磁盘、IB 连接拥塞、Python 服务或整个 Mac 响应迟缓。

**能力判断：**

- 修改源码：**否**
- 修改数据：**可通过刷新间接发生**
- 执行命令：**固定刷新命令**
- 造成系统不可用：**是**

**最小修复：**

设置很小的请求体上限，例如 16–64 KB；设置连接/读取超时；限制并发线程；限制每 IP 和每账户刷新频率；禁用公网 full；轮转日志；限制任务输出和保留数量。

**长期修复：**

使用成熟反向代理和生产级应用服务器；刷新交给有界任务队列；设置进程 CPU、内存、文件描述符和磁盘配额。

**验证修复：**

只能在隔离测试环境做压测：超限 body 应立即 413，超频应 429，线程和内存应保持有界，日志应轮转，重复刷新不得产生多个 worker。

---

### F-05：刷新状态 API 泄露账户、路径和原始运行日志

- **严重程度：Medium**
- **置信度：High**
- **Tunnel 可达：是\***

**证据与行为：**

状态接口匿名返回完整 payload：[inventory_planner_server.py:193](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:193)。

任务对象包含 `stdout`、`stderr` 和 `lines`：[inventory_planner_server.py:247](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:247)，运行中和完成后均返回原始输出：[inventory_planner_server.py:285](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:285)、[inventory_planner_server.py:306](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:306)。

当前状态文件已包含脱敏前的账户标识、IB 端口、client-id、内部数据路径和 Python site-packages 绝对路径。

**攻击请求：**

```text
GET /api/refresh-inventory-data/status?job=latest
GET /data/planner/refresh_status.json
```

**实际影响：**

泄露账户标识、环境结构、依赖路径、内部错误、刷新时间、业务品种和运行状态。子进程若打印 traceback，也会原样返回。

**能力判断：**

- 读取源码：**否**
- 读取敏感运行信息：**是**
- 修改数据/执行命令：**GET 本身否**

**最小修复：**

状态响应只保留 `ok/running/progress/stage/started/finished` 等固定字段；删除 stdout、stderr、lines、账户和路径。

**长期修复：**

结构化日志单独存储；按角色授权查看；统一脱敏、保留期限和审计。

**验证修复：**

人为制造含假账户、绝对路径和异常栈的测试错误，确认 HTTP 响应和静态目录均不包含这些内容。

---

### F-06：数据文件非原子发布，可能产生截断或跨版本混合

- **严重程度：Medium**
- **置信度：High**
- **Tunnel 可达：是\***

**证据与行为：**

最终 CSV 直接写目标文件：[carry_dashboard_sync.py:474](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/carry_dashboard_sync.py:474)。持仓、期权链、K 线依次覆盖，没有原子快照：[carry_dashboard_sync.py:485](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/carry_dashboard_sync.py:485)。

IB 客户端锁能避免同一 client-id 的刷新并行：[ib_client_lock.py:139](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/ib_client_lock.py:139)，但不能阻止 HTTP 在写入中途读取，也不能保证多个 CSV 属于同一版本。

**攻击请求：**

反复 POST 刷新，同时高频 GET 三个 CSV；或在发布过程中让进程异常退出。

**实际影响：**

读取到半写 CSV、持仓和期权链版本不一致；进程崩溃可能留下截断文件。没有自动备份或回滚。

**能力判断：**

- 修改源码：**否**
- 修改/破坏数据：**是**
- 执行命令：**以刷新为前提的固定命令**

**最小修复：**

每个文件先写同目录临时文件，`flush + fsync` 后 `os.replace`；最后原子更新 manifest。

**长期修复：**

发布到版本化快照目录，再原子切换当前版本指针；保留最近若干成功快照，失败任务不得替换当前版本。

**验证修复：**

在隔离环境强制中断发布，旧版本应保持字节不变；并发读取只能得到完整旧版或完整新版。

---

### F-07：缺少浏览器安全头和点击劫持防护

- **严重程度：Low**
- **置信度：High**
- **Tunnel 可达：是\***

**证据与行为：**

JSON 响应仅设置 Content-Type、Cache-Control 和 Content-Length：[inventory_planner_server.py:179](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/inventory_planner_server.py:179)。静态响应也未增加 CSP、`X-Frame-Options`、`X-Content-Type-Options` 等。

**攻击请求：**

攻击者可在自己的页面中 iframe 公网看板，诱导点击刷新；也可利用默认 `Server` 响应头进行技术指纹识别。

**实际影响：**

点击劫持和版本信息泄露。由于当前本来就没有认证，新增影响低于 F-02。

**能力判断：**

不直接提供源码修改、数据修改或命令执行能力。

**最小修复：**

加入 `Content-Security-Policy: frame-ancestors 'none'`、`X-Content-Type-Options: nosniff`、合适的 Referrer/Permissions Policy，并隐藏详细 Server 版本。

**长期修复：**

由统一反向代理维护安全头和 CSP；为内联脚本使用 nonce/hash，逐步移出内联 JavaScript。

**验证修复：**

`curl -I` 检查响应头；外部 iframe 应被浏览器拒绝。

### 有风险但需要动态验证

#### R-01：Streamlit 服务若单独公网暴露，可进行任意主机端口连接和项目外目录写入

- **严重程度：High（条件性）**
- **置信度：High**
- **当前 8766 Tunnel 可达：否**

用户可输入任意 host/port：[app.py:24](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/app.py:24)，并传入 IB 连接。用户还可输入任意 output directory：[app.py:116](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/app.py:116)，保存逻辑会创建目录并写 CSV：[static_option_chain.py:225](/Users/antony/Desktop/IB-API/target_treasury_account_monitor/static_option_chain.py:225)。

如果 8503 被另行暴露，攻击者可通过 Streamlit UI 连接任意主机端口的 IB 协议服务，并在当前 macOS 用户有权限的目录创建/覆盖固定命名 CSV。未确认可覆盖 `.py` 或执行任意命令。

修复应限制 host/port、账户和 output_dir 为服务端固定配置，并为 Streamlit 增加身份认证。验证时从非授权会话确认无法建立连接或修改路径。

#### R-02：A 股期权 8777 服务若单独暴露，存在匿名读写接口

- **严重程度：Medium（条件性）**
- **置信度：High**
- **当前 8766 Tunnel 可达：否**

`GET /api/config` 泄露 Excel 和持仓绝对路径：[A_Share_Option/server.py:108](/Users/antony/Desktop/IB-API/A_Share_Option/server.py:108)。`POST /api/positions` 无认证覆盖 `positions.json`：[A_Share_Option/server.py:119](/Users/antony/Desktop/IB-API/A_Share_Option/server.py:119)。`GET /api/chain` 可反复触发 Excel 读取。

当前没有发现 8777 监听。若未来暴露，应加入认证、CSRF、原子写入和请求频控。PowerShell 调用使用参数数组且 `shell=False`，未发现命令注入。

#### R-03：IB Gateway 当前监听通配地址

- **严重程度：Medium（条件性）**
- **置信度：Medium**
- **当前 8766 Tunnel 可达：否；匿名刷新可间接调用**

运行态发现 Java IB Gateway 监听 `*:4001`，而项目默认连接该端口：[settings.py:21](/Users/antony/Desktop/IB-API/target_treasury_monitor_clean/settings.py:21)。

是否能从局域网或公网直接连接取决于 macOS 防火墙、路由、IB Gateway Trusted IP 和只读配置，均不在仓库中，故不能确认。应将 Gateway 限制为 loopback 或通过主机防火墙仅允许必要进程/地址。

#### R-04：依赖不可复现，无法确认具体漏洞版本

- **严重程度：Medium（供应链风险）**
- **置信度：High**
- **Tunnel 可达：无法确认**

唯一依赖清单 [requirements_dashboard.txt](/Users/antony/Desktop/IB-API/requirements_dashboard.txt:1) 只有四个未固定版本的包，无锁文件和哈希；代码还直接使用 `beautifulsoup4`、`ibapi` 等未声明依赖。

因此无法在不访问外部漏洞数据库的情况下断言具体 CVE 或“已过时版本”。

修复后应在一次性环境执行：

```bash
pip-compile --generate-hashes requirements_dashboard.in
python -m pip_audit -r requirements_dashboard.lock
osv-scanner scan source -r .
gitleaks git .
```

本次未运行这些可能联网、安装或写文件的命令。

### 已检查且未发现问题

- 未发现 8766 调用链中的 `os.system`、`shell=True`、`eval`、`exec` 或用户输入命令拼接。
- subprocess 使用参数列表，`mode` 限定为 `fast/full`。
- 未发现 IB 下单、撤单或行权 API；项目 IB 连接设置为只读。
- 未发现 8766 的任意文件上传、用户指定文件名写入、重命名或删除接口。
- 标准路径转换会规范化并忽略 `..`；当前项目没有符号链接，因此未确认项目外路径穿越。
- 未发现从 8766 用户输入构造 SQL；没有 HTTP 可达的反序列化入口。
- 8766 没有 WebSocket、SSE、开放重定向或 Host Header 生成 URL 的逻辑。
- 没有通配 CORS；OPTIONS 使用默认 501。注意这不能阻止空 POST CSRF。
- 8766 不使用 Cookie 或会话，因此 Cookie 属性问题不适用；根本问题是没有认证。
- 主页面输出大多通过 `esc()` 或 `textContent` 处理，未确认可由远程攻击者利用的反射型、DOM 型或存储型 XSS。
- 当前未发现 `.env`、私钥、source map、备份文件或编辑器 swap 文件；硬编码 FMP Key 是明确例外。
- 未发现 Flask/FastAPI debug 模式；但当前使用的标准库 HTTP 服务本身不适合公网生产环境。

## 4. 明确回答以下问题

| 问题 | 结论 | 理由 |
|---|---|---|
| 公网用户能否直接下载后端源码？ | **是** | 整个仓库是静态根 |
| 公网用户能否看到前端源码和 API 调用逻辑？ | **是** | HTML 内嵌完整 JavaScript 和业务规则 |
| 公网用户能否读取项目外的本地文件？ | **否，当前未确认** | `..` 被规范化，当前无符号链接；未来符号链接会形成风险 |
| 公网用户能否修改或删除本地数据？ | **是，可修改；未发现删除** | 匿名刷新会覆盖固定 CSV/JSON |
| 公网用户能否覆盖或修改项目源码？ | **否，当前未发现** | 8766 无上传/任意路径写入 |
| 公网用户能否执行本地命令？ | **是，固定命令；不能确认任意命令** | 匿名 POST 启动固定 Python 刷新脚本 |
| 公网用户能否访问本机或局域网中的其他服务？ | **是，受限间接访问** | 固定访问本机 `127.0.0.1:4001`；未发现任意 URL SSRF |
| 是否存在无认证的写入接口？ | **是** | `POST /api/refresh-inventory-data` |
| 是否泄露密钥、路径、日志或 traceback？ | **是** | 硬编码 Key、Git、状态 stdout、绝对依赖路径；子进程 traceback 会原样返回 |
| 大量请求是否可能让本地服务或 Mac 失去响应？ | **是** | 无 body/并发/频率限制，线程、任务、日志、刷新资源无界 |

## 5. 上线前必须完成的事项

### P0：重新开放 Tunnel 前必须处理

1. 停止无认证 Quick Tunnel。
2. 轮换并撤销已暴露的 FMP API Key。
3. 将静态根从仓库根改为专用、最小发布目录；禁止 `.git`、源码、notebook、数据库、Excel、调试数据和目录列表。
4. 为页面和 API 加身份认证；默认拒绝匿名请求。
5. 禁止匿名刷新；增加严格 JSON、Origin、CSRF 和角色权限。
6. 增加请求体、并发、刷新频率和任务数量限制。
7. 将 8766 保持在 `127.0.0.1`；限制 IB Gateway 4001 的网络绑定/防火墙。
8. 状态响应删除 stdout、stderr、账户、路径和 traceback。

### P1：短期处理

- 使用临时文件和原子替换发布所有 CSV。
- 版本化快照并保留可回滚备份。
- 限制 jobs 数量、stdout 长度和任务保留时间。
- 配置日志轮转和磁盘配额。
- 增加 CSP、frame-ancestors、nosniff 等安全头。
- 为 Streamlit 和 8777 服务固定 host、port、路径和允许操作。
- 建立完整、固定版本、带哈希的依赖锁文件并执行依赖审计。
- 从 README、notebook、状态日志中删除或脱敏账户标识和本地路径。

### P2：长期加固

- 展示服务和 IB 刷新 worker 分进程、分权限运行。
- 使用专用低权限 macOS 用户，不让 Web 服务继承个人用户的全部文件权限。
- 使用任务队列、审计日志、监控、告警和备份恢复演练。
- CI 加入秘密扫描、静态安全检查、依赖审计和安全回归测试。
- 为公网用户提供经过聚合和脱敏的数据，不直接发布原始账户快照。

## 6. 推荐部署方式

| 方案 | 评价 | 适用性 |
|---|---|---|
| 无认证 Cloudflare Quick Tunnel | 任何知道 URL 的人都能下载仓库、触发刷新和消耗资源 | **禁止使用** |
| Cloudflare Tunnel + Access | 可在边缘做身份控制，适合需要从非 Tailscale 设备访问的明确用户组 | **完成 P0 后可用** |
| Tailscale Serve | 仅 tailnet 身份和 ACL 内访问，攻击面更小；不要使用公开的 Funnel | **个人/少量受信设备首选** |

结合当前代码，优先顺序是：

1. **个人使用：Tailscale Serve + tailnet ACL。**
2. **需要向指定外部人员开放：Cloudflare Tunnel + Access。**
3. **无认证 Quick Tunnel：不应重新启用。**

Access 或 Tailscale 不能替代代码修复。即使只有受信用户，也不应继续公开整个仓库或允许无限制刷新。

## 7. 审计覆盖范围与局限

### 已检查

- 所有 Python、Shell、PowerShell、HTML 和 JavaScript 文件。
- 8766、8777、8503 服务入口及路由实现。
- 静态目录、API 方法、鉴权、CORS、Cookie、CSRF、浏览器渲染点。
- `subprocess`、动态执行、文件路径、SQLite、网络连接、IB API 调用。
- 当前数据目录的文件类型、大小、表头和非空记录数量。
- notebook 中的凭据、账户和绝对路径模式。
- 当前 Git 树和全部 revision 中的凭据模式。
- 当前监听端口、8766 进程工作目录和日志大小。
- 依赖清单和锁文件情况。

### 未检查或未动态验证

- 未发送任何 HTTP 请求，因为标准处理器会写访问日志，不符合本次禁止改变本地数据的约束。
- 未验证 Cloudflare 实际缓存、请求体、超时、并发上限和 Access 配置。
- 未验证硬编码 Key 是否仍有效。
- 未验证 macOS 防火墙及 IB Gateway Trusted IP、只读 API 设置。
- 未进行并发、Slowloris、超大 body、路径编码和 XSS 动态测试。
- 未解码检查 SQLite/XLSX 的全部业务内容，也未输出任何敏感值。
- 未访问外部漏洞数据库，因此没有断言具体依赖 CVE。
- 工作树在审计开始前已存在大量未提交修改；后台刷新服务也正在运行，数据文件属于时间变化快照。

**最终判断：当前项目不应以无认证方式暴露到公网。**
