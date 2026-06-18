from __future__ import annotations

from dataclasses import dataclass, field


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 4002
DEFAULT_CLIENT_ID = 601
DEFAULT_MARKET_DATA_TYPE = 1
DEFAULT_CURRENCY = "USD"

# IBKR's TWS API models ForecastEx event contracts as options. The exchange
# code is FORECASTX, without the final "E" in ForecastEx.
DEFAULT_EVENT_EXCHANGES = ("FORECASTX",)
DEFAULT_EVENT_SEC_TYPES = ("OPT",)

# These are product-code seeds, not a universe endpoint. IBKR documents that
# Market Scanners are not available for Event Contracts; users discover product
# symbols through ForecastTrader and then query the option-like contracts.
DEFAULT_EVENT_SYMBOL_SEEDS = (
    "GCE",
)

EVENT_TEXT_MARKERS = (
    "FORECAST",
    "FORECASTEX",
    "FORECASTTRADER",
    "EVENT",
    "PREDICTION",
)


@dataclass(frozen=True)
class IBConnectionSettings:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    client_id: int = DEFAULT_CLIENT_ID
    market_data_type: int = DEFAULT_MARKET_DATA_TYPE
    readonly: bool = True
    timeout: float = 10.0


@dataclass(frozen=True)
class ScanSettings:
    symbols: tuple[str, ...] = field(default_factory=lambda: DEFAULT_EVENT_SYMBOL_SEEDS)
    local_symbols: tuple[str, ...] = ()
    expirations: tuple[str, ...] = ()
    strikes: tuple[float, ...] = ()
    rights: tuple[str, ...] = ()
    sec_types: tuple[str, ...] = field(default_factory=lambda: DEFAULT_EVENT_SEC_TYPES)
    exchanges: tuple[str, ...] = field(default_factory=lambda: DEFAULT_EVENT_EXCHANGES)
    currency: str = DEFAULT_CURRENCY
    keep_all_matches: bool = False
    request_pause_seconds: float = 0.15
