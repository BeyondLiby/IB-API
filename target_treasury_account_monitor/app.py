from __future__ import annotations

import math
import os
import time

import pandas as pd
import streamlit as st

from target_treasury_account_monitor.config import (
    DEFAULT_CLIENT_ID,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_REFRESH_SECONDS,
    DISCONNECT_ERROR_CODES,
    MARKET_DATA_TYPES,
    MonitorSettings,
)
from target_treasury_account_monitor.frames import excluded_positions_frame, positions_to_frame
from target_treasury_account_monitor.greeks import greek_totals
from target_treasury_account_monitor.ib_client import (
    account_summary_frame,
    connect_ib,
    disconnect_ib,
    fetch_target_positions,
    managed_accounts,
    maybe_reconnect,
    portfolio_items_by_key,
    refresh_account_portfolio,
    update_quote_subscriptions,
)
from target_treasury_account_monitor.margin import estimate_contract_capacity, what_if_order_margin
from target_treasury_account_monitor.utils import fmt_money, fmt_number, summary_value
from target_treasury_account_monitor.wechat import push_wechat_snapshot


def read_sidebar_settings() -> tuple[bool, MonitorSettings]:
    """Render sidebar controls and return the enabled flag plus settings."""
    with st.sidebar:
        st.subheader("IB")
        enabled = st.checkbox("Enable monitor", value=False)
        host = st.text_input("Host", value=os.getenv("IB_HOST", DEFAULT_HOST))
        port = st.number_input("Port", min_value=1, max_value=65535, value=int(os.getenv("IB_PORT", DEFAULT_PORT)), step=1)
        client_id = st.number_input("Client ID", min_value=1, max_value=9999, value=int(os.getenv("IB_CLIENT_ID", DEFAULT_CLIENT_ID)), step=1)
        account = st.text_input("Target account", value=os.getenv("TARGET_ACCOUNT", ""))
        market_label = st.selectbox("Market data type", list(MARKET_DATA_TYPES.keys()), index=0)
        quote_wait_seconds = st.number_input("Initial quote wait seconds", min_value=0.0, max_value=30.0, value=8.0, step=0.5)
        refresh_seconds = st.number_input("Refresh seconds", min_value=2, max_value=300, value=DEFAULT_REFRESH_SECONDS, step=1)
        auto_refresh = st.checkbox("Auto refresh", value=True)

        st.subheader("Reconnect")
        auto_reconnect = st.checkbox("Auto reconnect", value=True)
        reconnect_backoff_seconds = st.number_input("Reconnect backoff seconds", min_value=2, max_value=300, value=10, step=1)

        st.subheader("WeChat push")
        wechat_push_enabled = st.checkbox("Enable webhook push", value=False)
        wechat_webhook_url = st.text_input("WeCom robot webhook", value=os.getenv("WECHAT_WEBHOOK_URL", ""), type="password")
        wechat_min_interval_seconds = st.number_input("Min push interval seconds", min_value=30, max_value=86400, value=300, step=30)

        if st.button("Disconnect now"):
            disconnect_ib()
            st.success("Disconnected")

    settings = MonitorSettings(
        host=host.strip() or DEFAULT_HOST,
        port=int(port),
        client_id=int(client_id),
        account=account.strip(),
        market_data_type=MARKET_DATA_TYPES[market_label],
        quote_wait_seconds=float(quote_wait_seconds),
        refresh_seconds=int(refresh_seconds),
        auto_refresh=bool(auto_refresh),
        auto_reconnect=bool(auto_reconnect),
        reconnect_backoff_seconds=int(reconnect_backoff_seconds),
        wechat_webhook_url=wechat_webhook_url.strip(),
        wechat_push_enabled=bool(wechat_push_enabled),
        wechat_min_interval_seconds=int(wechat_min_interval_seconds),
    )
    return bool(enabled), settings


def maybe_push_wechat(settings: MonitorSettings, summary: pd.DataFrame, frame: pd.DataFrame) -> None:
    """Rate-limit optional WeCom pushes from the Streamlit session."""
    if not settings.wechat_push_enabled or not settings.wechat_webhook_url:
        return
    now = time.monotonic()
    last_push = float(st.session_state.get("target_last_wechat_push", 0.0))
    if now - last_push < settings.wechat_min_interval_seconds:
        return
    result = push_wechat_snapshot(settings, summary, frame)
    st.session_state.target_last_wechat_push = now
    st.session_state.target_last_wechat_result = result


