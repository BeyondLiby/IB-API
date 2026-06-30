from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import streamlit as st
from ib_async.ib import StartupFetch

from target_treasury_monitor_clean.account_dashboard import fetch_account_dashboard
from target_treasury_monitor_clean.chain_batch import refresh_static_chain
from target_treasury_monitor_clean.chain_realtime import LiveChainMonitor
from target_treasury_monitor_clean.ib_session import connect_ib
from target_treasury_monitor_clean.settings import (
    AccountDashboardSettings,
    IBSettings,
    LiveChainSettings,
    StaticChainSettings,
    MARKET_DATA_TYPES,
)


def _sidebar() -> tuple[bool, IBSettings]:
    with st.sidebar:
        enabled = st.checkbox("Enable IB connection", value=False)
        account = st.text_input("Target account", value="")
        host = st.text_input("Host", value="127.0.0.1")
        port = st.number_input("Port", min_value=1, max_value=65535, value=4001, step=1)
        client_id = st.number_input("Client ID", min_value=1, max_value=9999, value=351, step=1)
        market_label = st.selectbox("Market data", list(MARKET_DATA_TYPES.keys()), index=0)
        if st.button("Disconnect"):
            ib = st.session_state.pop("clean_ib", None)
            monitor = st.session_state.pop("clean_live_monitor", None)
            if monitor is not None:
                monitor.close()
            if ib is not None and ib.isConnected():
                ib.disconnect()
            st.success("Disconnected")
    return enabled, IBSettings(
        host=host,
        port=int(port),
        client_id=int(client_id),
        account=account.strip(),
        market_data_type=MARKET_DATA_TYPES[market_label],
    )


def _ib(settings: IBSettings):
    key = (settings.host, settings.port, settings.client_id, settings.market_data_type)
    existing = st.session_state.get("clean_ib")
    if existing is not None and st.session_state.get("clean_ib_key") == key and existing.isConnected():
        return existing
    if existing is not None and existing.isConnected():
        existing.disconnect()
    fetch_fields = StartupFetch.POSITIONS | StartupFetch.ACCOUNT_UPDATES | StartupFetch.SUB_ACCOUNT_UPDATES
    ib = connect_ib(settings, fetch_fields=fetch_fields)
    st.session_state.clean_ib = ib
    st.session_state.clean_ib_key = key
    return ib


def render_account_tab(ib, settings: IBSettings) -> None:
    cols = st.columns(4)
    quote_wait = cols[0].number_input("Quote wait seconds", min_value=0.0, max_value=30.0, value=6.0, step=0.5)
    infer_spreads = cols[1].checkbox("Infer spreads", value=False)
    auto_refresh = cols[2].checkbox("Auto refresh", value=False)
    refresh_seconds = cols[3].number_input("Refresh seconds", min_value=1, max_value=300, value=5, step=1)

    should_fetch = st.button("Refresh account dashboard", type="primary") or auto_refresh
    if not should_fetch:
        st.caption("Refresh to load positions, account summary, live quotes, PnL, and Greeks.")
        return

    snapshot = fetch_account_dashboard(
        ib,
        settings,
        AccountDashboardSettings(quote_wait_seconds=float(quote_wait), infer_spreads=bool(infer_spreads)),
        previous_tickers=st.session_state.get("clean_account_tickers"),
    )
    st.session_state.clean_account_tickers = snapshot.tickers
    st.session_state.clean_account_snapshot = snapshot

    ref = snapshot.future_reference
    st.caption(
        f"Last update: {pd.Timestamp.now(tz='Asia/Shanghai'):%Y-%m-%d %H:%M:%S} | "
        f"Visible accounts: {', '.join(snapshot.visible_accounts) or '-'} | "
        f"{ref.get('localSymbol') or ref.get('symbol', 'ZF')} ref={ref.get('price')}"
    )
    st.dataframe(snapshot.greek_summary, width="stretch", height=130)
    st.dataframe(snapshot.position_frame, width="stretch", height=420)

    with st.expander("Account summary and all positions", expanded=False):
        st.dataframe(snapshot.account_summary, width="stretch", height=260)
        st.dataframe(snapshot.account_positions, width="stretch", height=300)

    if auto_refresh:
        time.sleep(float(refresh_seconds))
        st.rerun()


