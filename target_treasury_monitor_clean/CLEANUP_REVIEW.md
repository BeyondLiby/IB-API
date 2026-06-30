# Cleanup Review

本文件是核验清单，不代表已经删除。当前阶段只新增 `target_treasury_monitor_clean`，不清理旧文件，避免误删仍有价值的调试资产。

## 明确不碰

- `news_api/`
- `prediction_market/`
- `data/prediction_market_*.csv`

## 新目录覆盖的核心需求

- 账户 dashboard：`account_dashboard.py` + `app.py` 的 `Account dashboard` 页签。
- 期权链批量快速更新：`chain_batch.py` + CLI `batch-chain`。
- 期权链实时监控：`chain_realtime.py` + CLI `live-chain`，使用持久订阅，避免每次刷新都重新请求全量行情。
- 统一入口：`run_clean_treasury_monitor.ps1`、`python -m target_treasury_monitor_clean.cli ...`。

## 核验后优先清理候选

这些大概率是运行产物或临时文件，可在你确认新目录可用后删除：

```text
__pycache__/
target_treasury_account_monitor/__pycache__/
streamlit_8502.err.log
streamlit_8502.out.log
~$live_carry.xlsx
carry_risk_dashboard.html
live_carry.csv
live_carry.xlsx
live_chain_carry.csv
zf_option_chain_account_frame.xlsx
zf_option_chain_monitor_frame.xlsx
```

## 外层 notebook 梳理

```text
test.ipynb
```

早期临时实验 notebook，仓库 `.gitignore` 已经标记为不提交。新目录验证后可删除。

```text
ZF_FOP_Debug.ipynb
```

大型 ZF 期权链调试 notebook。核心逻辑已经沉淀到 `treasury_fop_chain.py` 以及新目录的 `chain_batch.py` / `chain_realtime.py`。核验后建议归档或删除。

```text
treasury_carry_planner_validation.ipynb
```

carry planner 验证 notebook。建议先保留，等 `Account dashboard` 和 `Batch chain` 输出与你现有看板一致后，再决定是否删除。

```text
target_treasury_account_monitor/test_target_treasury_monitor.ipynb
```

旧目标账户 monitor 的 smoke test notebook。新 CLI 的 `dashboard-snapshot` 验证通过后，可归档或删除。

## 旧 Python 文件暂时保留

这些仍被新目录复用，现阶段不能删：

```text
treasury_fop_chain.py
target_treasury_account_monitor/
```

这些是旧入口或旧看板，等新目录验证通过、底层函数迁移完成后再清理：

```text
portfolio_monitor.py
zf_option_dashboard.py
zf_viz.py
treasury_fop_chain.py
target_treasury_account_monitor/app.py
target_treasury_account_monitor/static_option_chain.py
target_treasury_account_monitor/live_option_chain.py
```

## 建议核验顺序

1. 先运行新 Streamlit：`.\run_clean_treasury_monitor.ps1`
2. 在 `Account dashboard` 页签确认账户持仓、资金、Greeks、PnL 与旧看板一致。
3. 在 `Batch chain` 页签用 delayed 数据刷新 `202609,202612`，确认保存的 CSV 可用。
4. 在 `Live chain` 页签启动订阅，确认后续刷新速度明显快于重复 batch snapshot。
5. 核验通过后，再按上面的清理候选删除旧产物和 notebook。
