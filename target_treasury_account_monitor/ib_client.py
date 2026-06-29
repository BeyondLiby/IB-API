from __future__ import annotations

import math
import time
from typing import Any

import pandas as pd
import streamlit as st
from ib_async import Future, IB, util
from ib_async.ib import StartupFetch

try:
    from .config import ACCOUNT_TAGS, DEFAULT_GENERIC_TICKS, DISCONNECT_ERROR_CODES, MonitorSettings
    from .contracts import contract_label, infer_primary_future_month, is_treasury_contract, normalize_market_data_contract
    from .market_data import ticker_has_price
except ImportError:
    from config import ACCOUNT_TAGS, DEFAULT_GENERIC_TICKS, DISCONNECT_ERROR_CODES, MonitorSettings
    from contracts import contract_label, infer_primary_future_month, is_treasury_contract, normalize_market_data_contract
    from market_data import ticker_has_price


def connect_ib(settings: MonitorSettings) -> IB:
    """Create or reuse a readonly IB connection stored in Streamlit session state."""
    existing = st.session_state.get("target_ib")
    existing_key = st.session_state.get("target_connection_key")
    key = (settings.host, settings.port, settings.client_id)
    if existing is not None and existing_key != key:
        disconnect_ib()
        existing = None
    if existing is not None and existing.isConnected():
        return existing

    util.startLoop()
    ib = IB()
    ib.connect(
        settings.host,
        settings.port,
        clientId=settings.client_id,
        timeout=10,
        readonly=True,
        fetchFields=(
            StartupFetch.POSITIONS
            | StartupFetch.ACCOUNT_UPDATES
            | StartupFetch.SUB_ACCOUNT_UPDATES
        ),
    )
    ib.reqMarketDataType(settings.market_data_type)
    st.session_state.target_ib = ib
    st.session_state.target_connection_key = key
    st.session_state.target_errors = []
    st.session_state.target_needs_reconnect = False

    def on_error(req_id: int, error_code: int, error_string: str, contract: Any) -> None:
        """Collect IB API messages and mark reconnect-needed events."""
        row = {
            "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
            "reqId": req_id,
            "errorCode": error_code,
            "errorString": error_string,
            "contract": contract_label(contract) if contract else "",
        }
        errors = st.session_state.get("target_errors", [])
        errors.append(row)
        st.session_state.target_errors = errors[-100:]
        if int(error_code or 0) in DISCONNECT_ERROR_CODES:
            st.session_state.target_needs_reconnect = True

    ib.errorEvent += on_error
    st.session_state.target_error_handler = on_error
    return ib


def disconnect_ib() -> None:
    """Cancel active subscriptions and remove the cached IB connection."""
    ib = st.session_state.get("target_ib")
    if ib is not None:
        for ticker in st.session_state.get("target_tickers", {}).values():
            try:
                ib.cancelMktData(ticker.contract)
            except Exception:
                pass
        handler = st.session_state.get("target_error_handler")
        if handler is not None:
            try:
                ib.errorEvent -= handler
            except ValueError:
                pass
        if ib.isConnected():
            ib.disconnect()
    for key in [
        "target_ib",
        "target_connection_key",
        "target_error_handler",
        "target_tickers",
        "target_ticker_key",
        "target_needs_reconnect",
    ]:
        st.session_state.pop(key, None)


def maybe_reconnect(settings: MonitorSettings) -> None:
    """Reconnect after IB disconnect events while respecting a backoff interval."""
    ib = st.session_state.get("target_ib")
    needs_reconnect = bool(st.session_state.get("target_needs_reconnect", False))
    if not settings.auto_reconnect:
        return
    if ib is not None and ib.isConnected() and not needs_reconnect:
        return
    last_attempt = float(st.session_state.get("target_last_reconnect_attempt", 0.0))
    now = time.monotonic()
    if now - last_attempt < settings.reconnect_backoff_seconds:
        return
    st.session_state.target_last_reconnect_attempt = now
    disconnect_ib()
    connect_ib(settings)


def managed_accounts(ib: IB) -> list[str]:
    """Return the accounts visible to the current IB session."""
    try:
        accounts = ib.managedAccounts()
        if accounts:
            return sorted(str(account) for account in accounts)
    except Exception:
        pass
    try:
        return sorted({str(item.account) for item in ib.accountSummary() if getattr(item, "account", "")})
    except Exception:
        return []


def fetch_target_positions(ib: IB, account: str) -> tuple[list[Any], list[Any]]:
    """Fetch non-zero positions for one account and split out treasury contracts."""
    all_positions = [
        pos
        for pos in ib.positions()
        if str(getattr(pos, "account", "")) == account
        and float(getattr(pos, "position", 0) or 0) != 0.0
    ]
    treasury_positions = [
        pos for pos in all_positions if is_treasury_contract(getattr(pos, "contract", None))
    ]
    return treasury_positions, all_positions


