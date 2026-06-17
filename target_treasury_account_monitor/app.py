from __future__ import annotations

import math
import os
import time

import pandas as pd
import streamlit as st

from target_treasury_account_monitor.config import (
    DEFAULT_CLIENT_ID,
    DEFAULT_HOST,
    DEFAULT_MARKET_DATA_LABEL,
    DEFAULT_ORDER_PREVIEW_ENABLED,
    DEFAULT_PORT,
    DEFAULT_REFRESH_SECONDS,
    DISCONNECT_ERROR_CODES,
    MARKET_DATA_TYPES,
    MonitorSettings,
)
from target_treasury_account_monitor.frames import excluded_positions_frame
from target_treasury_account_monitor.greeks import greek_totals
from target_treasury_account_monitor.ib_client import (
    connect_ib,
    disconnect_ib,
    maybe_reconnect,
    update_quote_subscriptions,
)
from target_treasury_account_monitor.margin import estimate_contract_capacity, what_if_order_margin
from target_treasury_account_monitor.snapshot import TreasurySnapshot, build_snapshot
from target_treasury_account_monitor.utils import fmt_money, fmt_number, summary_value
from target_treasury_account_monitor.visualization import (
    chart_greek_exposure,
    chart_liquidity,
    chart_position_market_value,
    chart_unrealized_pnl,
)
from target_treasury_account_monitor.wechat import push_wechat_snapshot


def read_sidebar_settings() -> tuple[bool, MonitorSettings]:
    """读取侧边栏配置，返回是否启用监控和运行参数。"""
    with st.sidebar:
        st.subheader("IB")
        enabled = st.checkbox("启用监控", value=False)
        host = st.text_input("Host", value=os.getenv("IB_HOST", DEFAULT_HOST))
        port = st.number_input("Port", min_value=1, max_value=65535, value=int(os.getenv("IB_PORT", DEFAULT_PORT)), step=1)
        client_id = st.number_input("Client ID", min_value=1, max_value=9999, value=int(os.getenv("IB_CLIENT_ID", DEFAULT_CLIENT_ID)), step=1)
        account = st.text_input("目标账户", value=os.getenv("TARGET_ACCOUNT", ""))
        market_labels = list(MARKET_DATA_TYPES.keys())
        default_market_index = market_labels.index(DEFAULT_MARKET_DATA_LABEL)
        market_label = st.selectbox("行情类型", market_labels, index=default_market_index)
        quote_wait_seconds = st.number_input("首次等待行情秒数", min_value=0.0, max_value=30.0, value=8.0, step=0.5)
        refresh_seconds = st.number_input("刷新间隔（秒）", min_value=10, max_value=300, value=DEFAULT_REFRESH_SECONDS, step=1)
        auto_refresh = st.checkbox("自动刷新", value=True)

        st.subheader("下单试算")
        order_preview_enabled = st.checkbox("启用 IB what-if 试算", value=DEFAULT_ORDER_PREVIEW_ENABLED)

        st.subheader("重连")
        auto_reconnect = st.checkbox("自动重连", value=True)
        reconnect_backoff_seconds = st.number_input("重连退避秒数", min_value=2, max_value=300, value=10, step=1)

        st.subheader("企业微信推送")
        wechat_push_enabled = st.checkbox("启用 webhook 推送", value=False)
        wechat_webhook_url = st.text_input("WeCom robot webhook", value=os.getenv("WECHAT_WEBHOOK_URL", ""), type="password")
        wechat_min_interval_seconds = st.number_input("最小推送间隔（秒）", min_value=30, max_value=86400, value=300, step=30)

        if st.button("立即断开"):
            disconnect_ib()
            st.success("已断开")

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
        order_preview_enabled=bool(order_preview_enabled),
        readonly=not bool(order_preview_enabled),
    )
    return bool(enabled), settings


