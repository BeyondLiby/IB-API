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
from target_treasury_account_monitor.carry_view import (
    account_positions_frame,
    capital_base_value,
    zf_option_carry_frame,
)
from target_treasury_account_monitor.carry_dashboard import (
    account_view,
    action_table,
    carry_view,
    delta_bucket_summary,
    dte_bucket_summary,
    generate_summary_text,
    normalize_option_dashboard_frame,
    shock_pnl_table,
    summary_metrics,
)
from target_treasury_account_monitor.frames import excluded_positions_frame, positions_to_frame
from target_treasury_account_monitor.greeks import greek_totals
from target_treasury_account_monitor.ib_client import (
    account_summary_frame,
    connect_ib,
    disconnect_ib,
    fetch_target_positions,
    get_future_reference,
    managed_accounts,
    maybe_reconnect,
    portfolio_items_by_key,
    refresh_account_portfolio,
    update_quote_subscriptions,
)
from target_treasury_account_monitor.margin import estimate_contract_capacity, what_if_order_margin
from target_treasury_account_monitor.option_chain_view import fetch_zf_option_chain_snapshot
from target_treasury_account_monitor.portfolio_view import build_portfolio_view
from target_treasury_account_monitor.spreads import add_empty_spread_columns, pair_vertical_spreads
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
        infer_spreads = st.checkbox(
            "Infer spreads from legs",
            value=False,
            help="Experimental: pair long/short legs from current positions. This is not an IBKR-confirmed combo source.",
        )

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
        infer_spreads=bool(infer_spreads),
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


def render_metric_row(summary: pd.DataFrame, frame: pd.DataFrame, future_ref: dict[str, object]) -> None:
    """Render the top-line liquidity and treasury exposure metrics."""
    totals = greek_totals(frame)
    system = totals.iloc[0] if not totals.empty else {}
    cols = st.columns(7)
    future_label = f"{future_ref.get('localSymbol') or future_ref.get('symbol', 'ZF')}"
    cols[0].metric(f"{future_label} ref", fmt_number(future_ref.get("price", math.nan), 4))
    cols[1].metric("Net liquidation", fmt_money(summary_value(summary, "NetLiquidation")))
    cols[2].metric("Excess liquidity", fmt_money(summary_value(summary, "ExcessLiquidity")))
    cols[3].metric("Available funds", fmt_money(summary_value(summary, "AvailableFunds")))
    cols[4].metric("Maint margin", fmt_money(summary_value(summary, "MaintMarginReq")))
    treasury_mv = pd.to_numeric(frame.get("marketValue", pd.Series(dtype=float)), errors="coerce").sum()
    cols[5].metric("Treasury MV", fmt_money(treasury_mv))
    cols[6].metric("System Delta x Mult", fmt_number(system.get("deltaMultiplier", math.nan), 2))