def portfolio_items_by_key(ib: IB, account: str) -> dict[int, Any]:
    """Map IB portfolio items by conId for market value and PnL enrichment."""
    out: dict[int, Any] = {}
    try:
        items = ib.portfolio()
    except Exception:
        return out
    for item in items:
        if str(getattr(item, "account", "")) != account:
            continue
        contract = getattr(item, "contract", None)
        con_id = int(getattr(contract, "conId", 0) or 0)
        if con_id:
            out[con_id] = item
    return out


def refresh_account_portfolio(ib: IB, account: str, wait_seconds: float = 1.0) -> None:
    """Ask IB for account updates so portfolio market values and PnL can populate."""
    if not account:
        return
    try:
        ib.reqAccountUpdates(True, account)
        ib.sleep(wait_seconds)
    except Exception:
        pass


def cancel_tickers(ib: IB, tickers: dict[int, Any]) -> None:
    """Cancel a dict of market-data tickers while ignoring stale handles."""
    seen: set[int] = set()
    for ticker in tickers.values():
        ticker_id = id(ticker)
        if ticker_id in seen:
            continue
        seen.add(ticker_id)
        try:
            ib.cancelMktData(ticker.contract)
        except Exception:
            pass


def treasury_market_data_contracts(positions: list[Any]) -> list[tuple[int, Any]]:
    """Build normalized market-data contracts keyed by original position conId."""
    contracts: list[tuple[int, Any]] = []
    for pos in positions:
        contract = getattr(pos, "contract", None)
        original_con_id = int(getattr(contract, "conId", 0) or 0)
        if contract is None or original_con_id == 0:
            continue
        contracts.append((original_con_id, normalize_market_data_contract(contract)))
    return contracts


def add_ticker_aliases(tickers: dict[int, Any], original_con_id: int, ticker: Any) -> None:
    """Store a ticker by original and qualified conId so frame lookups do not miss."""
    tickers[original_con_id] = ticker
    qualified_con_id = int(getattr(getattr(ticker, "contract", None), "conId", 0) or 0)
    if qualified_con_id:
        tickers[qualified_con_id] = ticker


def add_snapshot_fallbacks(
    ib: IB,
    contracts: list[tuple[int, Any]],
    tickers: dict[int, Any],
    *,
    market_data_type: int,
) -> None:
    """Use blocking snapshots for contracts whose streaming ticker has no price yet."""
    def missing_items() -> tuple[list[int], list[Any]]:
        """Return original ids and contracts whose current ticker still lacks price."""
        missing_contracts = []
        missing_original_ids = []
        for original_con_id, contract in contracts:
            ticker = tickers.get(original_con_id)
            if ticker is None or not ticker_has_price(ticker):
                missing_original_ids.append(original_con_id)
                missing_contracts.append(contract)
        return missing_original_ids, missing_contracts

    missing_original_ids, missing_contracts = missing_items()
    if missing_contracts:
        try:
            snapshots = ib.reqTickers(*missing_contracts)
            for original_con_id, snapshot in zip(missing_original_ids, snapshots):
                add_ticker_aliases(tickers, original_con_id, snapshot)
        except Exception:
            pass

    if market_data_type != 1:
        return

    missing_original_ids, missing_contracts = missing_items()
    if not missing_contracts:
        return
    try:
        ib.reqMarketDataType(3)
        ib.sleep(1.0)
        snapshots = ib.reqTickers(*missing_contracts)
        for original_con_id, snapshot in zip(missing_original_ids, snapshots):
            add_ticker_aliases(tickers, original_con_id, snapshot)
    except Exception:
        pass
    finally:
        try:
            ib.reqMarketDataType(market_data_type)
        except Exception:
            pass


def subscribe_quotes_for_positions(
    ib: IB,
    positions: list[Any],
    settings: MonitorSettings,
    *,
    previous_tickers: dict[int, Any] | None = None,
) -> dict[int, Any]:
    """Subscribe to live market data without relying on Streamlit session state."""
    if previous_tickers:
        cancel_tickers(ib, previous_tickers)

    ib.reqMarketDataType(settings.market_data_type)
    tickers: dict[int, Any] = {}
    contracts = treasury_market_data_contracts(positions)
    for original_con_id, contract in contracts:
        try:
            ib.qualifyContracts(contract)
        except Exception:
            pass
        try:
            ticker = ib.reqMktData(contract, genericTickList=DEFAULT_GENERIC_TICKS, snapshot=False, regulatorySnapshot=False)
            add_ticker_aliases(tickers, original_con_id, ticker)
        except Exception as exc:
            print(f"subscribe failed for {contract_label(contract)}: {exc}")
    if tickers and settings.quote_wait_seconds > 0:
        ib.sleep(settings.quote_wait_seconds)
    add_snapshot_fallbacks(ib, contracts, tickers, market_data_type=settings.market_data_type)
    return tickers