def render_batch_tab(ib, ib_settings: IBSettings) -> None:
    cols = st.columns(5)
    root = cols[0].text_input("Root", value="ZF")
    months = cols[1].text_input("Future months", value="202609,202612")
    min_exp = cols[2].text_input("Min expiry", value="")
    max_exp = cols[3].text_input("Max expiry", value="")
    request_market_data = cols[4].checkbox("Fetch quotes", value=True)

    controls = st.columns(8)
    batch_size = controls[0].number_input("Batch size", min_value=25, max_value=500, value=150, step=25)
    wait_seconds = controls[1].number_input("Wait seconds", min_value=1.0, max_value=60.0, value=10.0, step=1.0)
    stable_seconds = controls[2].number_input("Stable seconds", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
    pause_seconds = controls[3].number_input("Batch pause", min_value=0.0, max_value=10.0, value=0.5, step=0.5)
    empty_retries = controls[4].number_input("Empty retries", min_value=0, max_value=5, value=1, step=1)
    output_dir = controls[5].text_input("Output dir", value="data")
    use_cache = controls[6].checkbox("Use contract cache", value=True)
    rebuild_cache = controls[7].checkbox("Rebuild contracts", value=False)
    filter_cols = st.columns(4)
    use_market_filter = filter_cols[0].checkbox("Filter before subscribe", value=True)
    near_dte_days = filter_cols[1].number_input("Near DTE days", min_value=0, max_value=60, value=7, step=1)
    near_width = filter_cols[2].number_input("Near width", min_value=0.25, max_value=20.0, value=1.0, step=0.25)
    far_width = filter_cols[3].number_input("Far width", min_value=0.25, max_value=20.0, value=3.0, step=0.25)

    if st.button("Batch refresh option chain", type="primary"):
        settings = StaticChainSettings(
            root=root,
            future_months=months,
            min_expiration=min_exp.strip() or None,
            max_expiration=max_exp.strip() or None,
            batch_size=int(batch_size),
            wait_max_seconds=float(wait_seconds),
            wait_stable_seconds=float(stable_seconds),
            inter_batch_pause_seconds=float(pause_seconds),
            empty_batch_retries=int(empty_retries),
            request_market_data=bool(request_market_data),
            output_dir=Path(output_dir),
            use_contract_cache=bool(use_cache),
            force_rebuild_contract_cache=bool(rebuild_cache),
            filter_market_data_by_moneyness=bool(use_market_filter),
            near_dte_days=int(near_dte_days),
            near_strike_width=float(near_width),
            far_strike_width=float(far_width),
        )
        result = refresh_static_chain(ib, settings)
        st.session_state.clean_static_chain = result

    result = st.session_state.get("clean_static_chain")
    if result is None:
        st.caption("Use this for one-time full or broad ZF chain updates.")
        return
    st.metric("Contracts", f"{result.raw.get('contract_count', 0):,}")
    st.metric("Snapshot rows", f"{result.raw.get('snapshot_count', 0):,}")
    st.caption(
        f"Selected: {result.raw.get('selected_contract_count', '')} | "
        f"Scope: {result.raw.get('snapshot_scope', '')} | "
        f"Universe source: {result.raw.get('universe_source', '')} | "
        f"Contract cache: {result.raw.get('contract_cache_path', '')}"
    )
    st.write({name: str(path) for name, path in result.saved_paths.items()})
    st.dataframe(result.monitor_frame, width="stretch", height=460)


def render_live_tab(ib, ib_settings: IBSettings) -> None:
    cols = st.columns(6)
    root = cols[0].text_input("Root", value="ZF", key="live_root")
    months = cols[1].text_input("Months", value="202609,202612", key="live_months")
    max_dte = cols[2].number_input("Max DTE", min_value=0, max_value=365, value=14, step=1)
    max_exp = cols[3].number_input("Expirations", min_value=1, max_value=30, value=8, step=1)
    strikes = cols[4].number_input("Strikes/side", min_value=1, max_value=50, value=12, step=1)
    width = cols[5].number_input("Strike width", min_value=0.25, max_value=20.0, value=5.0, step=0.25)

    poll = st.number_input("Auto refresh seconds", min_value=0.5, max_value=60.0, value=1.0, step=0.5)
    live_settings = LiveChainSettings(
        root=root,
        future_months=months,
        max_dte=int(max_dte),
        max_expirations=int(max_exp),
        strikes_each_side=int(strikes),
        strike_width=float(width),
        poll_seconds=float(poll),
    )

    buttons = st.columns(3)
    if buttons[0].button("Start live subscriptions", type="primary"):
        old = st.session_state.pop("clean_live_monitor", None)
        if old is not None:
            old.close()
        monitor = LiveChainMonitor(ib, live_settings)
        discovery = monitor.start()
        st.session_state.clean_live_monitor = monitor
        st.session_state.clean_live_discovery = discovery

    if buttons[1].button("Stop live subscriptions"):
        monitor = st.session_state.pop("clean_live_monitor", None)
        if monitor is not None:
            monitor.close()

    auto = buttons[2].checkbox("Auto refresh", value=False, key="live_auto")
    monitor = st.session_state.get("clean_live_monitor")
    if monitor is None:
        st.caption("Start live subscriptions once; later refreshes read active ticker objects for speed.")
        return

    snap = monitor.snapshot()
    st.caption(
        f"Rows={len(snap.raw_snapshot)} | "
        f"quote={snap.readiness.quote_ready}/{snap.readiness.requested} | "
        f"greeks={snap.readiness.greek_ready}/{snap.readiness.requested} | "
        f"events={len(snap.flow_events)} | saved={snap.output_path}"
    )
    st.dataframe(snap.monitor_frame, width="stretch", height=500)
    if not snap.flow_events.empty:
        st.subheader("Volume delta events")
        st.dataframe(snap.flow_events, width="stretch", height=240)
    if auto:
        time.sleep(float(poll))
        st.rerun()


def main() -> None:
    st.set_page_config(page_title="Clean Treasury Monitor", layout="wide")
    st.title("Clean Treasury Monitor")
    enabled, ib_settings = _sidebar()
    if not enabled:
        st.info("Enable IB connection after TWS or IB Gateway is running.")
        return
    if not ib_settings.account:
        st.warning("Fill target account for account dashboard; chain tools can still run without it.")
    ib = _ib(ib_settings)
    account_tab, batch_tab, live_tab = st.tabs(["Account dashboard", "Batch chain", "Live chain"])
    with account_tab:
        if ib_settings.account:
            render_account_tab(ib, ib_settings)
        else:
            st.caption("Target account is empty.")
    with batch_tab:
        render_batch_tab(ib, ib_settings)
    with live_tab:
        render_live_tab(ib, ib_settings)


if __name__ == "__main__":
    main()
