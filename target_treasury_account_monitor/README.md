# Target Treasury Account Monitor

This folder contains the standalone monitor for one IBKR target account.

## Files

- `app.py`: Streamlit UI entry point.
- `config.py`: Defaults, account tags, treasury roots, and runtime settings.
- `contracts.py`: Treasury futures/options filtering and contract helpers.
- `ib_client.py`: IB connection, reconnect, account summary, positions, and market-data subscriptions.
- `frames.py`: Converts IB positions/tickers into display tables.
- `greeks.py`: Reads IB system Greeks and aggregates account exposures.
- `market_data.py`: Ticker price helpers.
- `wechat.py`: WeCom robot webhook message generation and push.
- `test_target_treasury_monitor.ipynb`: Notebook for offline smoke tests and optional live IB calls.
- `test_position_snapshot.py`: CLI smoke test for option names, prices, values, and Greeks.
- `test_margin_whatif.py`: CLI what-if margin test for one existing option contract.

## Run

From the repository root:

```powershell
.\run_target_treasury_monitor.ps1
```

Then open:

```text
http://127.0.0.1:8502
```

The monitor keeps only treasury futures/options roots `ZT/ZF/ZN/TN/ZB/UB`.
Futures are included in the system Greek total with `delta=1` and `gamma/theta/vega=0`.
Mid-price Greeks are intentionally left as a future extension.

The dashboard also fetches the inferred ZF underlying future price, usually from the
most common underlying month in the account positions, and adds:

- `signedDistanceTicks`: `(strike - ZF reference price) / 0.25`.
- `otmTicks`: option-specific out-of-the-money distance in 0.25 ticks.
- `moneyness`: `ITM`, `ATM`, `OTM`, or `far OTM`; more than 2.5 OTM ticks is marked `far OTM`.
- `spreadType` / `spreadRole`: paired vertical spread labels such as bull put spread and bear call spread.

## Test Scripts

List current treasury positions with non-truncated key columns:

```powershell
python .\target_treasury_account_monitor\test_position_snapshot.py --account U1234567 --wait 8
```

List option contracts that can be used for margin what-if:

```powershell
python .\target_treasury_account_monitor\test_margin_whatif.py --account U1234567
```

Run one short-option margin preview:

```powershell
python .\target_treasury_account_monitor\test_margin_whatif.py --account U1234567 --contract ZF-20260615-106.75-P --action SELL --quantity 1 --limit-price 0.10 --safety-buffer 500
```

The margin script uses IB `whatIf=True` and `transmit=False`; it previews margin impact but does not send a live order.