def ordered_position_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Move the most useful position diagnostics to the front of the table."""
    preferred = [
        "optionName",
        "localSymbol",
        "position",
        "spreadType",
        "spreadRole",
        "spreadSource",
        "otmTicks",
        "moneyness",
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


def style_portfolio_view(view: pd.DataFrame) -> object:
    """Apply IBKR-like red/green coloring to PnL columns."""
    color_cols = ["盈亏", "未实现盈亏", "未实现盈亏%", "盈亏%", "投资组合Dlt值"]

    def color_value(value: object) -> str:
        """Return CSS color for positive/negative numeric values."""
        try:
            number = float(value)
        except (TypeError, ValueError):
            return ""
        if pd.isna(number) or number == 0:
            return ""
        return "color: #078b45;" if number > 0 else "color: #d12b38;"

    return view.style.map(color_value, subset=[col for col in color_cols if col in view.columns]).format(
        {
            "最后价": "{:.5f}",
            "市场价值": "{:,.2f}",
            "盈亏": "{:,.2f}",
            "未实现盈亏": "{:,.2f}",
            "未实现盈亏%": "{:.1f}%",
            "持仓": "{:,.0f}",
            "距离最后交易日天数": "{:,.0f}",
            "盈亏%": "{:.2f}%",
            "隐含波动率%": "{:.1f}%",
            "平均价格": "{:.5f}",
            "Delta": "{:.3f}",
            "投资组合Dlt值": "{:,.3f}",
        },
        na_rep="-",
    )


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


def load_monitor_snapshot(settings: MonitorSettings) -> tuple[list[object], list[object], pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str], dict[str, object]]:
    """Refresh IB state and return positions, frames, summary, and visible accounts."""
    maybe_reconnect(settings)
    ib = connect_ib(settings)
    available_accounts = managed_accounts(ib)
    positions, all_positions = fetch_target_positions(ib, settings.account)
    future_ref = get_future_reference(ib, positions, settings, root="ZF")
    tickers = update_quote_subscriptions(ib, positions, settings)
    refresh_account_portfolio(ib, settings.account)
    portfolio_map = portfolio_items_by_key(ib, settings.account)
    frame = positions_to_frame(positions, tickers, portfolio_map, reference_price=float(future_ref.get("price", math.nan)))
    account_frame = account_positions_frame(all_positions, portfolio_map)
    if settings.infer_spreads:
        frame, spread_summary = pair_vertical_spreads(frame)
    else:
        frame, spread_summary = add_empty_spread_columns(frame), pd.DataFrame()
    summary = account_summary_frame(ib, settings.account)
    return positions, all_positions, frame, spread_summary, summary, account_frame, available_accounts, future_ref


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


def render_carry_tab(account_frame: pd.DataFrame, frame: pd.DataFrame, summary: pd.DataFrame, settings: MonitorSettings) -> None:
    """Render account holdings and ZF option carry sizing."""
    st.subheader("Account positions")
    if account_frame.empty:
        st.caption("No non-zero account positions.")
    else:
        account_cols = [
            "localSymbol",
            "secType",
            "symbol",
            "expiry",
            "direction",
            "strike",
            "position",
            "avgCost",
            "marketPrice",
            "marketValue",
            "unrealizedPnL",
            "realizedPnL",
            "conId",
        ]
        st.dataframe(
            account_frame[[col for col in account_cols if col in account_frame.columns]],
            width="stretch",
            height=260,
            hide_index=True,
        )

    st.subheader("ZF futures options carry planner")
    cols = st.columns(4)
    target_return_pct = cols[0].number_input(
        "Monthly target return",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=0.5,
        format="%.1f",
    )
    capital_mode = cols[1].selectbox(
        "Capital base",
        ["Net liquidation", "Excess liquidity", "Available funds", "Custom"],
        index=0,
    )
    custom_capital = cols[2].number_input("Custom capital", min_value=0.0, value=0.0, step=1000.0)
    capital_base = capital_base_value(summary, capital_mode, float(custom_capital))
    cols[3].metric("Target premium", fmt_money(capital_base * target_return_pct / 100.0))

    carry = zf_option_carry_frame(
        frame,
        target_return=target_return_pct / 100.0,
        capital_base=capital_base,
    )
    if carry.empty:
        st.caption("No ZF futures option rows available.")
    else:
        st.dataframe(
            carry.style.format(
                {
                    "dte": "{:.0f}",
                    "strike": "{:.3f}",
                    "delta": "{:.3f}",
                    "price": "{:.5f}",
                    "gamma": "{:.5f}",
                    "signedDelta": "{:.3f}",
                    "position": "{:.0f}",
                    "premiumPerContract": "${:,.0f}",
                    "currentCarryPremium": "${:,.0f}",
                    "targetMonthlyPremium": "${:,.0f}",
                    "contractsForTarget": "{:,.1f}",
                    "deltaAtTarget": "{:,.2f}",
                    "iv": "{:.2%}",
                    "theta": "{:.5f}",
                    "marketValue": "${:,.0f}",
                    "unrealizedPnL": "${:,.0f}",
                },
                na_rep="-",
            ),
            width="stretch",
            height=420,
            hide_index=True,
        )
        st.caption("Sorted by dte, direction, strike, delta, price, gamma. Sizing uses option price x multiplier as premium per contract.")

    st.subheader("ZF option chain candidates")
    chain_cols = st.columns(6)
    manual_future_months = chain_cols[0].text_input("Future months", value="202609,202612", placeholder="YYYYMM,YYYYMM")
    min_future_month = chain_cols[1].text_input("Min future month", value="202609", placeholder="YYYYMM")
    max_future_months = chain_cols[2].number_input("Month count", min_value=1, max_value=6, value=2, step=1)
    min_expiration = chain_cols[3].text_input("Min expiry", value="", placeholder="YYYYMMDD")
    max_expiration = chain_cols[4].text_input("Max expiry", value="", placeholder="YYYYMMDD")
    scope = chain_cols[5].selectbox("Snapshot scope", ["Near futures price", "Full chain"], index=0)

    chain_cols_2 = st.columns(6)
    dte0_width = chain_cols_2[0].number_input("0DTE width", min_value=0.25, max_value=20.0, value=2.0, step=0.25)
    non_dte0_width = chain_cols_2[1].number_input("Other width", min_value=0.25, max_value=20.0, value=5.0, step=0.25)
    batch_size = chain_cols_2[2].number_input("Batch size", min_value=25, max_value=500, value=150, step=25)
    wait_max_seconds = chain_cols_2[3].number_input("Wait seconds", min_value=2.0, max_value=60.0, value=12.0, step=1.0)
    wait_stable_seconds = chain_cols_2[4].number_input("Stable seconds", min_value=0.5, max_value=10.0, value=2.0, step=0.5)
    force_rebuild = chain_cols_2[5].checkbox("Rebuild chain", value=False)
    chain_cols_3 = st.columns(2)
    request_interval = chain_cols_3[0].number_input("Request interval", min_value=0.0, max_value=0.25, value=0.025, step=0.005, format="%.3f")
    show_incomplete_candidates = chain_cols_3[1].checkbox("Show incomplete candidates", value=False)

    if st.button("Fetch ZF option chain candidates", type="primary"):
        ib = st.session_state.get("target_ib")
        if ib is None or not ib.isConnected():
            st.warning("IB is not connected.")
        else:
            with st.spinner("Fetching ZF option chain and live snapshot from IB..."):
                result = fetch_zf_option_chain_snapshot(
                    ib,
                    root="ZF",
                    market_data_type=settings.market_data_type,
                    future_months=manual_future_months.strip() or None,
                    min_month=min_future_month.strip() or None,
                    max_future_months=int(max_future_months),
                    min_expiration=min_expiration.strip() or None,
                    max_expiration=max_expiration.strip() or None,
                    full_chain_snapshot=(scope == "Full chain"),
                    dte0_width=float(dte0_width),
                    non_dte0_width=float(non_dte0_width),
                    batch_size=int(batch_size),
                    wait_max_seconds=float(wait_max_seconds),
                    wait_stable_seconds=float(wait_stable_seconds),
                    request_interval=float(request_interval),
                    force_rebuild_universe=bool(force_rebuild),
                )
                st.session_state.target_zf_chain_result = result

    chain_result = st.session_state.get("target_zf_chain_result")
    if not chain_result:
        st.caption("Fetch the ZF option chain to compare trade candidates beyond current holdings.")
        return

    meta_cols = st.columns(6)
    meta_cols[0].metric("Universe contracts", f"{chain_result.get('contract_count', 0):,}")
    meta_cols[1].metric("Snapshot contracts", f"{chain_result.get('snapshot_count', 0):,}")
    meta_cols[2].metric("Future months", ", ".join(chain_result.get("months", [])))
    meta_cols[3].metric("Scope", str(chain_result.get("snapshot_scope", "")))
    meta_cols[4].metric("Universe source", str(chain_result.get("universe_source", "")))
    meta_cols[5].metric("Min expiry", str(chain_result.get("min_expiration", "")))
    st.caption(f"Universe cache: {chain_result.get('cache_path', '')}")

    future_prices = chain_result.get("future_prices", pd.DataFrame())
    if isinstance(future_prices, pd.DataFrame) and not future_prices.empty:
        st.dataframe(future_prices, width="stretch", height=140, hide_index=True)

    chain_frame = chain_result.get("monitor_frame", pd.DataFrame())
    chain_carry = zf_option_carry_frame(
        chain_frame,
        target_return=target_return_pct / 100.0,
        capital_base=capital_base,
        require_complete_greeks=not show_incomplete_candidates,
    )
    if chain_carry.empty:
        st.caption("No option-chain candidate rows were returned by IB.")
    else:
        st.dataframe(
            chain_carry.style.format(
                {
                    "dte": "{:.0f}",
                    "strike": "{:.3f}",
                    "delta": "{:.3f}",
                    "price": "{:.5f}",
                    "gamma": "{:.5f}",
                    "signedDelta": "{:.3f}",
                    "position": "{:.0f}",
                    "premiumPerContract": "${:,.0f}",
                    "currentCarryPremium": "${:,.0f}",
                    "targetMonthlyPremium": "${:,.0f}",
                    "contractsForTarget": "{:,.1f}",
                    "deltaAtTarget": "{:,.2f}",
                    "iv": "{:.2%}",
                    "theta": "{:.5f}",
                },
                na_rep="-",
            ),
            width="stretch",
            height=520,
            hide_index=True,
        )

    with st.expander("Raw option-chain snapshot", expanded=False):
        metadata = chain_result.get("selected_metadata", pd.DataFrame())
        if isinstance(metadata, pd.DataFrame) and not metadata.empty:
            st.caption("Selected option-chain universe")
            st.dataframe(metadata, width="stretch", height=260, hide_index=True)
        snapshot = chain_result.get("snapshot", pd.DataFrame())
        if isinstance(snapshot, pd.DataFrame) and not snapshot.empty:
            st.caption("Live market-data snapshot")
            st.dataframe(snapshot, width="stretch", height=520, hide_index=True)
        else:
            st.caption("No raw snapshot rows.")


def render_carry_risk_tab(frame: pd.DataFrame, summary: pd.DataFrame) -> None:
    """Render the real-time option carry risk dashboard."""
    st.subheader("Option carry risk dashboard")
    controls = st.columns(4)
    target_return_pct = controls[0].number_input(
        "Target return",
        min_value=0.0,
        max_value=100.0,
        value=10.0,
        step=0.5,
        format="%.1f",
        key="risk_target_return_pct",
    )
    capital_mode = controls[1].selectbox(
        "Capital base",
        ["Net liquidation", "Excess liquidity", "Available funds", "Custom"],
        index=0,
        key="risk_capital_mode",
    )
    custom_capital = controls[2].number_input("Custom capital", min_value=0.0, value=0.0, step=1000.0, key="risk_custom_capital")
    harvest_threshold = controls[3].number_input("Harvest premium threshold", min_value=0.0, value=10.0, step=1.0)
    capital_base = capital_base_value(summary, capital_mode, float(custom_capital))

    account = normalize_option_dashboard_frame(
        frame,
        target_return=target_return_pct / 100.0,
        capital_base=capital_base,
        harvest_threshold=float(harvest_threshold),
    )
    account = account_view(account)
    carry = carry_view(account)
    metrics = summary_metrics(carry)
    shock = shock_pnl_table(account, carry)

    cards = st.columns(6)
    cards[0].metric("Carry net delta", fmt_number(metrics["carryNetDelta"], 3))
    cards[1].metric("Carry net gamma", fmt_number(metrics["carryNetGamma"], 3))
    cards[2].metric("Carry net theta", fmt_number(metrics["carryNetTheta"], 3))
    cards[3].metric("Remaining premium", fmt_money(metrics["carryRemainingPremium"]))
    cards[4].metric("Effective carry", fmt_money(metrics["effectiveCarry"]))
    cards[5].metric("Quality ratio", f"{metrics['carryQualityRatio']:.1%}" if is_valid_dashboard_number(metrics["carryQualityRatio"]) else "-")

    cards_2 = st.columns(6)
    cards_2[0].metric("Risk premium", fmt_money(metrics["riskPremium"]))
    cards_2[1].metric("Danger premium", fmt_money(metrics["dangerPremium"]))
    cards_2[2].metric("Short contracts", fmt_number(metrics["shortContracts"], 0))
    cards_2[3].metric("Carry MV", fmt_money(metrics["carryMarketValue"]))
    cards_2[4].metric("Carry unPnL", fmt_money(metrics["carryUnrealizedPnL"]))
    cards_2[5].metric("0-2DTE gamma share", f"{metrics['0-2DTE gamma share']:.1%}" if is_valid_dashboard_number(metrics["0-2DTE gamma share"]) else "-")

    st.markdown(generate_summary_text(metrics, carry, shock))

    table_cols = st.columns(2)
    with table_cols[0]:
        st.subheader("DTE bucket")
        st.dataframe(
            dte_bucket_summary(carry).style.format(
                {
                    "netDelta": "{:,.3f}",
                    "netGamma": "{:,.3f}",
                    "netTheta": "{:,.3f}",
                    "remainingPremium": "${:,.0f}",
                    "unrealizedPnL": "${:,.0f}",
                    "dangerPremium": "${:,.0f}",
                    "contracts": "{:,.0f}",
                },
                na_rep="-",
            ),
            width="stretch",
            height=280,
            hide_index=True,
        )
    with table_cols[1]:
        st.subheader("Abs delta bucket")
        st.dataframe(
            delta_bucket_summary(carry).style.format(
                {
                    "contracts": "{:,.0f}",
                    "netDelta": "{:,.3f}",
                    "netGamma": "{:,.3f}",
                    "remainingPremium": "${:,.0f}",
                    "unrealizedPnL": "${:,.0f}",
                },
                na_rep="-",
            ),
            width="stretch",
            height=280,
            hide_index=True,
        )

    st.subheader("Shock PnL")
    st.dataframe(
        shock.style.format({"move": "{:+.2f}", "accountPnL": "${:,.0f}", "carryPnL": "${:,.0f}"}, na_rep="-"),
        width="stretch",
        height=320,
        hide_index=True,
    )

    risk_columns = [
        "riskLevel",
        "actionCandidate",
        "flag",
        "dte",
        "direction",
        "strike",
        "absDelta",
        "signedDelta",
        "price",
        "gamma",
        "position",
        "deltaExposure",
        "gammaExposure",
        "thetaExposure",
        "remainingPremium",
        "effectiveCarry",
        "riskPremium",
        "localSymbol",
        "expiry",
        "iv",
        "theta",
        "marketValue",
        "unrealizedPnL",
    ]
    st.subheader("Position risk table")
    st.dataframe(
        carry[[col for col in risk_columns if col in carry.columns]].style.format(
            {
                "dte": "{:.0f}",
                "strike": "{:.3f}",
                "absDelta": "{:.3f}",
                "signedDelta": "{:.3f}",
                "price": "{:.5f}",
                "gamma": "{:.5f}",
                "position": "{:.0f}",
                "deltaExposure": "{:.3f}",
                "gammaExposure": "{:.3f}",
                "thetaExposure": "{:.3f}",
                "remainingPremium": "${:,.0f}",
                "effectiveCarry": "${:,.0f}",
                "riskPremium": "${:,.0f}",
                "iv": "{:.2%}",
                "theta": "{:.5f}",
                "marketValue": "${:,.0f}",
                "unrealizedPnL": "${:,.0f}",
            },
            na_rep="-",
        ),
        width="stretch",
        height=420,
        hide_index=True,
    )

    st.subheader("Action candidates")
    candidates = action_table(carry)
    if candidates.empty:
        st.caption("No close, roll, harvest, or hold candidates from the current short-option rules.")
    else:
        st.dataframe(
            candidates[[col for col in risk_columns if col in candidates.columns]].style.format(
                {
                    "dte": "{:.0f}",
                    "strike": "{:.3f}",
                    "absDelta": "{:.3f}",
                    "signedDelta": "{:.3f}",
                    "price": "{:.5f}",
                    "gamma": "{:.5f}",
                    "position": "{:.0f}",
                    "deltaExposure": "{:.3f}",
                    "gammaExposure": "{:.3f}",
                    "thetaExposure": "{:.3f}",
                    "remainingPremium": "${:,.0f}",
                    "effectiveCarry": "${:,.0f}",
                    "riskPremium": "${:,.0f}",
                    "iv": "{:.2%}",
                    "theta": "{:.5f}",
                    "marketValue": "${:,.0f}",
                    "unrealizedPnL": "${:,.0f}",
                },
                na_rep="-",
            ),
            width="stretch",
            height=320,
            hide_index=True,
        )

    with st.expander("Account View - all option positions", expanded=False):
        st.dataframe(account[[col for col in risk_columns if col in account.columns]], width="stretch", height=420, hide_index=True)


def is_valid_dashboard_number(value: object) -> bool:
    """Return True when a dashboard metric is a finite number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(number)