def render_metric_row(summary: pd.DataFrame, frame: pd.DataFrame) -> None:
    """Render the top-line liquidity and treasury exposure metrics."""
    totals = greek_totals(frame)
    system = totals.iloc[0] if not totals.empty else {}
    cols = st.columns(6)
    cols[0].metric("Net liquidation", fmt_money(summary_value(summary, "NetLiquidation")))
    cols[1].metric("Excess liquidity", fmt_money(summary_value(summary, "ExcessLiquidity")))
    cols[2].metric("Available funds", fmt_money(summary_value(summary, "AvailableFunds")))
    cols[3].metric("Maint margin", fmt_money(summary_value(summary, "MaintMarginReq")))
    treasury_mv = pd.to_numeric(frame.get("marketValue", pd.Series(dtype=float)), errors="coerce").sum()
    cols[4].metric("Treasury MV", fmt_money(treasury_mv))
    cols[5].metric("System Delta x Mult", fmt_number(system.get("deltaMultiplier", math.nan), 2))


def ordered_position_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Move the most useful position diagnostics to the front of the table."""
    preferred = [
        "optionName",
        "localSymbol",
        "position",
        "bid",
        "ask",
        "mid",
        "last",
        "modelOptionPrice",
        "price",
        "priceSource",
        "marketValue",
        "valueSource",
        "unrealizedPnL",
        "estimatedUnrealizedPnL",
        "iv",
        "delta",
        "gamma",
        "theta",
        "vega",
        "missingData",
        "account",
        "symbol",
        "secType",
        "expiry",
        "strike",
        "right",
        "conId",
    ]
    cols = [col for col in preferred if col in frame.columns]
    cols += [col for col in frame.columns if col not in cols]
    return frame[cols]


def render_errors() -> None:
    """Show recent IB API messages and highlight disconnect-style events."""
    errors = st.session_state.get("target_errors", [])
    if not errors:
        return
    latest = errors[-1]
    try:
        error_code = int(latest.get("errorCode") or 0)
    except (TypeError, ValueError):
        error_code = 0
    if error_code in DISCONNECT_ERROR_CODES:
        st.warning(f"IB connection event {latest['errorCode']}: {latest['errorString']}")
    with st.expander("IB messages", expanded=False):
        st.dataframe(pd.DataFrame(errors).tail(30), width="stretch", height=260)


def load_monitor_snapshot(settings: MonitorSettings) -> tuple[list[object], list[object], pd.DataFrame, pd.DataFrame, list[str]]:
    """Refresh IB state and return positions, frames, summary, and visible accounts."""
    maybe_reconnect(settings)
    ib = connect_ib(settings)
    available_accounts = managed_accounts(ib)
    positions, all_positions = fetch_target_positions(ib, settings.account)
    tickers = update_quote_subscriptions(ib, positions, settings)
    refresh_account_portfolio(ib, settings.account)
    portfolio_map = portfolio_items_by_key(ib, settings.account)
    frame = positions_to_frame(positions, tickers, portfolio_map)
    summary = account_summary_frame(ib, settings.account)
    return positions, all_positions, frame, summary, available_accounts


def render_capacity_tab(frame: pd.DataFrame, summary: pd.DataFrame, positions: list[object], settings: MonitorSettings) -> None:
    """Render IB what-if margin checks for adding contracts to current option positions."""
    option_frame = frame[frame.get("secType", "") == "FOP"].copy() if not frame.empty else pd.DataFrame()
    if option_frame.empty:
        st.caption("No futures option rows available for what-if checks.")
        return

    labels = option_frame["optionName"].fillna(option_frame["localSymbol"]).astype(str).tolist()
    selected_label = st.selectbox("Contract", labels)
    selected_row = option_frame[option_frame["optionName"].astype(str) == selected_label].iloc[0]
    default_price = selected_row.get("price", math.nan)
    try:
        default_price = float(default_price)
    except (TypeError, ValueError):
        default_price = 0.01
    if math.isnan(default_price):
        default_price = 0.01

    cols = st.columns(4)
    action = cols[0].selectbox("Action", ["SELL", "BUY"], index=0)
    quantity = cols[1].number_input("Quantity", min_value=1, max_value=100, value=1, step=1)
    limit_price = cols[2].number_input("Limit price", min_value=0.0, value=float(default_price), step=0.01, format="%.4f")
    safety_buffer = cols[3].number_input("Safety buffer", min_value=0.0, value=0.0, step=100.0)

    if st.button("Run IB what-if", type="primary"):
        ib = st.session_state.get("target_ib")
        if ib is None or not ib.isConnected():
            st.warning("IB is not connected.")
            return
        selected_con_id = int(selected_row["conId"])
        position_by_con_id = {int(getattr(pos.contract, "conId", 0) or 0): pos for pos in positions}
        selected_position = position_by_con_id.get(selected_con_id)
        if selected_position is None:
            st.warning("Could not find the selected contract object.")
            return
        margin_row = what_if_order_margin(
            ib,
            selected_position.contract,
            action=action,
            quantity=float(quantity),
            limit_price=float(limit_price),
            account=settings.account,
        )
        capacity_row = estimate_contract_capacity(summary, margin_row, safety_buffer=float(safety_buffer))
        st.dataframe(pd.DataFrame([margin_row | capacity_row]), width="stretch", height=120)

    st.caption("Spread capacity should be calculated with IB combo/BAG what-if legs; this tab currently checks single-leg candidates from existing option contracts.")


def render_tabs(frame: pd.DataFrame, summary: pd.DataFrame, positions: list[object], all_positions: list[object], settings: MonitorSettings) -> None:
    """Render detailed Greeks, positions, liquidity, and excluded-position tabs."""
    greek_tab, positions_tab, liquidity_tab, capacity_tab, excluded_tab = st.tabs(
        ["Greeks", "Treasury positions", "Liquidity", "Capacity", "Excluded positions"]
    )
    with greek_tab:
        st.dataframe(greek_totals(frame), width="stretch", height=140)
        greek_cols = [
            "optionName",
            "localSymbol",
            "position",
            "quoteReady",
            "greekReady",
            "hasPortfolioItem",
            "missingData",
            "greekSource",
            "iv",
            "delta",
            "gamma",
            "theta",
            "vega",
            "systemDeltaContracts",
            "systemDeltaMultiplier",
            "systemGammaMultiplier",
            "systemThetaMultiplier",
            "systemVegaMultiplier",
            "midGreekStatus",
        ]
        st.dataframe(frame[[col for col in greek_cols if col in frame.columns]], width="stretch", height=420)
        st.caption("Mid-price Greeks are reserved for the next step: compute option Greeks from each holding's underlying mid price.")

    with positions_tab:
        st.dataframe(ordered_position_frame(frame), width="stretch", height=520)

    with liquidity_tab:
        if summary.empty:
            st.caption("No accountSummary rows yet.")
        else:
            st.dataframe(summary.sort_values("tag"), width="stretch", height=460)

    with capacity_tab:
        render_capacity_tab(frame, summary, positions, settings)

    with excluded_tab:
        excluded = excluded_positions_frame(all_positions)
        if excluded.empty:
            st.caption("No non-treasury positions in this account.")
        else:
            st.dataframe(excluded, width="stretch", height=420)


def render_monitor() -> None:
    """Run the Streamlit treasury account monitor."""
    st.set_page_config(page_title="Target Treasury Account Monitor", layout="wide")
    st.title("Target Treasury Account Monitor")
    enabled, settings = read_sidebar_settings()

    if not enabled:
        disconnect_ib()
        st.info("Enable the monitor after IB Gateway/TWS is running and API access is open.")
        return
    if not settings.account:
        st.warning("Fill the target account ID first.")
        return

    try:
        positions, all_positions, frame, summary, available_accounts = load_monitor_snapshot(settings)
        maybe_push_wechat(settings, summary, frame)
    except Exception as exc:
        st.error(f"Refresh failed: {exc}")
        if settings.auto_reconnect:
            st.session_state.target_needs_reconnect = True
        render_errors()
        if settings.auto_refresh:
            time.sleep(settings.refresh_seconds)
            st.rerun()
        return

    if available_accounts:
        st.caption("Visible accounts: " + " / ".join(available_accounts))

    last_update = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")
    non_treasury_count = max(len(all_positions) - len(positions), 0)
    st.caption(
        f"Last update: {last_update} | Account: {settings.account} | "
        f"Treasury positions: {len(positions)} | Non-treasury excluded: {non_treasury_count}"
    )
    render_errors()
    render_metric_row(summary, frame)

    push_result = st.session_state.get("target_last_wechat_result")
    if push_result:
        status = "OK" if push_result.get("ok") else "FAILED"
        st.caption(f"WeChat push last result: {status} {push_result.get('detail', '')}")

    render_tabs(frame, summary, positions, all_positions, settings)

    if settings.auto_refresh:
        time.sleep(settings.refresh_seconds)
        st.rerun()


if __name__ == "__main__":
    render_monitor()