def maybe_push_wechat(settings: MonitorSettings, summary: pd.DataFrame, frame: pd.DataFrame) -> None:
    """按最小间隔发送企业微信快照。"""
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
    """渲染顶部账户资金和美债风险指标。"""
    totals = greek_totals(frame)
    system = totals.iloc[0] if not totals.empty else {}
    cols = st.columns(6)
    cols[0].metric("净清算值", fmt_money(summary_value(summary, "NetLiquidation")))
    cols[1].metric("剩余流动性", fmt_money(summary_value(summary, "ExcessLiquidity")))
    cols[2].metric("可用资金", fmt_money(summary_value(summary, "AvailableFunds")))
    cols[3].metric("维持保证金", fmt_money(summary_value(summary, "MaintMarginReq")))
    treasury_mv = pd.to_numeric(frame.get("marketValue", pd.Series(dtype=float)), errors="coerce").sum()
    cols[4].metric("美债持仓市值", fmt_money(treasury_mv))
    cols[5].metric("Delta x 乘数", fmt_number(system.get("deltaMultiplier", math.nan), 2))


def ordered_position_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """把最常看的持仓字段排到表格前面。"""
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
    """展示最近 IB API 消息，并突出断线类事件。"""
    errors = st.session_state.get("target_errors", [])
    if not errors:
        return
    latest = errors[-1]
    try:
        error_code = int(latest.get("errorCode") or 0)
    except (TypeError, ValueError):
        error_code = 0
    if error_code in DISCONNECT_ERROR_CODES:
        st.warning(f"IB 连接事件 {latest['errorCode']}: {latest['errorString']}")
    with st.expander("IB 消息", expanded=False):
        st.dataframe(pd.DataFrame(errors).tail(30), width="stretch", height=260)


def render_market_data_notice() -> None:
    """展示 Auto 行情探测结果。"""
    notices = st.session_state.get("target_market_data_notices", [])
    if not notices:
        return
    latest = notices[-1]
    detail = str(latest.get("detail", ""))
    effective = str(latest.get("effective", ""))
    requested = str(latest.get("requested", "Auto"))
    text = f"行情模式：{requested} -> {effective}。{detail}"
    if effective == "Delayed":
        st.warning(text)
    else:
        st.info(text)


def load_monitor_snapshot(settings: MonitorSettings) -> TreasurySnapshot:
    """刷新 IB 状态并返回标准账户快照。"""
    maybe_reconnect(settings)
    ib = connect_ib(settings)
    return build_snapshot(ib, settings, lambda positions: update_quote_subscriptions(ib, positions, settings))


def render_capacity_tab(frame: pd.DataFrame, summary: pd.DataFrame, positions: list[object], settings: MonitorSettings) -> None:
    """渲染 IB what-if 保证金试算。"""
    if not settings.order_preview_enabled:
        st.caption("侧边栏启用 IB what-if 试算后可使用。")
        return

    option_frame = frame[frame.get("secType", "") == "FOP"].copy() if not frame.empty else pd.DataFrame()
    if option_frame.empty:
        st.caption("当前没有可用于试算的美债期权持仓。")
        return

    labels = option_frame["optionName"].fillna(option_frame["localSymbol"]).astype(str).tolist()
    selected_label = st.selectbox("合约", labels)
    selected_row = option_frame[option_frame["optionName"].astype(str) == selected_label].iloc[0]
    default_price = selected_row.get("price", math.nan)
    try:
        default_price = float(default_price)
    except (TypeError, ValueError):
        default_price = 0.01
    if math.isnan(default_price):
        default_price = 0.01

    cols = st.columns(4)
    action = cols[0].selectbox("方向", ["SELL", "BUY"], index=0)
    quantity = cols[1].number_input("数量", min_value=1, max_value=100, value=1, step=1)
    limit_price = cols[2].number_input("限价", min_value=0.0, value=float(default_price), step=0.01, format="%.4f")
    safety_buffer = cols[3].number_input("安全垫", min_value=0.0, value=0.0, step=100.0)

    if st.button("运行 IB what-if", type="primary"):
        ib = st.session_state.get("target_ib")
        if ib is None or not ib.isConnected():
            st.warning("IB 尚未连接。")
            return
        selected_con_id = int(selected_row["conId"])
        position_by_con_id = {int(getattr(pos.contract, "conId", 0) or 0): pos for pos in positions}
        selected_position = position_by_con_id.get(selected_con_id)
        if selected_position is None:
            st.warning("没有找到选中合约的原始 IB 对象。")
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

    st.caption("当前只试算已有持仓中的单腿期权；组合策略需要进一步使用 IB BAG 组合单。")


