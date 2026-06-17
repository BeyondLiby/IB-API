from __future__ import annotations

import contextlib
import io
import math
import logging
import time
from typing import Any

import pandas as pd
from ib_async import IB, util
from ib_async.ib import StartupFetch

try:
    import streamlit as st
except ModuleNotFoundError:
    st = None

try:
    from .config import (
        ACCOUNT_TAGS,
        AUTO_MARKET_DATA_TYPE,
        DELAYED_MARKET_DATA_TYPE,
        DEFAULT_GENERIC_TICKS,
        DISCONNECT_ERROR_CODES,
        LIVE_MARKET_DATA_TYPE,
        MonitorSettings,
    )
    from .contracts import contract_label, is_treasury_contract, normalize_market_data_contract
    from .market_data import ticker_has_price
except ImportError:
    from config import (
        ACCOUNT_TAGS,
        AUTO_MARKET_DATA_TYPE,
        DELAYED_MARKET_DATA_TYPE,
        DEFAULT_GENERIC_TICKS,
        DISCONNECT_ERROR_CODES,
        LIVE_MARKET_DATA_TYPE,
        MonitorSettings,
    )
    from contracts import contract_label, is_treasury_contract, normalize_market_data_contract
    from market_data import ticker_has_price

REALTIME_UNAVAILABLE_CODES = {354, 10197}
PARTIAL_MARKET_DATA_CODES = {10090}
NOISY_MARKET_DATA_CODES = REALTIME_UNAVAILABLE_CODES | PARTIAL_MARKET_DATA_CODES | {300, 322}


def configure_ib_error_logging() -> None:
    """关闭 ib_async/ibapi 默认错误日志，避免 CLI/notebook 被原始英文/转义输出刷屏。"""
    for name in ("ib_async", "ib_async.wrapper", "ib_async.client", "ibapi", "ibapi.wrapper", "ibapi.client"):
        logger = logging.getLogger(name)
        logger.setLevel(logging.CRITICAL)
        logger.propagate = False


configure_ib_error_logging()


@contextlib.contextmanager
def suppress_ib_error_output() -> Any:
    """吞掉 ib_async/ibapi 在行情请求期间直接写到控制台的原始错误。"""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
        yield


def decode_ib_text(value: Any) -> str:
    """把 IB 返回的 '\\uXXXX' 转义文本还原成正常中文。"""
    text = str(value or "")
    if "\\u" not in text:
        return text
    try:
        return text.encode("utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return text


def ib_error_summary(error_code: Any, error_string: Any = "") -> str:
    """把常见 IB 行情错误压缩成中文摘要。"""
    try:
        code = int(error_code)
    except (TypeError, ValueError):
        code = 0
    text = decode_ib_text(error_string)
    if code == 354:
        return "没有实时行情权限，已使用延迟行情。"
    if code == 10090:
        return "部分行情字段未订阅，基础报价或延迟行情仍可继续使用。"
    if code == 10197:
        return "实时行情被其他设备或会话占用，已使用延迟行情。"
    if code == 300:
        return "行情 ticker 已取消或不存在，可忽略。"
    if code == 322:
        return "IB 处理某个行情请求失败，本轮会继续使用可用数据。"
    return text


def session_state() -> Any:
    """读取 Streamlit 会话状态；命令行函数不会调用它。"""
    if st is None:
        raise ImportError("Streamlit session state is only available after installing streamlit")
    return st.session_state


def initial_market_data_type(settings: MonitorSettings) -> int:
    """Auto 模式连接时先按实时行情请求，失败后订阅阶段再切 delayed。"""
    return LIVE_MARKET_DATA_TYPE if settings.market_data_type == AUTO_MARKET_DATA_TYPE else settings.market_data_type


def connect_ib(settings: MonitorSettings) -> IB:
    """创建或复用 Streamlit 会话里的 IB 连接。"""
    state = session_state()
    existing = state.get("target_ib")
    existing_key = state.get("target_connection_key")
    key = (settings.host, settings.port, settings.client_id, settings.readonly)
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
        readonly=settings.readonly,
        fetchFields=(
            StartupFetch.POSITIONS
            | StartupFetch.ACCOUNT_UPDATES
            | StartupFetch.SUB_ACCOUNT_UPDATES
        ),
    )
    ib.reqMarketDataType(initial_market_data_type(settings))
    state.target_ib = ib
    state.target_connection_key = key
    state.target_errors = []
    state.target_needs_reconnect = False

    def on_error(req_id: int, error_code: int, error_string: str, contract: Any) -> None:
        """记录 IB API 消息，并标记需要重连的连接事件。"""
        summary = ib_error_summary(error_code, error_string)
        row = {
            "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
            "reqId": req_id,
            "errorCode": error_code,
            "errorString": summary,
            "contract": contract_label(contract) if contract else "",
        }
        if int(error_code or 0) not in NOISY_MARKET_DATA_CODES:
            errors = state.get("target_errors", [])
            errors.append(row)
            state.target_errors = errors[-100:]
        if int(error_code or 0) in DISCONNECT_ERROR_CODES:
            state.target_needs_reconnect = True

    ib.errorEvent += on_error
    state.target_error_handler = on_error
    return ib