def render_tabs(frame: pd.DataFrame, spread_summary: pd.DataFrame, summary: pd.DataFrame, account_frame: pd.DataFrame, positions: list[object], all_positions: list[object], settings: MonitorSettings) -> None:
    """Render detailed Greeks, positions, liquidity, and excluded-position tabs."""
    risk_tab, carry_tab, portfolio_tab, spread_tab, greek_tab, positions_tab, liquidity_tab, capacity_tab, excluded_tab = st.tabs(
        ["Carry Risk", "Carry planner", "Portfolio", "Spreads", "Greeks", "Leg details", "Liquidity", "Capacity", "Excluded positions"]
    )
    with risk_tab:
        render_carry_risk_tab(frame, summary)

    with carry_tab:
        render_carry_tab(account_frame, frame, summary, settings)

    with portfolio_tab:
        portfolio_view = build_portfolio_view(frame, spread_summary)
        if portfolio_view.empty:
            st.caption("No treasury option rows available.")
        else:
            st.dataframe(style_portfolio_view(portfolio_view), width="stretch", height=520, hide_index=True)

    with spread_tab:
        if spread_summary.empty:
            st.caption("No paired vertical spreads detected.")
        else:
            st.dataframe(spread_summary, width="stretch", height=360)

    with greek_tab:
        st.dataframe(greek_totals(frame), width="stretch", height=140)
        greek_cols = [
            "optionName",
            "localSymbol",
            "position",
            "spreadType",
            "spreadRole",
            "spreadSource",
            "otmTicks",
            "moneyness",
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
        positions, all_positions, frame, spread_summary, summary, account_frame, available_accounts, future_ref = load_monitor_snapshot(settings)
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
    render_metric_row(summary, frame, future_ref)

    push_result = st.session_state.get("target_last_wechat_result")
    if push_result:
        status = "OK" if push_result.get("ok") else "FAILED"
        st.caption(f"WeChat push last result: {status} {push_result.get('detail', '')}")

    render_tabs(frame, spread_summary, summary, account_frame, positions, all_positions, settings)

    if settings.auto_refresh:
        time.sleep(settings.refresh_seconds)
        st.rerun()


if __name__ == "__main__":
    render_monitor()
