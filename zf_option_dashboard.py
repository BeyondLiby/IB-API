from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from ib_async import IB, util

from treasury_fop_chain import (
    FOPMarketDataStreamer,
    append_flow_events_sqlite,
    compute_volume_delta_events,
    filter_contracts_by_moneyness,
    get_future_prices_for_months,
    load_flow_events_sqlite,
    load_universe,
    qualify_future,
    prepare_snapshot_features,
    snapshot_in_batches,
    summarize_snapshot,
)


alt.data_transformers.disable_max_rows()

ROOT = "ZF"
FUTURE_MONTHS = ["202606", "202609", "202612"]
DEFAULT_DASHBOARD_CLIENT_ID = 201
UNIVERSE_CSV = Path(f"{ROOT}_FOP_Universe_{'_'.join(FUTURE_MONTHS)}.csv")
RAW_SNAPSHOT_CSV = Path(f"{ROOT}_FOP_Snapshot.csv")
FEATURES_CSV = Path(f"{ROOT}_FOP_Snapshot_features_cn.csv")
FLOW_DB = Path("data") / "zf_option_flow.sqlite"


st.set_page_config(
    page_title="ZF Option Chain",
    page_icon="",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        :root {
            --bg: #0f1419;
            --panel: #151c23;
            --panel-2: #101820;
            --grid: #26323d;
            --text: #dce5ee;
            --muted: #8c9aa7;
            --green: #51c184;
            --red: #ef6b73;
            --amber: #e5b454;
            --blue: #69a7ff;
        }
        .stApp { background: var(--bg); color: var(--text); }
        [data-testid="stSidebar"] { background: #111820; }
        .block-container { padding-top: 1.25rem; }
        h1, h2, h3 { letter-spacing: 0; }
        .metric-strip {
            display: grid;
            grid-template-columns: repeat(6, minmax(0, 1fr));
            gap: 10px;
            margin-bottom: 14px;
        }
        .metric-box {
            background: linear-gradient(180deg, #18222b 0%, #121a22 100%);
            border: 1px solid #26323d;
            border-radius: 8px;
            padding: 10px 12px;
        }
        .metric-label { color: var(--muted); font-size: 12px; }
        .metric-value { color: var(--text); font-size: 20px; font-weight: 650; line-height: 1.25; }
        .metric-note { color: var(--muted); font-size: 11px; margin-top: 2px; }
        .chain-wrap {
            max-height: 690px;
            overflow: auto;
            border: 1px solid var(--grid);
            border-radius: 8px;
            background: var(--panel);
        }
        table.option-chain {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 12px;
            font-variant-numeric: tabular-nums;
        }
        .option-chain th {
            position: sticky;
            top: 0;
            z-index: 2;
            background: #1b2530;
            color: #aebdca;
            padding: 7px 6px;
            border-bottom: 1px solid var(--grid);
            text-align: right;
            white-space: nowrap;
        }
        .option-chain th.strike-head {
            text-align: center;
            background: #202b36;
            color: #ffffff;
        }
        .option-chain td {
            padding: 5px 6px;
            border-bottom: 1px solid rgba(38, 50, 61, 0.75);
            text-align: right;
            white-space: nowrap;
        }
        .option-chain tr:hover td { background: rgba(105, 167, 255, 0.08); }
        .option-chain td.strike {
            text-align: center;
            background: #111a22;
            color: #ffffff;
            font-weight: 700;
            border-left: 1px solid var(--grid);
            border-right: 1px solid var(--grid);
        }
        .option-chain tr.atm td {
            background: rgba(229, 180, 84, 0.16);
            border-top: 1px solid rgba(229, 180, 84, 0.45);
            border-bottom: 1px solid rgba(229, 180, 84, 0.45);
        }
        .call { color: var(--green); }
        .put { color: var(--red); }
        .muted { color: var(--muted); }
        .small-caption { color: var(--muted); font-size: 12px; margin: 2px 0 8px; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def valid_price(value) -> bool:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(value) and value != -1.0


def fmt(value, digits: int = 3, na: str = "") -> str:
    if not valid_price(value):
        return na
    return f"{float(value):.{digits}f}"


def fmt_int(value, na: str = "") -> str:
    if not valid_price(value):
        return na
    return f"{int(float(value)):,}"


@st.cache_data(ttl=10)
def read_csv_snapshot(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    return pd.read_csv(p)


@st.cache_data
def read_universe(path: str):
    return load_universe(path)


def parse_manual_prices(text: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for item in text.replace("\n", ",").split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        k, v = item.split("=", 1)
        try:
            prices[k.strip()] = float(v.strip())
        except ValueError:
            pass
    return prices


def snapshot_from_live(
    refresh_seconds: int,
    save_seconds: int,
    request_interval: float,
    dte0_width: float,
    non_dte0_width: float,
    manual_prices: dict[str, float],
    ib_client_id: int,
    max_live_contracts: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    contracts, universe_df = read_universe(str(UNIVERSE_CSV))

    existing_ib = st.session_state.get("ib")
    if existing_ib is not None and st.session_state.get("ib_client_id") != ib_client_id:
        old_streamer = st.session_state.get("streamer")
        if old_streamer is not None:
            old_streamer.cancel()
        if existing_ib.isConnected():
            existing_ib.disconnect()
        for key in ["streamer", "ib", "active_conids"]:
            st.session_state.pop(key, None)

    if "ib" not in st.session_state:
        util.startLoop()
        ib = IB()
        ib.connect("127.0.0.1", 4002, clientId=ib_client_id, timeout=10)
        ib.reqMarketDataType(1)
        st.session_state.ib = ib
        st.session_state.ib_client_id = ib_client_id
        st.session_state.ib_errors = []

        def on_error(req_id: int, error_code: int, error_string: str, contract: Any) -> None:
            st.session_state.ib_errors.append(
                {
                    "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
                    "reqId": req_id,
                    "errorCode": error_code,
                    "errorString": error_string,
                    "contract": str(contract),
                }
            )
            st.session_state.ib_errors = st.session_state.ib_errors[-80:]

        ib.errorEvent += on_error
    ib: IB = st.session_state.ib

    fallback_by_month = (
        universe_df.groupby("underlyingMonth")["strike"].median().astype(float).to_dict()
    )
    fallback_by_month.update({str(k): float(v) for k, v in manual_prices.items()})

    future_prices = get_future_prices_for_months(
        ib,
        ROOT,
        FUTURE_MONTHS,
        market_data_type=1,
        fallback_by_month=fallback_by_month,
        wait_seconds=2,
        raise_on_missing=False,
    )
    spot_by_month = dict(zip(future_prices["month"].astype(str), future_prices["price"].astype(float)))

    filtered_contracts, filtered_universe = filter_contracts_by_moneyness(
        contracts,
        universe_df,
        spot_by_underlying_month=spot_by_month,
        dte0_width=dte0_width,
        non_dte0_width=non_dte0_width,
    )
    if max_live_contracts and len(filtered_contracts) > max_live_contracts:
        filtered_universe = filtered_universe.head(max_live_contracts).copy()
        allowed = set(pd.to_numeric(filtered_universe["conId"], errors="coerce").dropna().astype(int))
        filtered_contracts = [contract for contract in filtered_contracts if contract.conId in allowed]
        st.session_state.live_contract_cap_applied = True
    else:
        st.session_state.live_contract_cap_applied = False
    conids = tuple(sorted(c.conId for c in filtered_contracts))

    needs_subscribe = (
        "streamer" not in st.session_state
        or st.session_state.get("active_conids") != conids
        or st.session_state.get("request_interval") != request_interval
    )
    st.session_state.resubscribed_this_run = False
    if "streamer" not in st.session_state:
        subscribe_reason = "first subscription in this Streamlit session"
    elif st.session_state.get("active_conids") != conids:
        subscribe_reason = "filtered contract set changed"
    elif st.session_state.get("request_interval") != request_interval:
        subscribe_reason = "request interval changed"
    else:
        subscribe_reason = "reused existing market data subscriptions"

    if needs_subscribe:
        old_streamer = st.session_state.get("streamer")
        if old_streamer is not None:
            old_streamer.cancel()
        streamer = FOPMarketDataStreamer(ib, request_interval=request_interval)
        streamer.subscribe(filtered_contracts)
        streamer.wait_until_stable(min_seconds=2, max_seconds=15, stable_seconds=2)
        st.session_state.streamer = streamer
        st.session_state.active_conids = conids
        st.session_state.request_interval = request_interval
        st.session_state.resubscribed_this_run = True
        st.session_state.subscription_generation = st.session_state.get("subscription_generation", 0) + 1
        st.session_state.last_subscribe_time = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")
        st.session_state.last_subscribe_contracts = len(filtered_contracts)
    st.session_state.last_subscribe_reason = subscribe_reason

    streamer: FOPMarketDataStreamer = st.session_state.streamer
    snapshot = streamer.snapshot()

    now = time.time()
    previous = st.session_state.get("previous_snapshot")
    events = compute_volume_delta_events(snapshot, previous)
    st.session_state.previous_snapshot = snapshot

    last_save = st.session_state.get("last_save_ts", 0.0)
    if now - last_save >= save_seconds:
        RAW_SNAPSHOT_CSV.parent.mkdir(parents=True, exist_ok=True)
        snapshot.to_csv(RAW_SNAPSHOT_CSV, index=False, encoding="utf-8-sig")
        prepare_snapshot_features(snapshot).to_csv(FEATURES_CSV, index=False, encoding="utf-8-sig")
        append_flow_events_sqlite(events, FLOW_DB)
        st.session_state.last_save_ts = now

    st.session_state.last_refresh_ts = now
    st.session_state.next_refresh_seconds = refresh_seconds
    return snapshot, future_prices, filtered_universe


def get_intraday_bars(
    ib: IB,
    *,
    month: str,
    bar_size: str = "1 min",
    duration: str = "1 D",
    what_to_show: str = "TRADES",
) -> pd.DataFrame:
    contract = qualify_future(ib, ROOT, month)
    bars = ib.reqHistoricalData(
        contract,
        endDateTime="",
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow=what_to_show,
        useRTH=False,
        formatDate=1,
        keepUpToDate=False,
    )
    if not bars:
        return pd.DataFrame()
    rows = []
    for bar in bars:
        rows.append(
            {
                "date": pd.to_datetime(bar.date),
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    if df["date"].dt.tz is None:
        df["date"] = df["date"].dt.tz_localize("America/Chicago", ambiguous="NaT", nonexistent="shift_forward")
    df["dateChina"] = df["date"].dt.tz_convert("Asia/Shanghai")
    return df


def get_intraday_bars_safe(month: str, bar_size: str) -> pd.DataFrame:
    ib = st.session_state.get("ib")
    if ib is None or not ib.isConnected():
        return pd.DataFrame()
    cache_key = f"intraday_bars_{month}_{bar_size}"
    ts_key = f"intraday_bars_ts_{month}_{bar_size}"
    now = time.time()
    ttl = int(st.session_state.get("kline_refresh_seconds", 30))
    cached = st.session_state.get(cache_key)
    cached_ts = st.session_state.get(ts_key, 0.0)
    if cached is not None and now - cached_ts < ttl:
        return cached
    try:
        bars = get_intraday_bars(ib, month=month, bar_size=bar_size)
        st.session_state[cache_key] = bars
        st.session_state[ts_key] = now
        return bars
    except Exception as exc:
        st.warning(f"日内K线暂时无法获取：{exc}")
        return cached if cached is not None else pd.DataFrame()


def render_ib_status(mode: str, future_prices: pd.DataFrame, filtered_universe: pd.DataFrame) -> None:
    errors = st.session_state.get("ib_errors", [])
    latest_10197 = next((err for err in reversed(errors) if err.get("errorCode") == 10197), None)
    latest_100 = next((err for err in reversed(errors) if err.get("errorCode") in {100, 420}), None)

    if mode != "连接IB实时":
        st.info("当前是 CSV 快照模式：页面只读取本地 ZF_FOP_Snapshot.csv，不会连接 IB 或占用行情线。")
        return

    if latest_10197:
        st.error(
            "IB 返回 10197：当前账号的实时行情正在被其他 live session 占用。"
            "关闭其他 TWS/IB Gateway/手机端行情窗口后，点击侧边栏“停止IB订阅”，再重新进入实时模式。"
        )
    elif latest_100:
        st.warning(
            f"IB pacing/请求频率提示 {latest_100['errorCode']}：可以把请求间隔调大，或使用 CSV 模式查看。"
        )
    else:
        active = len(st.session_state.get("active_conids", []))
        st.success(f"IB 实时模式运行中。当前订阅合约数：{active:,}")

    if not future_prices.empty:
        missing = future_prices[~pd.to_numeric(future_prices["price"], errors="coerce").notna()]
        if not missing.empty:
            st.warning("部分期货价格没有拿到，过滤会使用 fallback。若看到 10197，请先处理 competing live session。")

    if not filtered_universe.empty:
        st.caption(f"过滤后 universe：{len(filtered_universe):,} 行。0DTE 用 ±2，非0DTE 用 ±5。")
    if st.session_state.get("live_contract_cap_applied"):
        st.warning("已应用最大实时订阅合约数限制。若需要全量链，请提高上限，但 IB Gateway 会更重。")

    if errors:
        with st.expander("最近 IB API 提示"):
            st.dataframe(pd.DataFrame(errors).tail(20), width="stretch", height=220)


def render_subscription_status(mode: str) -> None:
    if mode != "连接IB实时":
        return
    active_count = len(st.session_state.get("active_conids", []))
    resubscribed = bool(st.session_state.get("resubscribed_this_run", False))
    action = "本次重新发送 reqMktData" if resubscribed else "本次复用已有订阅"
    action_color = "#e5b454" if resubscribed else "#51c184"
    client_id = st.session_state.get("ib_client_id", "")
    generation = st.session_state.get("subscription_generation", 0)
    last_time = st.session_state.get("last_subscribe_time", "")
    reason = st.session_state.get("last_subscribe_reason", "")
    st.markdown(
        f"""
        <div class="metric-strip">
          <div class="metric-box"><div class="metric-label">IB clientId</div><div class="metric-value">{client_id}</div><div class="metric-note">dashboard 固定客户端</div></div>
          <div class="metric-box"><div class="metric-label">订阅合约数</div><div class="metric-value">{active_count:,}</div><div class="metric-note">当前 active conIds</div></div>
          <div class="metric-box"><div class="metric-label">订阅状态</div><div class="metric-value" style="color:{action_color}">{action}</div><div class="metric-note">{reason}</div></div>
          <div class="metric-box"><div class="metric-label">订阅批次</div><div class="metric-value">{generation}</div><div class="metric-note">每次重订阅 +1</div></div>
          <div class="metric-box"><div class="metric-label">上次订阅时间</div><div class="metric-value">{last_time[-8:] if last_time else ''}</div><div class="metric-note">{last_time[:10] if last_time else ''}</div></div>
          <div class="metric-box"><div class="metric-label">刷新逻辑</div><div class="metric-value">snapshot</div><div class="metric-note">普通刷新只读内存 ticker</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def snapshot_from_csv() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    snapshot = read_csv_snapshot(str(RAW_SNAPSHOT_CSV))
    future_prices = pd.DataFrame()
    filtered_universe = pd.DataFrame()
    return snapshot, future_prices, filtered_universe


def make_metric_strip(snapshot: pd.DataFrame, future_prices: pd.DataFrame, flow: pd.DataFrame) -> None:
    if snapshot.empty:
        return
    summary = summarize_snapshot(snapshot)
    last_time = pd.to_datetime(snapshot["snapshotTimeUtc"], utc=True, errors="coerce").max()
    last_time_cn = ""
    if pd.notna(last_time):
        last_time_cn = last_time.tz_convert("Asia/Shanghai").strftime("%H:%M:%S")
    spot_text = ""
    if not future_prices.empty:
        parts = []
        for row in future_prices.to_dict("records"):
            parts.append(f"{row['month']}: {fmt(row['price'], 4)}")
        spot_text = " / ".join(parts)

    flow_today = 0
    if not flow.empty and "volumeDelta" in flow.columns:
        flow_today = int(pd.to_numeric(flow["volumeDelta"], errors="coerce").fillna(0).sum())

    st.markdown(
        f"""
        <div class="metric-strip">
          <div class="metric-box"><div class="metric-label">合约数</div><div class="metric-value">{int(summary.get('contracts', 0)):,}</div><div class="metric-note">过滤后订阅/显示</div></div>
          <div class="metric-box"><div class="metric-label">有报价</div><div class="metric-value">{int(summary.get('with_quote', 0)):,}</div><div class="metric-note">bid/ask/last/close</div></div>
          <div class="metric-box"><div class="metric-label">有 Greeks</div><div class="metric-value">{int(summary.get('with_primary_delta', 0)):,}</div><div class="metric-note">delta 非空</div></div>
          <div class="metric-box"><div class="metric-label">有 OI</div><div class="metric-value">{int(summary.get('with_open_interest', 0)):,}</div><div class="metric-note">未平仓量非空</div></div>
          <div class="metric-box"><div class="metric-label">成交增量</div><div class="metric-value">{flow_today:,}</div><div class="metric-note">SQLite 累计事件</div></div>
          <div class="metric-box"><div class="metric-label">更新时间</div><div class="metric-value">{last_time_cn}</div><div class="metric-note">{spot_text}</div></div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def build_chain_html(df: pd.DataFrame, expiration: str, spot: float | None) -> str:
    one = df[df["expiration"].astype(str) == str(expiration)].copy()
    if one.empty:
        return "<div class='small-caption'>No contracts for this expiration.</div>"

    one["strike"] = pd.to_numeric(one["strike"], errors="coerce")
    strikes = sorted(one["strike"].dropna().unique())
    atm_strike = min(strikes, key=lambda x: abs(x - spot)) if spot is not None and strikes else None

    call = one[one["right"] == "C"].set_index("strike")
    put = one[one["right"] == "P"].set_index("strike")

    headers = [
        ("call", "IV"),
        ("call", "Delta"),
        ("call", "OI"),
        ("call", "Bid"),
        ("call", "Ask"),
        ("call", "Mid"),
        ("strike-head", "Strike"),
        ("put", "Mid"),
        ("put", "Ask"),
        ("put", "Bid"),
        ("put", "OI"),
        ("put", "Delta"),
        ("put", "IV"),
    ]
    html = ["<div class='chain-wrap'><table class='option-chain'><thead><tr>"]
    for cls, label in headers:
        html.append(f"<th class='{cls}'>{label}</th>")
    html.append("</tr></thead><tbody>")

    def row_at(frame: pd.DataFrame, strike: float, col: str):
        if strike not in frame.index:
            return math.nan
        val = frame.loc[strike, col]
        if isinstance(val, pd.Series):
            val = val.iloc[0]
        return val

    for strike in strikes:
        row_class = "atm" if atm_strike is not None and strike == atm_strike else ""
        html.append(f"<tr class='{row_class}'>")
        html.append(f"<td class='call'>{fmt(row_at(call, strike, 'iv'), 3)}</td>")
        html.append(f"<td class='call'>{fmt(row_at(call, strike, 'delta'), 3)}</td>")
        html.append(f"<td class='call'>{fmt_int(row_at(call, strike, 'openInterest'))}</td>")
        html.append(f"<td class='call'>{fmt(row_at(call, strike, 'bid'), 4)}</td>")
        html.append(f"<td class='call'>{fmt(row_at(call, strike, 'ask'), 4)}</td>")
        html.append(f"<td class='call'>{fmt(row_at(call, strike, 'mid'), 4)}</td>")
        html.append(f"<td class='strike'>{fmt(strike, 2)}</td>")
        html.append(f"<td class='put'>{fmt(row_at(put, strike, 'mid'), 4)}</td>")
        html.append(f"<td class='put'>{fmt(row_at(put, strike, 'ask'), 4)}</td>")
        html.append(f"<td class='put'>{fmt(row_at(put, strike, 'bid'), 4)}</td>")
        html.append(f"<td class='put'>{fmt_int(row_at(put, strike, 'openInterest'))}</td>")
        html.append(f"<td class='put'>{fmt(row_at(put, strike, 'delta'), 3)}</td>")
        html.append(f"<td class='put'>{fmt(row_at(put, strike, 'iv'), 3)}</td>")
        html.append("</tr>")

    html.append("</tbody></table></div>")
    return "".join(html)


def chart_iv_smile(df: pd.DataFrame, expiration: str):
    one = df[df["expiration"].astype(str) == str(expiration)].copy()
    one["strike"] = pd.to_numeric(one["strike"], errors="coerce")
    one["iv"] = pd.to_numeric(one["iv"], errors="coerce")
    one = one.dropna(subset=["strike", "iv"])
    if one.empty:
        return None
    return (
        alt.Chart(one)
        .mark_line(point=alt.OverlayMarkDef(size=40), strokeWidth=2)
        .encode(
            x=alt.X("strike:Q", title="Strike"),
            y=alt.Y("iv:Q", title="IV", scale=alt.Scale(zero=False)),
            color=alt.Color("right:N", title="", scale=alt.Scale(domain=["C", "P"], range=["#51c184", "#ef6b73"])),
            tooltip=["localSymbol:N", "strike:Q", "right:N", "iv:Q", "delta:Q", "bid:Q", "ask:Q"],
        )
        .properties(height=260)
    )


def chart_heatmap(df: pd.DataFrame, value_col: str, title: str):
    data = df.copy()
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
    data["strike"] = pd.to_numeric(data["strike"], errors="coerce")
    data = data.dropna(subset=["strike", value_col])
    if data.empty:
        return None
    base = (
        alt.Chart(data)
        .mark_rect()
        .encode(
            x=alt.X("strike:O", title="Strike", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("expiration:O", title="Expiration"),
            color=alt.Color(f"{value_col}:Q", title=title, scale=alt.Scale(scheme="viridis")),
            tooltip=["expiration:N", "strike:Q", "right:N", f"{value_col}:Q"],
        )
        .properties(height=220)
    )
    return base.facet(row=alt.Row("right:N", title=""))


def chart_intraday_candles(bars: pd.DataFrame):
    if bars.empty:
        return None
    data = bars.copy()
    data["direction"] = (data["close"] >= data["open"]).map({True: "up", False: "down"})
    nearest = alt.selection_point(
        nearest=True,
        on="pointerover",
        fields=["dateChina"],
        empty=False,
    )
    base = alt.Chart(data).encode(
        x=alt.X("dateChina:T", title="Time"),
        tooltip=[
            alt.Tooltip("dateChina:T", title="Time"),
            alt.Tooltip("open:Q", title="Open", format=".4f"),
            alt.Tooltip("high:Q", title="High", format=".4f"),
            alt.Tooltip("low:Q", title="Low", format=".4f"),
            alt.Tooltip("close:Q", title="Close", format=".4f"),
            alt.Tooltip("volume:Q", title="Volume", format=",.0f"),
        ],
    )
    rule = base.mark_rule().encode(
        y=alt.Y("low:Q", title="Price", scale=alt.Scale(zero=False)),
        y2="high:Q",
        color=alt.Color(
            "direction:N",
            legend=None,
            scale=alt.Scale(domain=["up", "down"], range=["#51c184", "#ef6b73"]),
        ),
    )
    body = base.mark_bar(size=5).encode(
        y=alt.Y("open:Q", title="Price", scale=alt.Scale(zero=False)),
        y2="close:Q",
        color=alt.Color(
            "direction:N",
            legend=None,
            scale=alt.Scale(domain=["up", "down"], range=["#51c184", "#ef6b73"]),
        ),
    )
    points = base.mark_point(opacity=0).add_params(nearest)
    return (rule + body + points).properties(height=260)


def chart_flow_heatmap(flow: pd.DataFrame):
    if flow.empty:
        return None
    data = flow.copy()
    data["strike"] = pd.to_numeric(data["strike"], errors="coerce")
    data["volumeDelta"] = pd.to_numeric(data["volumeDelta"], errors="coerce")
    data = data.dropna(subset=["strike", "volumeDelta"])
    if data.empty:
        return None
    return (
        alt.Chart(data)
        .mark_circle(opacity=0.78)
        .encode(
            x=alt.X("snapshotTimeChina:T", title="Time"),
            y=alt.Y("strike:Q", title="Strike", scale=alt.Scale(zero=False)),
            size=alt.Size("volumeDelta:Q", title="Volume Δ", scale=alt.Scale(range=[20, 900])),
            color=alt.Color("right:N", title="", scale=alt.Scale(domain=["C", "P"], range=["#51c184", "#ef6b73"])),
            tooltip=["snapshotTimeChina:T", "expiration:N", "strike:Q", "right:N", "volumeDelta:Q", "mid:Q", "iv:Q", "delta:Q"],
        )
        .properties(height=310)
    )


def chart_flow_by_strike(flow: pd.DataFrame):
    if flow.empty:
        return None
    data = flow.copy()
    data["volumeDelta"] = pd.to_numeric(data["volumeDelta"], errors="coerce")
    data["strike"] = pd.to_numeric(data["strike"], errors="coerce")
    data = data.dropna(subset=["volumeDelta", "strike"])
    if data.empty:
        return None
    grouped = data.groupby(["tradeDate", "strike", "right"], as_index=False)["volumeDelta"].sum()
    return (
        alt.Chart(grouped)
        .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("strike:O", title="Strike", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("volumeDelta:Q", title="Volume Δ"),
            color=alt.Color("right:N", title="", scale=alt.Scale(domain=["C", "P"], range=["#51c184", "#ef6b73"])),
            column=alt.Column("tradeDate:N", title="Trade Date"),
            tooltip=["tradeDate:N", "strike:Q", "right:N", "volumeDelta:Q"],
        )
        .properties(height=260)
    )


def main() -> None:
    inject_css()
    st.title("ZF Option Chain Dashboard")

    with st.sidebar:
        st.subheader("数据")
        mode = st.radio("数据源", ["读取CSV快照", "连接IB实时"], index=0)
        refresh_seconds = st.number_input("页面刷新秒数", min_value=1, max_value=60, value=3, step=1)
        save_seconds = st.number_input("保存秒数", min_value=10, max_value=3600, value=60, step=10)
        ib_client_id = st.number_input("IB clientId", min_value=1, max_value=9999, value=DEFAULT_DASHBOARD_CLIENT_ID, step=1)
        request_interval = st.number_input("请求间隔秒", min_value=0.005, max_value=0.100, value=0.025, step=0.005, format="%.3f")
        max_live_contracts = st.number_input("最大实时订阅合约数", min_value=100, max_value=3000, value=900, step=100)
        kline_bar_size = st.selectbox("ZF日内K线周期", ["30 secs", "1 min", "2 mins", "5 mins"], index=1)
        kline_refresh_seconds = st.number_input("K线刷新秒数", min_value=10, max_value=300, value=30, step=10)
        st.session_state.kline_refresh_seconds = int(kline_refresh_seconds)
        dte0_width = st.number_input("0DTE strike 宽度", min_value=0.25, max_value=10.0, value=2.0, step=0.25)
        non_dte0_width = st.number_input("非0DTE strike 宽度", min_value=0.25, max_value=20.0, value=5.0, step=0.25)
        manual_price_text = st.text_area(
            "手工期货价 fallback",
            value="",
            placeholder="202606=106.95, 202609=106.50, 202612=106.00",
        )
        auto_refresh = st.checkbox("自动刷新页面", value=False)
        if st.button("停止IB订阅"):
            streamer = st.session_state.get("streamer")
            if streamer is not None:
                streamer.cancel()
            ib = st.session_state.get("ib")
            if ib is not None and ib.isConnected():
                ib.disconnect()
            for key in ["streamer", "ib", "active_conids", "ib_client_id", "subscription_generation", "last_subscribe_time", "last_subscribe_contracts"]:
                st.session_state.pop(key, None)
            st.success("已停止")

    manual_prices = parse_manual_prices(manual_price_text)
    if mode == "连接IB实时":
        if not UNIVERSE_CSV.exists():
            st.error(f"缺少 universe 文件：{UNIVERSE_CSV}")
            return
        snapshot, future_prices, filtered_universe = snapshot_from_live(
            int(refresh_seconds),
            int(save_seconds),
            float(request_interval),
            float(dte0_width),
            float(non_dte0_width),
            manual_prices,
            int(ib_client_id),
            int(max_live_contracts),
        )
    else:
        snapshot, future_prices, filtered_universe = snapshot_from_csv()

    render_ib_status(mode, future_prices, filtered_universe)
    render_subscription_status(mode)

    if snapshot.empty:
        st.warning("没有可视化数据。先运行 notebook 生成 ZF_FOP_Snapshot.csv，或切换到 IB 实时模式。")
        return

    for col in ["strike", "bid", "ask", "mid", "last", "close", "openInterest", "iv", "delta", "gamma", "theta", "vega"]:
        if col in snapshot.columns:
            snapshot[col] = pd.to_numeric(snapshot[col], errors="coerce")
    snapshot["expiration"] = snapshot["expiration"].astype(str)
    snapshot["right"] = snapshot["right"].astype(str)

    flow = load_flow_events_sqlite(FLOW_DB, limit=5000)
    make_metric_strip(snapshot, future_prices, flow)

    if mode == "连接IB实时":
        st.subheader("ZF 日内K线")
        bars = get_intraday_bars_safe(FUTURE_MONTHS[0], kline_bar_size)
        cached_ts = st.session_state.get(f"intraday_bars_ts_{FUTURE_MONTHS[0]}_{kline_bar_size}", 0.0)
        if cached_ts:
            st.caption(
                "K线数据缓存时间："
                + pd.Timestamp.fromtimestamp(cached_ts, tz="Asia/Shanghai").strftime("%H:%M:%S")
                + f"；刷新间隔 {st.session_state.get('kline_refresh_seconds', 30)} 秒"
            )
        candle_chart = chart_intraday_candles(bars)
        if candle_chart is not None:
            st.altair_chart(candle_chart, width="stretch")
        else:
            st.caption("暂无日内K线数据。若看到 10197，请先处理 competing live session。")

    expirations = sorted(snapshot["expiration"].dropna().unique())
    default_exp = 0
    selected_exp = st.selectbox("到期日", expirations, index=default_exp)

    spot = None
    if not future_prices.empty:
        valid_prices = pd.to_numeric(future_prices["price"], errors="coerce").dropna()
        if not valid_prices.empty:
            spot = float(valid_prices.iloc[0])
    if spot is None and "undPrice" in snapshot.columns:
        valid_und = pd.to_numeric(snapshot["undPrice"], errors="coerce").dropna()
        if not valid_und.empty:
            spot = float(valid_und.median())

    left, right = st.columns([1.15, 1.0], gap="large")
    with left:
        st.subheader("期权链")
        st.markdown(f"<div class='small-caption'>Expiration {selected_exp} · ATM reference {fmt(spot, 4) if spot else ''}</div>", unsafe_allow_html=True)
        st.markdown(build_chain_html(snapshot, selected_exp, spot), unsafe_allow_html=True)

    with right:
        st.subheader("曲线")
        iv_chart = chart_iv_smile(snapshot, selected_exp)
        if iv_chart is not None:
            st.altair_chart(iv_chart, width="stretch")
        heatmap = chart_heatmap(snapshot, "openInterest", "OI")
        if heatmap is not None:
            st.altair_chart(heatmap, width="stretch")

    st.subheader("成交情况")
    if flow.empty:
        st.caption("还没有 volumeDelta 事件。IB 的 volume 是累计值，需要至少两次快照后才可能产生差分。")
    else:
        c1, c2 = st.columns([1, 1], gap="large")
        with c1:
            bubble = chart_flow_heatmap(flow)
            if bubble is not None:
                st.altair_chart(bubble, width="stretch")
        with c2:
            bar = chart_flow_by_strike(flow)
            if bar is not None:
                st.altair_chart(bar, width="stretch")

    with st.expander("字段表"):
        features = prepare_snapshot_features(snapshot, include_chinese_columns=True)
        st.dataframe(features, width="stretch", height=360)

    if auto_refresh:
        time.sleep(int(refresh_seconds))
        st.rerun()


if __name__ == "__main__":
    main()
