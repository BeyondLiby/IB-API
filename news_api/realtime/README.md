# Realtime News Monitors

This folder keeps the two realtime IBKR news modes separate.

## 1. Watchlist-specific news

File: `watchlist_monitor.py`

This is the already validated path. It subscribes to contract-specific news for
P0/P1 symbols from `news_api/watchlist.py`.

Run:

```powershell
python -m news_api.realtime.watchlist_monitor --port 4001 --seconds 30
```

Expected output includes `tick_news ORCL ...` or other watchlist symbols.

## 2. BroadTape all-news probe

File: `broadtape_monitor.py`

This is the new all-news capability probe. It subscribes to IBKR NEWS contracts
instead of stock contracts. Default specs come from `NEWS_BROADTAPE_SPECS`:

```text
BRF:BRF_ALL@BRF,BZ:BZ_ALL@BZ,FLY:FLY_ALL@FLY
```

Run:

```powershell
python -m news_api.realtime.broadtape_monitor --port 4001 --seconds 30
```

Or run the full capability probe:

```powershell
python -m news_api.realtime.probe --port 4001 --seconds 30
```

If this prints entitlement or invalid-contract errors and zero events, the IBKR
account is not enabled for those BroadTape feeds. In that case, the watchlist
path still works, but "all news" must be approximated with a larger universe of
stock contracts rather than true BroadTape.

## Market data type

The default `NEWS_MARKET_DATA_TYPE=3` requests delayed market data before
subscribing. This avoids live Top market data entitlement failures for
contract-specific news. Set it to `1` only when the account has live market data
permissions.