def update_quote_subscriptions(
    ib: IB,
    positions: list[Any],
    settings: MonitorSettings,
) -> dict[int, Any]:
    """Subscribe to live market data for the current treasury position set."""
    contracts = treasury_market_data_contracts(positions)
    ticker_key = (
        settings.market_data_type,
        tuple(sorted(original_con_id for original_con_id, _ in contracts)),
    )
    if st.session_state.get("target_ticker_key") == ticker_key:
        return st.session_state.get("target_tickers", {})

    cancel_tickers(ib, st.session_state.get("target_tickers", {}))
    ib.reqMarketDataType(settings.market_data_type)
    tickers: dict[int, Any] = {}
    for original_con_id, contract in contracts:
        try:
            ib.qualifyContracts(contract)
        except Exception:
            pass
        try:
            ticker = ib.reqMktData(contract, genericTickList=DEFAULT_GENERIC_TICKS, snapshot=False, regulatorySnapshot=False)
            add_ticker_aliases(tickers, original_con_id, ticker)
        except Exception as exc:
            errors = st.session_state.get("target_errors", [])
            errors.append(
                {
                    "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
                    "reqId": "",
                    "errorCode": "local",
                    "errorString": f"subscribe failed: {exc}",
                    "contract": contract_label(contract),
                }
            )
            st.session_state.target_errors = errors[-100:]
    if tickers and settings.quote_wait_seconds > 0:
        ib.sleep(settings.quote_wait_seconds)
    add_snapshot_fallbacks(ib, contracts, tickers, market_data_type=settings.market_data_type)
    st.session_state.target_tickers = tickers
    st.session_state.target_ticker_key = ticker_key
    return tickers


def account_summary_frame(ib: IB, account: str) -> pd.DataFrame:
    """Normalize selected IB accountSummary tags for one account."""
    try:
        rows = ib.accountSummary()
    except Exception:
        return pd.DataFrame()
    data = []
    for item in rows:
        if str(getattr(item, "account", "")) != account:
            continue
        tag = str(getattr(item, "tag", ""))
        if tag not in ACCOUNT_TAGS:
            continue
        data.append(
            {
                "account": account,
                "tag": tag,
                "value": pd.to_numeric(getattr(item, "value", math.nan), errors="coerce"),
                "currency": getattr(item, "currency", ""),
            }
        )
    return pd.DataFrame(data)


def get_future_reference(
    ib: IB,
    positions: list[Any],
    settings: MonitorSettings,
    *,
    root: str = "ZF",
) -> dict[str, Any]:
    """Fetch the inferred treasury futures reference price for distance calculations."""
    month = infer_primary_future_month(positions, root=root)
    if not month:
        return {"symbol": root, "month": "", "localSymbol": "", "price": math.nan, "priceSource": "missing_month"}

    contract = Future(symbol=root, lastTradeDateOrContractMonth=month, exchange="CBOT", currency="USD")
    try:
        qualified = ib.qualifyContracts(contract)
        if qualified:
            contract = qualified[0]
    except Exception:
        pass

    ib.reqMarketDataType(settings.market_data_type)
    ticker = None
    try:
        ticker = ib.reqMktData(contract, genericTickList="", snapshot=False, regulatorySnapshot=False)
        ib.sleep(min(max(settings.quote_wait_seconds, 1.0), 3.0))
    except Exception:
        ticker = None

    return _future_reference_from_ticker_or_snapshot(ib, contract, ticker, settings.market_data_type, root, month)


def _future_reference_from_ticker_or_snapshot(
    ib: IB,
    contract: Any,
    ticker: Any,
    market_data_type: int,
    root: str,
    month: str,
) -> dict[str, Any]:
    """Read a futures price from streaming ticker, live snapshot, or delayed snapshot."""
    try:
        from .market_data import ticker_price
    except ImportError:
        from market_data import ticker_price

    price = math.nan
    source = ""
    if ticker is not None:
        price, source = ticker_price(ticker)
    if math.isnan(price):
        try:
            snapshot = ib.reqTickers(contract)[0]
            price, source = ticker_price(snapshot)
        except Exception:
            pass
    if math.isnan(price) and market_data_type == 1:
        try:
            ib.reqMarketDataType(3)
            delayed = ib.reqTickers(contract)[0]
            price, source = ticker_price(delayed)
            if source:
                source = f"delayed_{source}"
        except Exception:
            pass
        finally:
            try:
                ib.reqMarketDataType(market_data_type)
            except Exception:
                pass
    return {
        "symbol": root,
        "month": month,
        "localSymbol": contract_label(contract),
        "price": price,
        "priceSource": source,
        "conId": int(getattr(contract, "conId", 0) or 0),
    }