def disconnect_ib() -> None:
    """取消行情订阅并清理缓存连接。"""
    state = session_state()
    ib = state.get("target_ib")
    if ib is not None:
        for ticker in state.get("target_tickers", {}).values():
            try:
                ib.cancelMktData(ticker.contract)
            except Exception:
                pass
        handler = state.get("target_error_handler")
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
        "target_requested_ticker_key",
        "target_needs_reconnect",
    ]:
        state.pop(key, None)


def maybe_reconnect(settings: MonitorSettings) -> None:
    """IB 断线后按退避时间自动重连。"""
    state = session_state()
    ib = state.get("target_ib")
    needs_reconnect = bool(state.get("target_needs_reconnect", False))
    if not settings.auto_reconnect:
        return
    if ib is not None and ib.isConnected() and not needs_reconnect:
        return
    last_attempt = float(state.get("target_last_reconnect_attempt", 0.0))
    now = time.monotonic()
    if now - last_attempt < settings.reconnect_backoff_seconds:
        return
    state.target_last_reconnect_attempt = now
    disconnect_ib()
    connect_ib(settings)


def managed_accounts(ib: IB) -> list[str]:
    """返回当前 IB 会话可见的账户。"""
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
    """读取目标账户非零持仓，并筛选出美债期货/期权。"""
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
    """按 conId 建立 portfolio item 映射，用来补充市值和 PnL。"""
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
    """请求账户更新，让 portfolio 市值和 PnL 有时间填充。"""
    if not account:
        return
    try:
        ib.reqAccountUpdates(True, account)
        ib.sleep(wait_seconds)
    except Exception:
        pass


def cancel_tickers(ib: IB, tickers: dict[int, Any]) -> None:
    """取消一组行情订阅，忽略已经失效的 ticker 句柄。"""
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
    """从持仓生成行情合约，并保留原始 conId 作为回填 key。"""
    contracts: list[tuple[int, Any]] = []
    for pos in positions:
        contract = getattr(pos, "contract", None)
        original_con_id = int(getattr(contract, "conId", 0) or 0)
        if contract is None or original_con_id == 0:
            continue
        contracts.append((original_con_id, normalize_market_data_contract(contract)))
    return contracts


def add_ticker_aliases(tickers: dict[int, Any], original_con_id: int, ticker: Any) -> None:
    """同时按原始 conId 和 qualify 后 conId 保存 ticker，避免查表落空。"""
    tickers[original_con_id] = ticker
    qualified_con_id = int(getattr(getattr(ticker, "contract", None), "conId", 0) or 0)
    if qualified_con_id:
        tickers[qualified_con_id] = ticker


