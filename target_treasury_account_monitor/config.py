from __future__ import annotations

from dataclasses import dataclass


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4001
DEFAULT_CLIENT_ID = 351
DEFAULT_REFRESH_SECONDS = 60
DEFAULT_GENERIC_TICKS = ""
DEFAULT_ORDER_PREVIEW_ENABLED = True
AUTO_MARKET_DATA_TYPE = 0
LIVE_MARKET_DATA_TYPE = 1
DELAYED_MARKET_DATA_TYPE = 3
DEFAULT_MARKET_DATA_LABEL = "Auto (Live -> Delayed)"

TREASURY_ROOTS = {"ZT", "ZF", "ZN", "TN", "ZB", "UB"}
DISCONNECT_ERROR_CODES = {1100, 1101, 1102, 1300, 2110}

MARKET_DATA_TYPES = {
    DEFAULT_MARKET_DATA_LABEL: AUTO_MARKET_DATA_TYPE,
    "Live": 1,
    "Frozen": 2,
    "Delayed": 3,
    "Delayed frozen": 4,
}

ACCOUNT_TAGS = {
    "NetLiquidation",
    "TotalCashValue",
    "AvailableFunds",
    "ExcessLiquidity",
    "BuyingPower",
    "GrossPositionValue",
    "InitMarginReq",
    "MaintMarginReq",
    "FullInitMarginReq",
    "FullMaintMarginReq",
    "FullAvailableFunds",
    "FullExcessLiquidity",
    "LookAheadAvailableFunds",
    "LookAheadExcessLiquidity",
    "UnrealizedPnL",
    "RealizedPnL",
    "SMA",
    "Leverage",
}


@dataclass(frozen=True)
class MonitorSettings:
    """监控运行参数，Streamlit、命令行脚本和 notebook 共用这一份配置。"""

    host: str
    port: int
    client_id: int
    account: str
    market_data_type: int
    quote_wait_seconds: float
    refresh_seconds: int
    auto_refresh: bool
    auto_reconnect: bool
    reconnect_backoff_seconds: int
    wechat_webhook_url: str
    wechat_push_enabled: bool
    wechat_min_interval_seconds: int
    order_preview_enabled: bool = DEFAULT_ORDER_PREVIEW_ENABLED
    readonly: bool = False
