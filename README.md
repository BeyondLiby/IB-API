# IBKR ZF Options Dashboard

一个基于 `ib_async` 的 IBKR 美债期货期权工具，当前聚焦 `ZF`（5 年美债期货）期权链。

## 功能

- 抓取多个 ZF 底层期货月份的期权链，例如 `202606 / 202609 / 202612`
- 支持日度、周度、月度期权链聚合
- 按当前期货价格过滤合约：
  - 0DTE：保留距离期货价格 2 点以内的 strike
  - 非 0DTE：保留距离期货价格 5 点以内的 strike
- 获取期权报价、OI、成交量、IV 和 Greeks
- Streamlit + Altair 可视化：
  - ZF 日内 K 线
  - 传统期权链表格
  - IV smile
  - OI heatmap
  - 成交增量可视化
- 将高频刷新和低频存储分离，减少本地存储压力

## 文件说明

```text
treasury_fop_chain.py      核心 IBKR / ib_async 工具函数
zf_option_dashboard.py     Streamlit 可视化仪表盘
ZF_FOP_Debug.ipynb         Jupyter 调试 notebook
requirements_dashboard.txt 运行仪表盘所需依赖
run_zf_dashboard.ps1       Windows PowerShell 启动脚本
```

## 安装依赖

建议在你的 IBKR / Jupyter 同一个 Python 环境里安装：

```powershell
pip install -r requirements_dashboard.txt
```

## 运行仪表盘

```powershell
cd E:\策略\IB-API
.\run_zf_dashboard.ps1
```

或直接运行：

```powershell
streamlit run .\zf_option_dashboard.py --server.address 127.0.0.1 --server.port 8501
```

打开浏览器：

```text
http://127.0.0.1:8501
```

## IBKR 注意事项

- 需要先启动 TWS 或 IB Gateway，并开启 API
- 默认连接 `127.0.0.1:4002`，通常对应 IB Gateway Paper
- dashboard 默认使用固定 `clientId = 201`
- 如果出现 `10197: No market data during competing live session`，说明实时行情被其他 IB 会话占用，需要退出其他 TWS / Gateway / 手机端行情会话，或切换到 CSV 快照模式
- 第一次实时订阅会发送较多 `reqMktData` 请求；后续页面刷新只读取内存中的 ticker 快照，不应重复订阅

## 数据存储

默认策略：

- 页面可按几秒刷新一次
- 快照 CSV 只覆盖最新结果
- 成交增量通过 `volumeDelta = current.volume - previous.volume` 计算
- 只有成交增量事件会写入 SQLite：

```text
data/zf_option_flow.sqlite
```

## 不提交的内容

仓库默认忽略：

- IBKR 原始 CSV 快照
- SQLite 数据库
- `test.ipynb`
- Python 缓存
- 本地配置和日志