def probe_live_market_data(ib: IB, contract: Any, wait_seconds: float = 2.0) -> tuple[bool, str]:
    """用一只合约探测实时行情权限；只有明确返回 Live 类型才算成功。"""
    messages: list[dict[str, Any]] = []

    def on_probe_error(req_id: int, error_code: int, error_string: str, error_contract: Any) -> None:
        messages.append(
            {
                "reqId": req_id,
                "errorCode": int(error_code or 0),
                "errorString": ib_error_summary(error_code, error_string),
                "contract": contract_label(error_contract) if error_contract else "",
            }
        )

    ticker = None
    try:
        with suppress_ib_error_output():
            ib.reqMarketDataType(LIVE_MARKET_DATA_TYPE)
            ib.errorEvent += on_probe_error
            ticker = ib.reqMktData(contract, genericTickList="", snapshot=False, regulatorySnapshot=False)
            ib.sleep(wait_seconds)
    except Exception as exc:
        messages.append({"errorCode": "local", "errorString": str(exc), "contract": contract_label(contract)})
    finally:
        if ticker is not None:
            try:
                ib.cancelMktData(ticker.contract)
            except Exception:
                pass
        try:
            ib.errorEvent -= on_probe_error
        except (NameError, ValueError):
            pass

    for row in messages:
        if row.get("errorCode") in REALTIME_UNAVAILABLE_CODES:
            return False, f"{row.get('errorString', '实时行情不可用，已切换到延迟行情。')}"
    market_data_type = int(getattr(ticker, "marketDataType", 0) or 0) if ticker is not None else 0
    if market_data_type == LIVE_MARKET_DATA_TYPE:
        return True, "实时行情探测确认 marketDataType=Live，本轮使用 Live。"
    partial = [row for row in messages if row.get("errorCode") in PARTIAL_MARKET_DATA_CODES]
    if partial:
        return False, "实时行情探测只收到部分字段权限提示，已切换到延迟行情。"
    if ticker is not None and ticker_has_price(ticker):
        return False, "探测合约有价格但未确认是实时行情，已切换到延迟行情。"
    return False, "实时行情探测未确认 Live 权限，已切换到延迟行情。"


def attach_market_data_error_collector(ib: IB) -> tuple[list[dict[str, Any]], Any]:
    """临时收集本轮行情订阅产生的权限错误。"""
    messages: list[dict[str, Any]] = []

    def on_error(req_id: int, error_code: int, error_string: str, contract: Any) -> None:
        code = int(error_code or 0)
        if code not in REALTIME_UNAVAILABLE_CODES and code not in PARTIAL_MARKET_DATA_CODES:
            return
        messages.append(
            {
                "reqId": req_id,
                "errorCode": code,
                "errorString": ib_error_summary(code, error_string),
                "contract": contract_label(contract) if contract else "",
            }
        )

    ib.errorEvent += on_error
    return messages, on_error


def detach_market_data_error_collector(ib: IB, handler: Any) -> None:
    """移除临时行情错误监听器。"""
    try:
        ib.errorEvent -= handler
    except ValueError:
        pass


def has_realtime_unavailable(messages: list[dict[str, Any]]) -> bool:
    """判断本轮订阅是否遇到实时行情不可用。"""
    return any(row.get("errorCode") in REALTIME_UNAVAILABLE_CODES for row in messages)


def realtime_unavailable_detail(messages: list[dict[str, Any]]) -> str:
    """生成实时行情不可用的简短说明。"""
    for row in messages:
        if row.get("errorCode") in REALTIME_UNAVAILABLE_CODES:
            return str(row.get("errorString", "实时行情不可用。"))
    return ""


