# IB API Workspace

This repository currently keeps three active areas:

- `target_treasury_monitor_clean/`: Treasury futures/options account monitor, option-chain refresh workflows, and local HTML dashboard publishing.
- `news_api/`: IBKR news collection and relevance pipeline.
- `prediction_market/`: Prediction-market scanner and quote helpers.

Older ZF dashboard experiments and historical notebooks have been removed after the clean monitor workflow became the active entry point.

## Treasury Monitor

The current treasury workflow publishes stable CSV files that are read by the local HTML dashboards:

```text
data/carry_dashboard_positions.csv
data/carry_dashboard_chain.csv
data/carry_dashboard_bars.csv
```

Main files:

```text
carry_risk_dashboard.html                 carry/risk dashboard
sell_side_inventory_planner.html          sell-side inventory planner
verify_clean_workflows.ipynb              notebook verification flow
run_clean_treasury_monitor.ps1            Streamlit clean monitor launcher
target_treasury_monitor_clean/cli.py      CLI entry points
target_treasury_monitor_clean/app.py      Streamlit clean monitor
```

Serve the carry dashboard locally:

```powershell
python -m target_treasury_monitor_clean.cli serve-carry-html `
  --directory . `
  --host 127.0.0.1 `
  --port 8765
```

Then open:

```text
http://127.0.0.1:8765/carry_risk_dashboard.html
```

Serve the inventory planner:

```powershell
python -m target_treasury_monitor_clean.cli serve-inventory-planner `
  --directory . `
  --host 127.0.0.1 `
  --port 8766
```

Then open:

```text
http://127.0.0.1:8766/sell_side_inventory_planner.html
```

## IBKR Notes

- Start TWS or IB Gateway first and enable API access.
- The clean workflows default to local IB host/ports configured in `target_treasury_monitor_clean.settings`.
- Generated market data, SQLite files, logs, Python caches, and `data/` outputs are ignored by git.

## Cleanup Status

The previous Streamlit ZF dashboard files, old notebooks, historical Excel exports, old smoke-test scripts, and cache folders have been removed. Some lower-level legacy modules remain under `target_treasury_account_monitor/` because `target_treasury_monitor_clean/` still imports them. Those can be migrated later before deleting the old package entirely.