def render_chart(chart: object | None) -> None:
    """统一渲染 Altair 图表；没有数据时给出轻量提示。"""
    if chart is None:
        st.caption("暂无足够数据生成图表。")
        return
    st.altair_chart(chart, use_container_width=True)


def render_visual_tab(frame: pd.DataFrame, summary: pd.DataFrame) -> None:
    """渲染账户风险、资金和持仓分布图。"""
    top_left, top_right = st.columns(2)
    with top_left:
        render_chart(chart_greek_exposure(frame))
    with top_right:
        render_chart(chart_liquidity(summary))

    bottom_left, bottom_right = st.columns(2)
    with bottom_left:
        render_chart(chart_position_market_value(frame))
    with bottom_right:
        render_chart(chart_unrealized_pnl(frame))


def render_tabs(frame: pd.DataFrame, summary: pd.DataFrame, positions: list[object], all_positions: list[object], settings: MonitorSettings) -> None:
    """渲染图表、Greeks、持仓、资金、试算和排除持仓。"""
    visual_tab, greek_tab, positions_tab, liquidity_tab, capacity_tab, excluded_tab = st.tabs(
        ["可视化", "Greeks", "美债持仓", "账户资金", "下单试算", "排除持仓"]
    )
    with visual_tab:
        render_visual_tab(frame, summary)

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
        ]
        st.dataframe(frame[[col for col in greek_cols if col in frame.columns]], width="stretch", height=420)

    with positions_tab:
        st.dataframe(ordered_position_frame(frame), width="stretch", height=520)

    with liquidity_tab:
        if summary.empty:
            st.caption("暂未取得 accountSummary 数据。")
        else:
            st.dataframe(summary.sort_values("tag"), width="stretch", height=460)

    with capacity_tab:
        render_capacity_tab(frame, summary, positions, settings)

    with excluded_tab:
        excluded = excluded_positions_frame(all_positions)
        if excluded.empty:
            st.caption("该账户没有被排除的非美债持仓。")
        else:
            st.dataframe(excluded, width="stretch", height=420)


def render_monitor() -> None:
    """运行 Streamlit 美债账户监控页面。"""
    st.set_page_config(page_title="美债账户持仓监控", layout="wide")
    st.title("美债账户持仓监控")
    enabled, settings = read_sidebar_settings()

    if not enabled:
        disconnect_ib()
        st.info("请先启动 IB Gateway/TWS 并打开 API，然后在侧边栏启用监控。")
        return
    if not settings.account:
        st.warning("请先填写目标账户。")
        return

    try:
        snapshot = load_monitor_snapshot(settings)
        positions = snapshot.positions
        all_positions = snapshot.all_positions
        frame = snapshot.frame
        summary = snapshot.summary
        maybe_push_wechat(settings, summary, frame)
    except Exception as exc:
        st.error(f"刷新失败：{exc}")
        if settings.auto_reconnect:
            st.session_state.target_needs_reconnect = True
        render_errors()
        if settings.auto_refresh:
            time.sleep(settings.refresh_seconds)
            st.rerun()
        return

    if snapshot.accounts:
        st.caption("可见账户：" + " / ".join(snapshot.accounts))

    last_update = snapshot.updated_at.strftime("%Y-%m-%d %H:%M:%S")
    st.caption(
        f"更新时间：{last_update} | 账户：{settings.account} | "
        f"美债持仓：{len(positions)} | 已排除非美债持仓：{snapshot.excluded_count}"
    )
    render_errors()
    render_market_data_notice()
    render_metric_row(summary, frame)

    push_result = st.session_state.get("target_last_wechat_result")
    if push_result:
        status = "成功" if push_result.get("ok") else "失败"
        st.caption(f"企业微信最近推送：{status} {push_result.get('detail', '')}")

    render_tabs(frame, summary, positions, all_positions, settings)

    if settings.auto_refresh:
        time.sleep(settings.refresh_seconds)
        st.rerun()


if __name__ == "__main__":
    render_monitor()
