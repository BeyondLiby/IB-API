from __future__ import annotations

import html
import math
import time
from dataclasses import dataclass
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from ib_async import IB, util


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002
DEFAULT_CLIENT_ID = 301

MARKET_DATA_TYPES = {
    "Live 实时": 1,
    "Frozen 冻结": 2,
    "Delayed 延迟": 3,
    "Delayed frozen 延迟冻结": 4,
}


@dataclass(frozen=True)
class PortfolioSettings:
    host: str
    port: int
    client_id: int
    market_data_type: int
    account_filter: tuple[str, ...]
    futures_options_account: str
    stock_account: str
    quote_wait_seconds: float
    refresh_seconds: int
    auto_refresh: bool
    subscribe_quotes: bool


def inject_portfolio_css() -> None:
    st.markdown(
        """
        <style>
        .portfolio-hero {
            border: 1px solid #24313a;
            border-radius: 8px;
            background: linear-gradient(135deg, #111820 0%, #17222b 55%, #18251f 100%);
            padding: 18px 20px;
            margin-bottom: 14px;
        }
        .portfolio-title {
            color: #eef5f8;
            font-size: 30px;
            font-weight: 720;
            line-height: 1.18;
            margin: 0;
        }
        .portfolio-subtitle {
            color: #9fafbb;
            font-size: 13px;
            margin-top: 6px;
        }
        .portfolio-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 10px;
            margin: 12px 0 16px;
        }
        .portfolio-card {
            border: 1px solid #26343d;
            border-radius: 8px;
            background: #141d24;
            padding: 12px 14px;
            min-height: 86px;
        }
        .portfolio-card-label {
            color: #8fa0ad;
            font-size: 12px;
        }
        .portfolio-card-value {
            color: #eff7fb;
            font-size: 24px;
            font-weight: 720;
            line-height: 1.25;
            font-variant-numeric: tabular-nums;
            margin-top: 4px;
        }
        .portfolio-card-note {
            color: #8796a2;
            font-size: 11px;
            margin-top: 4px;
        }
        .pnl-pos { color: #55c98f; }
        .pnl-neg { color: #ef6b73; }
        .portfolio-section-title {
            color: #dfeaf0;
            font-size: 17px;
            font-weight: 680;
            margin: 18px 0 8px;
        }
        .portfolio-login-note {
            border: 1px solid #415060;
            border-radius: 8px;
            background: #121a22;
            color: #b9c5cf;
            padding: 12px 14px;
            margin: 10px 0 14px;
            font-size: 13px;
        }
        @media (max-width: 1100px) {
            .portfolio-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        }
        @media (max-width: 720px) {
            .portfolio-grid { grid-template-columns: 1fr; }
            .portfolio-title { font-size: 24px; }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def is_valid_number(value: Any, *, allow_zero: bool = True) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if math.isnan(number) or number == -1.0:
        return False
    return allow_zero or number != 0.0


def clean_number(value: Any) -> float:
    return float(value) if is_valid_number(value) else math.nan


def fmt_money(value: Any, digits: int = 0) -> str:
    if not is_valid_number(value):
        return "-"
    return f"${float(value):,.{digits}f}"


def fmt_number(value: Any, digits: int = 2) -> str:
    if not is_valid_number(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def fmt_percent(value: Any) -> str:
    if not is_valid_number(value):
        return "-"
    return f"{float(value):+.2f}%"


def pnl_class(value: Any) -> str:
    if not is_valid_number(value):
        return ""
    return "pnl-pos" if float(value) >= 0 else "pnl-neg"


def parse_accounts(text: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in text.replace("\n", ",").split(",") if part.strip())


def contract_label(contract: Any) -> str:
    for attr in ("localSymbol", "symbol"):
        value = getattr(contract, attr, "")
        if value:
            return str(value)
    return str(getattr(contract, "conId", ""))


def contract_group(contract: Any) -> str:
    sec_type = str(getattr(contract, "secType", "")).upper()
    if sec_type in {"STK", "ETF"}:
        return "股票"
    if sec_type in {"FUT", "FOP", "OPT"}:
        return "期货/期权"
    if sec_type in {"CASH", "CFD"}:
        return "现金/外汇"
    return sec_type or "其他"


def contract_multiplier(contract: Any) -> float:
    sec_type = str(getattr(contract, "secType", "")).upper()
    if sec_type in {"STK", "ETF", "CASH", "CFD"}:
        return 1.0
    raw = getattr(contract, "multiplier", None)
    if raw in (None, ""):
        return 1.0
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 1.0
    return value if value > 0 else 1.0


def ticker_price(ticker: Any) -> tuple[float, str]:
    market_price_attr = getattr(ticker, "marketPrice", None)
    market_price = market_price_attr() if callable(market_price_attr) else math.nan
    bid = clean_number(getattr(ticker, "bid", math.nan))
    ask = clean_number(getattr(ticker, "ask", math.nan))
    mid = (bid + ask) / 2.0 if is_valid_number(bid) and is_valid_number(ask) else math.nan
    candidates = [
        ("market", market_price),
        ("mid", mid),
        ("last", getattr(ticker, "last", math.nan)),
        ("close", getattr(ticker, "close", math.nan)),
        ("bid", bid),
        ("ask", ask),
    ]
    for source, value in candidates:
        if is_valid_number(value):
            return float(value), source
    return math.nan, ""


def estimate_cost_basis(contract: Any, quantity: float, avg_cost: float, price: float) -> float:
    if not is_valid_number(avg_cost):
        return math.nan
    multiplier = contract_multiplier(contract)
    sec_type = str(getattr(contract, "secType", "")).upper()

    if sec_type in {"OPT", "FOP"} and is_valid_number(price):
        # IB often reports option avgCost in currency per contract, while quotes are points.
        if abs(avg_cost) > abs(price * multiplier) * 0.5:
            return quantity * avg_cost

    return quantity * avg_cost * multiplier


def estimate_market_value(contract: Any, quantity: float, price: float) -> float:
    if not is_valid_number(price):
        return math.nan
    return quantity * price * contract_multiplier(contract)


def connect_ib(settings: PortfolioSettings) -> IB:
    existing = st.session_state.get("portfolio_ib")
    existing_key = st.session_state.get("portfolio_connection_key")
    key = (settings.host, settings.port, settings.client_id)
    if existing is not None and existing_key != key:
        disconnect_ib()
        existing = None

    if existing is not None and existing.isConnected():
        return existing

    util.startLoop()
    ib = IB()
    ib.connect(settings.host, settings.port, clientId=settings.client_id, timeout=10)
    ib.reqMarketDataType(settings.market_data_type)
    st.session_state.portfolio_ib = ib
    st.session_state.portfolio_connection_key = key
    st.session_state.portfolio_errors = []

    def on_error(req_id: int, error_code: int, error_string: str, contract: Any) -> None:
        errors = st.session_state.get("portfolio_errors", [])
        errors.append(
            {
                "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
                "reqId": req_id,
                "errorCode": error_code,
                "errorString": error_string,
                "contract": contract_label(contract) if contract else "",
            }
        )
        st.session_state.portfolio_errors = errors[-80:]

    ib.errorEvent += on_error
    st.session_state.portfolio_error_handler = on_error
    return ib


def disconnect_ib() -> None:
    ib = st.session_state.get("portfolio_ib")
    if ib is not None:
        for ticker in st.session_state.get("portfolio_tickers", {}).values():
            try:
                ib.cancelMktData(ticker.contract)
            except Exception:
                pass
        handler = st.session_state.get("portfolio_error_handler")
        if handler is not None:
            try:
                ib.errorEvent -= handler
            except ValueError:
                pass
        if ib.isConnected():
            ib.disconnect()
    for key in [
        "portfolio_ib",
        "portfolio_connection_key",
        "portfolio_tickers",
        "portfolio_ticker_key",
        "portfolio_error_handler",
        "portfolio_accounts",
    ]:
        st.session_state.pop(key, None)


def managed_accounts(ib: IB) -> list[str]:
    try:
        accounts = ib.managedAccounts()
        if accounts:
            return sorted(str(account) for account in accounts)
    except Exception:
        pass
    try:
        summary = ib.accountSummary()
        accounts = sorted({str(item.account) for item in summary if getattr(item, "account", "")})
        return accounts
    except Exception:
        return []


def portfolio_items_by_key(ib: IB) -> dict[tuple[str, int], Any]:
    try:
        items = ib.portfolio()
    except Exception:
        return {}
    out: dict[tuple[str, int], Any] = {}
    for item in items:
        account = str(getattr(item, "account", ""))
        contract = getattr(item, "contract", None)
        con_id = int(getattr(contract, "conId", 0) or 0)
        if account and con_id:
            out[(account, con_id)] = item
    return out


def fetch_positions(ib: IB, account_filter: tuple[str, ...]) -> list[Any]:
    positions = ib.positions()
    allowed = set(account_filter)
    if allowed:
        positions = [pos for pos in positions if str(getattr(pos, "account", "")) in allowed]
    return [pos for pos in positions if float(getattr(pos, "position", 0) or 0) != 0.0]


def update_quote_subscriptions(ib: IB, positions: list[Any], settings: PortfolioSettings) -> dict[int, Any]:
    if not settings.subscribe_quotes:
        for ticker in st.session_state.get("portfolio_tickers", {}).values():
            try:
                ib.cancelMktData(ticker.contract)
            except Exception:
                pass
        st.session_state.portfolio_tickers = {}
        st.session_state.portfolio_ticker_key = tuple()
        return {}

    contracts = [getattr(pos, "contract", None) for pos in positions]
    contracts = [contract for contract in contracts if contract is not None and getattr(contract, "conId", 0)]
    ticker_key = tuple(sorted(int(contract.conId) for contract in contracts))
    if st.session_state.get("portfolio_ticker_key") == ticker_key:
        return st.session_state.get("portfolio_tickers", {})

    for ticker in st.session_state.get("portfolio_tickers", {}).values():
        try:
            ib.cancelMktData(ticker.contract)
        except Exception:
            pass

    tickers: dict[int, Any] = {}
    for contract in contracts:
        try:
            ticker = ib.reqMktData(contract, genericTickList="", snapshot=False, regulatorySnapshot=False)
            tickers[int(contract.conId)] = ticker
        except Exception as exc:
            errors = st.session_state.get("portfolio_errors", [])
            errors.append(
                {
                    "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
                    "reqId": "",
                    "errorCode": "local",
                    "errorString": f"订阅失败: {exc}",
                    "contract": contract_label(contract),
                }
            )
            st.session_state.portfolio_errors = errors[-80:]
    if tickers and settings.quote_wait_seconds > 0:
        ib.sleep(settings.quote_wait_seconds)
    st.session_state.portfolio_tickers = tickers
    st.session_state.portfolio_ticker_key = ticker_key
    return tickers


def positions_to_frame(positions: list[Any], tickers: dict[int, Any], portfolio_map: dict[tuple[str, int], Any]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for pos in positions:
        contract = pos.contract
        account = str(pos.account)
        con_id = int(getattr(contract, "conId", 0) or 0)
        quantity = float(pos.position)
        portfolio_item = portfolio_map.get((account, con_id))
        ticker = tickers.get(con_id)

        price = math.nan
        price_source = ""
        if portfolio_item is not None and is_valid_number(getattr(portfolio_item, "marketPrice", math.nan)):
            price = float(portfolio_item.marketPrice)
            price_source = "portfolio"
        elif ticker is not None:
            price, price_source = ticker_price(ticker)

        avg_cost = clean_number(getattr(portfolio_item, "averageCost", math.nan)) if portfolio_item is not None else clean_number(pos.avgCost)
        market_value = clean_number(getattr(portfolio_item, "marketValue", math.nan)) if portfolio_item is not None else math.nan
        if not is_valid_number(market_value):
            market_value = estimate_market_value(contract, quantity, price)

        cost_basis = estimate_cost_basis(contract, quantity, avg_cost, price)
        unrealized_pnl = clean_number(getattr(portfolio_item, "unrealizedPNL", math.nan)) if portfolio_item is not None else math.nan
        if not is_valid_number(unrealized_pnl) and is_valid_number(market_value) and is_valid_number(cost_basis):
            unrealized_pnl = market_value - cost_basis
        pnl_pct = unrealized_pnl / abs(cost_basis) * 100.0 if is_valid_number(unrealized_pnl) and is_valid_number(cost_basis, allow_zero=False) else math.nan

        rows.append(
            {
                "账户": account,
                "分组": contract_group(contract),
                "合约": contract_label(contract),
                "类型": getattr(contract, "secType", ""),
                "交易所": getattr(contract, "exchange", ""),
                "币种": getattr(contract, "currency", ""),
                "数量": quantity,
                "均价": avg_cost,
                "现价": price,
                "价格来源": price_source,
                "市值": market_value,
                "成本": cost_basis,
                "未实现盈亏": unrealized_pnl,
                "盈亏%": pnl_pct,
                "conId": con_id,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["账户", "分组", "未实现盈亏"], ascending=[True, True, False], ignore_index=True)


def account_summary_frame(ib: IB, accounts: list[str]) -> pd.DataFrame:
    try:
        rows = ib.accountSummary()
    except Exception:
        return pd.DataFrame()
    wanted = {"NetLiquidation", "TotalCashValue", "AvailableFunds", "BuyingPower", "UnrealizedPnL", "RealizedPnL", "GrossPositionValue"}
    data = []
    allowed = set(accounts)
    for item in rows:
        account = str(getattr(item, "account", ""))
        if allowed and account not in allowed:
            continue
        tag = str(getattr(item, "tag", ""))
        if tag in wanted:
            data.append(
                {
                    "账户": account,
                    "指标": tag,
                    "值": pd.to_numeric(getattr(item, "value", math.nan), errors="coerce"),
                    "币种": getattr(item, "currency", ""),
                }
            )
    return pd.DataFrame(data)


def render_metric_cards(frame: pd.DataFrame, summary: pd.DataFrame) -> None:
    market_value = frame["市值"].sum(skipna=True) if not frame.empty else math.nan
    pnl = frame["未实现盈亏"].sum(skipna=True) if not frame.empty else math.nan
    pnl_pct = pnl / frame["成本"].abs().sum(skipna=True) * 100.0 if not frame.empty and frame["成本"].abs().sum(skipna=True) else math.nan
    accounts = frame["账户"].nunique() if not frame.empty else 0

    net_liq = math.nan
    if not summary.empty:
        rows = summary[summary["指标"] == "NetLiquidation"]
        if not rows.empty:
            net_liq = rows["值"].sum(skipna=True)

    st.markdown(
        f"""
        <div class="portfolio-grid">
          <div class="portfolio-card">
            <div class="portfolio-card-label">账户净值</div>
            <div class="portfolio-card-value">{fmt_money(net_liq)}</div>
            <div class="portfolio-card-note">来自 IB accountSummary</div>
          </div>
          <div class="portfolio-card">
            <div class="portfolio-card-label">持仓市值</div>
            <div class="portfolio-card-value">{fmt_money(market_value)}</div>
            <div class="portfolio-card-note">当前持仓合约估算/IB 回传</div>
          </div>
          <div class="portfolio-card">
            <div class="portfolio-card-label">未实现盈亏</div>
            <div class="portfolio-card-value {pnl_class(pnl)}">{fmt_money(pnl)}</div>
            <div class="portfolio-card-note">{fmt_percent(pnl_pct)}</div>
          </div>
          <div class="portfolio-card">
            <div class="portfolio-card-label">持仓账户</div>
            <div class="portfolio-card-value">{accounts}</div>
            <div class="portfolio-card-note">{len(frame):,} 个非零持仓</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def chart_pnl_by_account(frame: pd.DataFrame):
    if frame.empty:
        return None
    data = frame.groupby(["账户", "分组"], as_index=False)["未实现盈亏"].sum()
    data = data[pd.to_numeric(data["未实现盈亏"], errors="coerce").notna()]
    if data.empty:
        return None
    return (
        alt.Chart(data)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("账户:N", title="账户"),
            y=alt.Y("未实现盈亏:Q", title="未实现盈亏"),
            color=alt.Color("分组:N", title="分组", scale=alt.Scale(range=["#55c98f", "#69a7ff", "#e5b454", "#ef6b73"])),
            tooltip=["账户:N", "分组:N", alt.Tooltip("未实现盈亏:Q", format=",.2f")],
        )
        .properties(height=260)
    )