def resolve_market_data_type(
    ib: IB,
    contracts: list[tuple[int, Any]],
    settings: MonitorSettings,
    *,
    notify: Any | None = None,
) -> int:
    """解析本轮实际使用的行情类型；Auto/Live 会先探测，失败后回退 Delayed。"""
    if settings.market_data_type not in {AUTO_MARKET_DATA_TYPE, LIVE_MARKET_DATA_TYPE}:
        return settings.market_data_type
    if not contracts:
        return settings.market_data_type

    _, contract = contracts[0]
    try:
        ib.qualifyContracts(contract)
    except Exception:
        pass
    live_ok, detail = probe_live_market_data(ib, contract)
    effective_type = LIVE_MARKET_DATA_TYPE if live_ok else DELAYED_MARKET_DATA_TYPE
    if notify is not None:
        notify(
            {
                "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
                "requested": "Auto" if settings.market_data_type == AUTO_MARKET_DATA_TYPE else "Live",
                "effective": "Live" if live_ok else "Delayed",
                "detail": detail,
            }
        )
    return effective_type


def request_streaming_tickers(
    ib: IB,
    contracts: list[tuple[int, Any]],
    *,
    market_data_type: int,
    wait_seconds: float,
) -> tuple[dict[int, Any], list[dict[str, Any]]]:
    """按指定行情类型订阅一批合约，并返回本轮权限错误。"""
    tickers: dict[int, Any] = {}
    messages, handler = attach_market_data_error_collector(ib)
    try:
        with suppress_ib_error_output():
            ib.reqMarketDataType(market_data_type)
            for original_con_id, contract in contracts:
                try:
                    ib.qualifyContracts(contract)
                except Exception:
                    pass
                try:
                    ticker = ib.reqMktData(contract, genericTickList=DEFAULT_GENERIC_TICKS, snapshot=False, regulatorySnapshot=False)
                    add_ticker_aliases(tickers, original_con_id, ticker)
                except Exception as exc:
                    messages.append(
                        {
                            "reqId": "",
                            "errorCode": "local",
                            "errorString": f"subscribe failed: {exc}",
                            "contract": contract_label(contract),
                        }
                    )
            if tickers and wait_seconds > 0:
                ib.sleep(wait_seconds)
    finally:
        detach_market_data_error_collector(ib, handler)
    return tickers, messages


def add_snapshot_fallbacks(
    ib: IB,
    contracts: list[tuple[int, Any]],
    tickers: dict[int, Any],
    *,
    market_data_type: int,
) -> None:
    """对还没有价格的流式 ticker 再做一次阻塞快照兜底。"""
    def missing_items() -> tuple[list[int], list[Any]]:
        """找出当前仍缺少价格的合约。"""
        missing_contracts = []
        missing_original_ids = []
        for original_con_id, contract in contracts:
            ticker = tickers.get(original_con_id)
            if ticker is None or not ticker_has_price(ticker):
                missing_original_ids.append(original_con_id)
                missing_contracts.append(contract)
        return missing_original_ids, missing_contracts

    missing_original_ids, missing_contracts = missing_items()
    if not missing_contracts:
        return

    if market_data_type != LIVE_MARKET_DATA_TYPE:
        try:
            with suppress_ib_error_output():
                snapshots = ib.reqTickers(*missing_contracts)
            for original_con_id, snapshot in zip(missing_original_ids, snapshots):
                add_ticker_aliases(tickers, original_con_id, snapshot)
        except Exception:
            pass
        return

    try:
        with suppress_ib_error_output():
            ib.reqMarketDataType(DELAYED_MARKET_DATA_TYPE)
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
    """订阅持仓行情，不依赖 Streamlit 状态，供脚本和 notebook 使用。"""
    if previous_tickers:
        cancel_tickers(ib, previous_tickers)

    tickers: dict[int, Any] = {}
    contracts = treasury_market_data_contracts(positions)
    effective_market_data_type = resolve_market_data_type(
        ib,
        contracts,
        settings,
        notify=None,
    )
    initial_effective_market_data_type = effective_market_data_type
    tickers, messages = request_streaming_tickers(
        ib,
        contracts,
        market_data_type=effective_market_data_type,
        wait_seconds=settings.quote_wait_seconds,
    )
    if effective_market_data_type == LIVE_MARKET_DATA_TYPE and has_realtime_unavailable(messages):
        cancel_tickers(ib, tickers)
        detail = realtime_unavailable_detail(messages)
        print(f"{pd.Timestamp.now(tz='Asia/Shanghai').strftime('%H:%M:%S')} market data: Live 批量订阅出现实时权限错误，已整批切换到 Delayed：{detail}")
        effective_market_data_type = DELAYED_MARKET_DATA_TYPE
        tickers, messages = request_streaming_tickers(
            ib,
            contracts,
            market_data_type=effective_market_data_type,
            wait_seconds=settings.quote_wait_seconds,
        )
    elif initial_effective_market_data_type == LIVE_MARKET_DATA_TYPE:
        print(f"{pd.Timestamp.now(tz='Asia/Shanghai').strftime('%H:%M:%S')} market data: 批量订阅未发现实时权限错误，本轮使用 Live。")
    for row in messages:
        if row.get("errorCode") == "local":
            print(f"subscribe failed for {row.get('contract')}: {row.get('errorString')}")
    add_snapshot_fallbacks(ib, contracts, tickers, market_data_type=effective_market_data_type)
    return tickers


