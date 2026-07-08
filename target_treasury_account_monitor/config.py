from __future__ import annotations

from dataclasses import dataclass


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4001
DEFAULT_CLIENT_ID = 351
DEFAULT_REFRESH_SECONDS = 5
DEFAULT_GENERIC_TICKS = "100,101,106"
DEFAULT_TICK_SIZE = 0.25

TREASURY_ROOTS = {"ZT", "ZF", "ZN", "TN", "ZB", "UB", "ZC"}
DISCONNECT_ERROR_CODES = {1100, 1101, 1102, 1300, 2110}

MARKET_DATA_TYPES = {
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
    """Runtime settings shared by the Streamlit app and notebook tests."""

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
    infer_spreads: bool = False
