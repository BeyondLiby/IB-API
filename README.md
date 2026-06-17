# IBKR 美债持仓监控

这个项目用于监控一个 IBKR 账户里的美债期货和美债期货期权持仓，核心入口是：

```text
target_treasury_account_monitor/
```

当前主线能力：

- 自动读取目标账户非零持仓，并只保留 `ZT / ZF / ZN / TN / ZB / UB` 美债期货和期货期权。
- 合并 IB 持仓、portfolio、实时/延迟行情，生成包含价格、市值、PnL、IV、Delta、Gamma、Theta、Vega 的持仓明细表。
- 默认每 60 秒刷新一次，可在 Streamlit 侧边栏调整。
- 提供账户资金、Greeks 敞口、持仓市值、未实现盈亏的可视化图表。
- 提供 IB `whatIf=True`、`transmit=False` 的下单保证金试算，不发送真实订单。
- 提供 notebook 和命令行 smoke test，便于离线验证和连接 IB 后做真实检查。

## 安装

建议在 IB Gateway/TWS、Jupyter 和 Streamlit 共用的 Python 环境里安装依赖：

```bash
pip install -r requirements_dashboard.txt
```

依赖主要包括：

```text
ib_async
pandas
altair
streamlit
```

## 运行监控页面

从项目根目录运行：

```bash
streamlit run target_treasury_account_monitor/app.py --server.address 127.0.0.1 --server.port 8502
```

Windows 也可以直接运行：

```powershell
.\run_target_treasury_monitor.ps1
```

然后打开：

```text
http://127.0.0.1:8502
```

## IBKR 设置

运行前需要先启动 IB Gateway 或 TWS，并打开 API。

常见端口：

- IB Gateway Live：`4001`
- IB Gateway Paper：`4002`
- TWS Live：`7496`
- TWS Paper：`7497`

页面侧边栏需要填写：

- `Host`：通常是 `127.0.0.1`
- `Port`：按你的 Gateway/TWS 类型填写
- `Client ID`：避免和其他脚本重复
- `目标账户`：例如 `U1234567`
- `行情类型`：默认 `Auto (Live -> Delayed)`；只有 IB 明确返回 `marketDataType=Live` 才使用实时行情，否则自动切到延迟行情并提示

## 数据逻辑

监控快照的主流程在 `target_treasury_account_monitor/snapshot.py`：

1. 从 IB 读取目标账户全部非零持仓。
2. 用 `contracts.py` 过滤出美债期货和期货期权。
3. 为这些合约订阅行情；若流式 ticker 暂时没有价格，会再请求一次 snapshot 兜底。
4. 用 IB portfolio 补齐市值和 PnL。
5. 用 ticker Greeks 补齐 IV、Delta、Gamma、Theta、Vega。
6. 汇总成持仓表、账户资金表和 Greeks 汇总表。

Greeks 来源优先级：

```text
modelGreeks -> lastGreeks -> askGreeks -> bidGreeks
```

美债期货本身按 `delta=1`、`gamma/theta/vega=0` 计入账户 Delta。

## 下单试算

“下单试算”页使用 IB 的 what-if order：

- `order.whatIf = True`
- `order.transmit = False`

它只请求 IB 返回保证金变化，不发送真实订单。若侧边栏启用 what-if，连接会使用 `readonly=False`，否则使用只读连接。

## 测试

离线 notebook：

```text
target_treasury_account_monitor/test_target_treasury_monitor.ipynb
```

它默认不连接 IB，会用 mock 对象验证过滤、价格兜底、Greeks 聚合和 what-if 容量计算。

连接 IB 后的命令行检查，默认使用 `Auto (Live -> Delayed)`：

```bash
python target_treasury_account_monitor/test_position_snapshot.py --account U1234567 --wait 8
```

如果想优先尝试实时行情，可加；探测失败时仍会回退到延迟行情：

```bash
--market-data Live
```

如果想跳过实时探测、直接使用延迟行情，可加：

```bash
--market-data Delayed
```

## 持仓接口和行情接口

IB 里“拉持仓”和“取行情”不是同一个接口：

- 持仓：`positions()`、`portfolio()`、`accountSummary()`，属于账户/组合数据。
- 行情：`reqMktData()`、`reqTickers()`，属于市场数据，需要对应实时或延迟行情权限。

所以本项目可以在没有实时行情权限时仍然正确列出账户持仓；但价格、bid/ask、IV、Greeks 等字段需要行情接口。没有实时权限时，程序会自动使用 delayed 行情兜底。

列出可试算的期权合约：

```bash
python target_treasury_account_monitor/test_margin_whatif.py --account U1234567
```

试算单腿期权保证金：

```bash
python target_treasury_account_monitor/test_margin_whatif.py \
  --account U1234567 \
  --contract ZF-20260615-106.75-P \
  --action SELL \
  --quantity 1 \
  --limit-price 0.10 \
  --safety-buffer 500
```

## 目录说明

```text
target_treasury_account_monitor/  主线账户监控包
treasury_fop_chain.py             美债期权链历史工具
zf_option_dashboard.py            ZF 期权链历史 Streamlit 页面
zf_viz.py                         期权链可视化历史 helper
news_api/                         新闻模块，和美债账户监控主线无直接依赖
```
