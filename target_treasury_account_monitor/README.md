# Target Treasury Account Monitor

这个包是项目主线：监控一个 IBKR 账户中的美债期货和美债期货期权持仓。

## 对应需求

1. 获取账户美债持仓  
   `ib_client.fetch_target_positions()` 读取目标账户非零持仓，`contracts.is_treasury_contract()` 只保留 `ZT/ZF/ZN/TN/ZB/UB` 的 `FUT/FOP`。

2. 获取持仓详细数据和 Greeks  
   `frames.positions_to_frame()` 合并持仓、行情、portfolio、Greeks，输出价格、市值、PnL、IV、Delta、Gamma、Theta、Vega 等字段。

3. 动态更新  
   `config.DEFAULT_REFRESH_SECONDS = 60`，Streamlit 页面默认每 60 秒刷新，也可以在侧边栏修改。

4. 可视化  
   `visualization.py` 提供账户 Greeks、资金/保证金、持仓市值、未实现盈亏图表。

5. 下单试算  
   `margin.py` 使用 IB what-if order 计算保证金变化，`order.whatIf=True` 且 `order.transmit=False`，不发送真实订单。

## 核心数据流

```text
IB positions()
  -> fetch_target_positions()
  -> Auto 探测实时行情，必要时回退延迟行情
  -> update_quote_subscriptions() / subscribe_quotes_for_positions()
  -> refresh_account_portfolio()
  -> positions_to_frame()
  -> Streamlit / notebook / CLI
```

公共快照入口：

```python
from target_treasury_account_monitor.snapshot import build_snapshot
```

`build_snapshot()` 会返回 `TreasurySnapshot`：

```text
positions       已过滤的美债持仓
all_positions   目标账户全部非零持仓
frame           持仓明细表
summary         accountSummary 资金表
accounts        当前 IB 会话可见账户
updated_at      快照时间
```

## 文件说明

```text
app.py                     Streamlit 页面入口
config.py                  默认端口、刷新间隔、账户指标、运行配置
contracts.py               美债合约过滤、显示名、乘数和行情合约规范化
ib_client.py               IB 连接、重连、持仓、账户、行情订阅
snapshot.py                统一快照构建入口
frames.py                  把 IB 原始对象清洗成持仓明细表
greeks.py                  读取 ticker Greeks 并聚合账户风险
market_data.py             ticker 价格、mid、模型价等兜底逻辑
margin.py                  IB what-if 保证金试算
visualization.py           Altair 可视化图表
utils.py                   数字清洗、格式化、summary 读取
wechat.py                  企业微信 webhook 推送
test_position_snapshot.py  连接 IB 后打印持仓快照
test_margin_whatif.py      连接 IB 后做单腿保证金试算
test_target_treasury_monitor.ipynb  离线/可选在线 notebook 测试
```

## 运行页面

项目根目录执行：

```bash
streamlit run target_treasury_account_monitor/app.py --server.address 127.0.0.1 --server.port 8502
```

Windows：

```powershell
.\run_target_treasury_monitor.ps1
```

## Notebook 测试

打开：

```text
target_treasury_account_monitor/test_target_treasury_monitor.ipynb
```

默认只跑 mock 数据，不连接 IB。确认 IB Gateway/TWS 已启动后，可以在 notebook 中设置：

```python
RUN_LIVE_IB = True
TARGET_ACCOUNT = "U1234567"
```

再运行在线快照单元。

## 命令行测试

打印目标账户美债持仓快照：

```bash
python target_treasury_account_monitor/test_position_snapshot.py --account U1234567 --wait 8
```

列出可试算的期权持仓：

```bash
python target_treasury_account_monitor/test_margin_whatif.py --account U1234567
```

执行一笔 what-if 保证金试算：

```bash
python target_treasury_account_monitor/test_margin_whatif.py \
  --account U1234567 \
  --contract ZF-20260615-106.75-P \
  --action SELL \
  --quantity 1 \
  --limit-price 0.10 \
  --safety-buffer 500
```

## 重要字段

```text
optionName                  合约标准显示名
localSymbol                 IB 本地合约代码
position                    持仓数量
price / priceSource         价格和来源
marketValue / valueSource   市值和来源
unrealizedPnL               未实现盈亏
iv                          隐含波动率
delta/gamma/theta/vega      IB ticker/modelGreeks
systemDeltaMultiplier       position * delta * multiplier
missingData                 当前缺失的数据提示
```

## 注意事项

- 默认使用 `Auto (Live -> Delayed)`，因为很多账户没有 CBOT 美债期货/期权实时行情权限，或实时行情被手机端占用。
- Auto 会先用当前持仓中的一只合约探测实时行情；只有 IB 明确返回 `marketDataType=Live` 才继续使用实时行情，否则自动切到 `Delayed` 并给出提示。
- 如果批量订阅阶段仍然出现 `354/10197`，程序会取消本轮 Live 订阅，并整批改用 `Delayed`。
- 如果确认有实时行情权限，可以在页面侧边栏切换到 `Live`，或在命令行加 `--market-data Live`；探测失败时仍会回退到 `Delayed`。
- 如果出现 `354`，通常是没有订阅对应实时行情；IB 同时提示“延迟市场数据可用”时，使用 `Delayed` 即可。
- 如果出现 `10090`，通常是部分行情字段没有订阅；基础报价可能仍然有效。
- 如果出现 `10197`，通常是实时行情被其他 IB 会话占用。
- 拉账户持仓和拉行情是不同接口：`positions()/portfolio()/accountSummary()` 不需要行情订阅，`reqMktData()/reqTickers()` 才需要实时或延迟行情权限。
- what-if 试算需要 API 允许非只读连接，但订单不会发送。
- 当前下单试算只覆盖已有持仓中的单腿期权；组合/BAG 试算需要另行扩展。
