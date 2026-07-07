# Cleanup Review

This file records the cleanup state after consolidating the active treasury workflow around `target_treasury_monitor_clean/`.

## Kept Intentionally

- `news_api/`
- `prediction_market/`
- `target_treasury_monitor_clean/`
- `carry_risk_dashboard.html`
- `sell_side_inventory_planner.html`
- `verify_clean_workflows.ipynb`
- `run_clean_treasury_monitor.ps1`
- `data/carry_dashboard_positions.csv`
- `data/carry_dashboard_chain.csv`
- `data/carry_dashboard_bars.csv`

## Removed

Runtime/cache artifacts:

```text
__pycache__/
target_treasury_account_monitor/__pycache__/
target_treasury_monitor_clean/__pycache__/
data/.DS_Store
```

Historical exports and old data snapshots:

```text
live_carry.xlsx
zf_option_chain_account_frame.xlsx
zf_option_chain_monitor_frame.xlsx
data/clean_verify/ZF_FOP_Static_202609_202612_from_20260630_to_all_*
data/clean_verify/ZF_FOP_Static_202609_202612_from_20260702_to_all_*
data/clean_verify/ZF_FOP_Static_202609_202612_from_20260703_to_all_*
data/clean_verify/chain_monitor.csv
```

Old notebooks:

```text
ZF_FOP_Debug.ipynb
treasury_carry_planner_validation.ipynb
target_treasury_account_monitor/test_target_treasury_monitor.ipynb
```

Old entry points and dashboards:

```text
run_zf_dashboard.ps1
run_target_treasury_monitor.ps1
zf_option_dashboard.py
portfolio_monitor.py
zf_viz.py
```

Old tests and diagnostics:

```text
test_static_zf_option_chain.py
test_live_zf_near_expiry_chain.py
target_treasury_account_monitor/test_position_snapshot.py
target_treasury_account_monitor/test_margin_whatif.py
target_treasury_account_monitor/inspect_combo_sources.py
```

Old UI modules that are no longer imported by the clean workflow:

```text
target_treasury_account_monitor/app.py
target_treasury_account_monitor/carry_dashboard.py
target_treasury_account_monitor/margin.py
target_treasury_account_monitor/notebook_view.py
target_treasury_account_monitor/portfolio_view.py
target_treasury_account_monitor/wechat.py
target_treasury_account_monitor/README.md
```

## Still Required Legacy Layer

These files remain because the clean workflow imports them directly or indirectly:

```text
treasury_fop_chain.py
target_treasury_account_monitor/__init__.py
target_treasury_account_monitor/config.py
target_treasury_account_monitor/contracts.py
target_treasury_account_monitor/market_data.py
target_treasury_account_monitor/utils.py
target_treasury_account_monitor/carry_view.py
target_treasury_account_monitor/frames.py
target_treasury_account_monitor/greeks.py
target_treasury_account_monitor/ib_client.py
target_treasury_account_monitor/spreads.py
target_treasury_account_monitor/option_chain_view.py
target_treasury_account_monitor/static_option_chain.py
target_treasury_account_monitor/live_option_chain.py
```

Next cleanup step: migrate these lower-level helpers into `target_treasury_monitor_clean/`, update imports, then delete `target_treasury_account_monitor/` entirely.
