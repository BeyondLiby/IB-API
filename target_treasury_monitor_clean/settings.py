from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


MARKET_DATA_TYPES = {
    "live": 1,
    "frozen": 2,
    "delayed": 3,
    "delayed_frozen": 4,
}

DEFAULT_IB_ACCOUNT = "U16251798"


@dataclass(frozen=True)
class IBSettings:
    """Connection settings shared by CLI scripts and the Streamlit app."""

    host: str = "127.0.0.1"
    port: int = 4001
    client_id: int = 351
    account: str = DEFAULT_IB_ACCOUNT
    market_data_type: int = 1
    readonly: bool = True


@dataclass(frozen=True)
class AccountDashboardSettings:
    """Controls for the account-position dashboard snapshot."""

    quote_wait_seconds: float = 6.0
    infer_spreads: bool = False
    reference_root: str = "ZF"


@dataclass(frozen=True)
class StaticChainSettings:
    """One-shot batch chain refresh settings."""

    root: str = "ZF"
    future_months: str = "202609,202612"
    min_expiration: str | None = None
    max_expiration: str | None = None
    qualify_batch_size: int = 300
    batch_size: int = 150
    wait_max_seconds: float = 10.0
    wait_stable_seconds: float = 2.0
    request_interval: float = 0.025
    inter_batch_pause_seconds: float = 0.5
    empty_batch_retries: int = 1
    empty_batch_retry_pause_seconds: float = 5.0
    request_market_data: bool = True
    output_dir: Path = Path("data")
    use_contract_cache: bool = True
    force_rebuild_contract_cache: bool = False
    filter_market_data_by_moneyness: bool = True
    near_dte_days: int = 7
    near_strike_width: float = 1.0
    far_strike_width: float = 3.0


@dataclass(frozen=True)
class LiveChainSettings:
    """Persistent subscription settings for a focused near-expiry chain."""

    root: str = "ZF"
    future_months: str = "202609,202612"
    max_dte: int | None = 14
    max_expirations: int | None = 8
    strikes_each_side: int = 12
    strike_width: float | None = 5.0
    qualify_batch_size: int = 250
    request_interval: float = 0.025
    warmup_seconds: float = 4.0
    stable_seconds: float = 1.0
    poll_seconds: float = 1.0
    output_path: Path | None = Path("data/live_zf_chain_latest.csv")
    flow_db_path: Path | None = Path("data/zf_option_flow.sqlite")
    min_volume_delta: float = 1.0


def market_data_type_from_label(label: str | int) -> int:
    """Normalize a market-data label or integer into the IB numeric value."""
    if isinstance(label, int):
        return int(label)
    text = str(label).strip().lower().replace(" ", "_").replace("-", "_")
    if text.isdigit():
        return int(text)
    if text not in MARKET_DATA_TYPES:
        raise ValueError(f"Unknown market data type: {label}")
    return MARKET_DATA_TYPES[text]