def render_holdings_table(frame: pd.DataFrame) -> None:
    if frame.empty:
        st.info("当前没有读到非零持仓。请确认 Gateway/TWS 登录账户、交易权限，以及 API 配置已启用。")
        return

    display = frame.copy()
    numeric_cols = ["数量", "均价", "现价", "市值", "成本", "未实现盈亏", "盈亏%"]
    for col in numeric_cols:
        display[col] = pd.to_numeric(display[col], errors="coerce")
    st.dataframe(
        display[
            [
                "账户",
                "分组",
                "合约",
                "类型",
                "数量",
                "均价",
                "现价",
                "市值",
                "未实现盈亏",
                "盈亏%",
                "价格来源",
                "币种",
                "conId",
            ]
        ].style.format(
            {
                "数量": "{:,.2f}",
                "均价": "{:,.4f}",
                "现价": "{:,.4f}",
                "市值": "${:,.0f}",
                "未实现盈亏": "${:,.0f}",
                "盈亏%": "{:+.2f}%",
            },
            na_rep="-",
        ),
        width="stretch",
        height=420,
    )


def render_account_blocks(frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    for account, one in frame.groupby("账户", sort=True):
        value = one["市值"].sum(skipna=True)
        pnl = one["未实现盈亏"].sum(skipna=True)
        st.markdown(
            f"<div class='portfolio-section-title'>{html.escape(str(account))} · {fmt_money(value)} · "
            f"<span class='{pnl_class(pnl)}'>{fmt_money(pnl)}</span></div>",
            unsafe_allow_html=True,
        )
        render_holdings_table(one)


def render_login_note() -> None:
    st.markdown(
        """
        <div class="portfolio-login-note">
        <b>登录确认：</b>如果期货期权账户和股票账户都挂在同一个 IBKR 登录名/Advisor 结构下，
        这个页面通常可以一次连接 TWS/IB Gateway 后同时读取两个账户。若两个账户必须用不同用户名登录，
        就需要两个 Gateway/TWS 实例、两个端口和两套 session，模块需要再扩展成多连接版本。
        <br><br>
        <b>手机端行情：</b>实时行情订阅可能触发 IB 的 competing live session 限制。你可以先用
        Frozen/Delayed 模式验证持仓读取，确认无误后再启用 Live 实时行情。
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_errors() -> None:
    errors = st.session_state.get("portfolio_errors", [])
    latest_10197 = next((err for err in reversed(errors) if err.get("errorCode") == 10197), None)
    if latest_10197:
        st.error("IB 返回 10197：实时行情被其他 live session 占用。请关闭手机端/TWS 行情窗口，或切到 Frozen/Delayed 模式。")
    if errors:
        with st.expander("最近 IB API 提示"):
            st.dataframe(pd.DataFrame(errors).tail(20), width="stretch", height=220)


def render_portfolio_monitor() -> None:
    inject_portfolio_css()
    st.markdown(
        """
        <div class="portfolio-hero">
          <div class="portfolio-title">持仓表现监控</div>
          <div class="portfolio-subtitle">按账户聚合股票、期货和期权持仓，实时刷新价格、市值和未实现盈亏。</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_login_note()

    with st.sidebar:
        st.subheader("持仓连接")
        enabled = st.checkbox("启用 IB 持仓监控", value=False)
        host = st.text_input("IB host", value=DEFAULT_HOST)
        port = st.number_input("IB port", min_value=1, max_value=65535, value=DEFAULT_PORT, step=1)
        client_id = st.number_input("持仓 clientId", min_value=1, max_value=9999, value=DEFAULT_CLIENT_ID, step=1)
        market_label = st.selectbox("行情模式", list(MARKET_DATA_TYPES.keys()), index=0)
        subscribe_quotes = st.checkbox("订阅持仓实时行情", value=True)
        quote_wait_seconds = st.number_input("首次等价秒数", min_value=0.0, max_value=10.0, value=1.5, step=0.5)
        refresh_seconds = st.number_input("持仓刷新秒数", min_value=2, max_value=120, value=5, step=1)
        futures_options_account = st.text_input("期货期权账户ID", value="")
        stock_account = st.text_input("股票账户ID", value="")
        extra_accounts = st.text_area("其他账户过滤", value="", placeholder="可留空；多个账户用逗号分隔")
        auto_refresh = st.checkbox("自动刷新持仓页", value=False)
        if st.button("断开持仓连接"):
            disconnect_ib()
            st.success("已断开持仓连接")

    accounts = parse_accounts(",".join([futures_options_account, stock_account, extra_accounts]))
    settings = PortfolioSettings(
        host=host.strip() or DEFAULT_HOST,
        port=int(port),
        client_id=int(client_id),
        market_data_type=MARKET_DATA_TYPES[market_label],
        account_filter=accounts,
        futures_options_account=futures_options_account.strip(),
        stock_account=stock_account.strip(),
        quote_wait_seconds=float(quote_wait_seconds),
        refresh_seconds=int(refresh_seconds),
        auto_refresh=bool(auto_refresh),
        subscribe_quotes=bool(subscribe_quotes),
    )

    if not enabled:
        disconnect_ib()
        st.info("已就绪。勾选侧边栏的“启用 IB 持仓监控”后才会连接 Gateway/TWS。")
        return

    try:
        ib = connect_ib(settings)
        available_accounts = managed_accounts(ib)
        if available_accounts:
            st.caption("IB 可见账户：" + " / ".join(available_accounts))
        selected_accounts = list(settings.account_filter) or available_accounts
        positions = fetch_positions(ib, settings.account_filter)
        tickers = update_quote_subscriptions(ib, positions, settings)
        portfolio_map = portfolio_items_by_key(ib)
        frame = positions_to_frame(positions, tickers, portfolio_map)
        summary = account_summary_frame(ib, selected_accounts)
    except Exception as exc:
        st.error(f"持仓监控连接/刷新失败：{exc}")
        render_errors()
        return

    last_update = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d %H:%M:%S")
    st.caption(f"最后刷新：{last_update} · clientId {settings.client_id} · 行情模式 {market_label}")
    render_errors()
    render_metric_cards(frame, summary)

    chart = chart_pnl_by_account(frame)
    if chart is not None:
        st.altair_chart(chart, width="stretch")

    account_tab, all_tab, summary_tab = st.tabs(["按账户", "全部持仓", "账户摘要"])
    with account_tab:
        render_account_blocks(frame)
    with all_tab:
        render_holdings_table(frame)
    with summary_tab:
        if summary.empty:
            st.caption("暂未取得 accountSummary。")
        else:
            st.dataframe(summary, width="stretch", height=360)

    if settings.auto_refresh:
        time.sleep(settings.refresh_seconds)
        st.rerun()