def update_quote_subscriptions(
    ib: IB,
    positions: list[Any],
    settings: MonitorSettings,
) -> dict[int, Any]:
    """为当前美债持仓订阅行情，Streamlit 页面会复用已有订阅。"""
    state = session_state()
    contracts = treasury_market_data_contracts(positions)
    contract_ids = tuple(sorted(original_con_id for original_con_id, _ in contracts))
    requested_ticker_key = (settings.market_data_type, contract_ids)
    if state.get("target_requested_ticker_key") == requested_ticker_key and state.get("target_tickers"):
        return state.get("target_tickers", {})

    def save_market_data_notice(row: dict[str, Any]) -> None:
        notices = state.get("target_market_data_notices", [])
        notices.append(row)
        state.target_market_data_notices = notices[-20:]

    effective_market_data_type = resolve_market_data_type(
        ib,
        contracts,
        settings,
        notify=save_market_data_notice,
    )
    cancel_tickers(ib, state.get("target_tickers", {}))
    tickers, messages = request_streaming_tickers(
        ib,
        contracts,
        market_data_type=effective_market_data_type,
        wait_seconds=settings.quote_wait_seconds,
    )
    if effective_market_data_type == LIVE_MARKET_DATA_TYPE and has_realtime_unavailable(messages):
        cancel_tickers(ib, tickers)
        detail = realtime_unavailable_detail(messages)
        effective_market_data_type = DELAYED_MARKET_DATA_TYPE
        save_market_data_notice(
            {
                "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
                "requested": "Auto" if settings.market_data_type == AUTO_MARKET_DATA_TYPE else "Live",
                "effective": "Delayed",
                "detail": f"Live 批量订阅出现实时权限错误，已整批切换到 Delayed：{detail}",
            }
        )
        tickers, messages = request_streaming_tickers(
            ib,
            contracts,
            market_data_type=effective_market_data_type,
            wait_seconds=settings.quote_wait_seconds,
        )

    ticker_key = (
        settings.market_data_type,
        effective_market_data_type,
        contract_ids,
    )
    local_errors = [row for row in messages if row.get("errorCode") == "local"]
    if local_errors:
        errors = state.get("target_errors", [])
        for row in local_errors:
            errors.append(
                {
                    "time": pd.Timestamp.now(tz="Asia/Shanghai").strftime("%H:%M:%S"),
                    "reqId": row.get("reqId", ""),
                    "errorCode": "local",
                    "errorString": row.get("errorString", ""),
                    "contract": row.get("contract", ""),
                }
            )
        state.target_errors = errors[-100:]
    add_snapshot_fallbacks(ib, contracts, tickers, market_data_type=effective_market_data_type)
    state.target_tickers = tickers
    state.target_ticker_key = ticker_key
    state.target_requested_ticker_key = requested_ticker_key
    state.target_effective_market_data_type = effective_market_data_type
    return tickers


def account_summary_frame(ib: IB, account: str) -> pd.DataFrame:
    """标准化目标账户的 accountSummary 关键指标。"""
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
