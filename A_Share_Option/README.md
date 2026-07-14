# A Share Option Dashboard

本目录用于读取桌面上的 `A股期权信息.xlsx`，并以本地网页方式查看 `sheet='创业板'` 的期权链、手动维护持仓、汇总 Greeks/浮盈亏/保证金。

## 启动

```powershell
cd E:\策略\IB-API
.\.venv\Scripts\python.exe .\A_Share_Option\server.py --port 8777
```

打开：

```text
http://127.0.0.1:8777/dashboard.html
```

如果 Excel 路径变化：

```powershell
.\.venv\Scripts\python.exe .\A_Share_Option\server.py --excel "C:\Users\Beyond\Desktop\A股期权信息.xlsx" --sheet "创业板" --port 8777
```

## 数据与持仓

- 期权链 API 每次请求都会重新读取 Excel，因此 sheet 行数新增、报价更新后，前端下一次刷新会同步。
- 当前表头没有显式 `strike/行权价` 列；前端会保留 strike 输入框，点击期权行时自动带出代码、方向、到期、开仓价，strike 可手动补录。之后如果 Excel 新增 `行权价`、`执行价` 或 `Strike` 列，会自动读取。
- 持仓保存在 `A_Share_Option/positions.json`，字段包括品种、方向、持仓、strike、开仓价、到期日、代码和备注。
- 合约乘数默认 `10000`，可以在页面顶部调整。

