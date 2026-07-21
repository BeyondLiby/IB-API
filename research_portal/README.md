# Sell-side Research Portal Collector

面向本人已有权限的 JPMorgan Markets 与 Goldman Sachs Marquee research 内容采集工具。

当前阶段先建立可靠的登录会话与页面诊断机制；后续基于真实登录页面快照实现 JPM subscriptions 遍历、GS Feed 滚动、日期范围、全量历史、去重和断点续跑。

## 安全边界

- 仅使用本人已有访问权限的账号和内容。
- 不绕过验证码、MFA、权限控制或网站限流。
- 登录 profile、页面 HTML、截图和抓取数据只保存在本机，并由 `.gitignore` 排除。
- 如网站条款或机构政策禁止自动化，应停止使用并改走官方 API/导出流程。

## 第一步：安装

在 PowerShell 中运行：

```powershell
cd E:\策略\IB-API\research_portal
powershell -ExecutionPolicy Bypass -File .\setup.ps1
```

本项目直接驱动本机已安装的 Chrome，不额外下载 Playwright Chromium。

## 第二步：首次登录 JPM

```powershell
powershell -ExecutionPolicy Bypass -File .\login.ps1 -Portal jpm
```

浏览器会使用 `research_portal/.local/chrome-profile` 专属 profile。手工完成登录/MFA，确认订阅页可见，然后回到 PowerShell 按 Enter。以后运行会复用这份登录状态。

首次进入 JPM 时可能出现 `Welcome to My Research Subscriptions` 欢迎弹窗。登录和采集流程会检测并关闭它，再读取后面的订阅栏目。

GS 同理：

```powershell
powershell -ExecutionPolicy Bypass -File .\login.ps1 -Portal gs
```

两个站点可共用同一个项目 profile。

## 第三步：生成登录后页面快照

```powershell
powershell -ExecutionPolicy Bypass -File .\snapshot.ps1 -Portal jpm
powershell -ExecutionPolicy Bypass -File .\snapshot.ps1 -Portal gs
```

输出位于 `.local/artifacts/`，仅用于确定真实页面结构和调试采集逻辑。

## JPM 采集

采集某个指定日期：

```powershell
.\.venv\Scripts\python.exe -m portal_crawler crawl-jpm `
  --subscription "China Equity Strategy" `
  --date 2026-07-15
```

采集某日起至今，省略 `--subscription` 时遍历所有订阅：

```powershell
.\.venv\Scripts\python.exe -m portal_crawler crawl-jpm --from-date 2026-07-01
```

采集全部可见历史：

```powershell
.\.venv\Scripts\python.exe -m portal_crawler crawl-jpm --all-history
```

结果去重写入 `data/research.sqlite`，每次运行的 JSON 保存在 `data/runs/`。

### 下载 PDF

采集命令先建立文档索引；PDF 下载使用独立命令，可安全重复运行，已有且文件头有效的 PDF 会跳过：

```powershell
# 指定日期
.\.venv\Scripts\python.exe -m portal_crawler download-jpm --date 2026-07-15

# 某个 subscription 的全部已索引历史
.\.venv\Scripts\python.exe -m portal_crawler download-jpm `
  --subscription "China Equity Strategy" --all-history

# 全部已索引文档
.\.venv\Scripts\python.exe -m portal_crawler download-jpm --all-history
```

PDF 按以下结构保存：

```text
data/YYYY-MM-DD/JPM-报告/YYYY-MM-DD_JPM_报告类型.pdf
```

“报告类型”取报告标题中第一个冒号前的内容。同一天同类型有多篇时自动追加 `_2`、`_3`，防止覆盖。路径、字节数和 SHA-256 写入 SQLite。

## 关于“直接使用当前浏览器登录”

普通方式启动且正在运行的 Chrome 无法被自动化工具安全接管。若当前 Chrome 原本就是以 `--remote-debugging-port=9222` 启动，可使用：

```powershell
powershell -ExecutionPolicy Bypass -File .\snapshot.ps1 -Portal jpm -Mode cdp
```

否则推荐使用项目专属持久化 profile：只需登录一次，且不会触碰或复制日常 Chrome profile。可先运行以下命令检查 CDP 是否可连接：

```powershell
.\.venv\Scripts\python.exe -m portal_crawler doctor
```
