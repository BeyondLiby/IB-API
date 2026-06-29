from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Sequence

import pandas as pd
from ib_async import IB

from treasury_fop_chain import (
    UniverseResult,
    build_treasury_fop_universe,
    get_future_prices_for_months,
    snapshot_in_batches,
)

try:
    from .option_chain_view import snapshot_to_monitor_frame
    from .utils import is_valid_number
except ImportError:
    from option_chain_view import snapshot_to_monitor_frame
    from utils import is_valid_number


def normalize_months(value: str | Sequence[str]) -> list[str]:
    """Normalize YYYYMM future-month input."""
    if isinstance(value, str):
        parts = re.split(r"[\s,;]+", value.strip())
    else:
        parts = [str(item).strip() for item in value]
    months = [part for part in parts if re.fullmatch(r"\d{6}", part)]
    if not months:
        raise ValueError("At least one YYYYMM future month is required.")
    return months


def today_yyyymmdd(timezone: str = "Asia/Shanghai") -> str:
    return pd.Timestamp.now(tz=timezone).strftime("%Y%m%d")


def dte_from_expiration(expiration: Any, *, today: str) -> int | None:
    exp_dt = pd.to_datetime(str(expiration)[:8], format="%Y%m%d", errors="coerce")
    today_dt = pd.to_datetime(str(today)[:8], format="%Y%m%d", errors="coerce")
    if pd.isna(exp_dt) or pd.isna(today_dt):
        return None
    return int((exp_dt - today_dt).days)


def enrich_static_metadata(
    metadata: pd.DataFrame,
    *,
    today: str,
    future_prices: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Add expiration/DTE and underlying-price audit columns to qualified contracts."""
    if metadata.empty:
        return metadata.copy()

    out = metadata.copy()
    out["underlyingMonth"] = out.get("underlyingMonth", "").astype(str)
    out["expiration"] = out["lastTradeDateOrContractMonth"].astype(str)
    out["dte"] = out["expiration"].map(lambda exp: dte_from_expiration(exp, today=today))
    out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
    out["conId"] = pd.to_numeric(out["conId"], errors="coerce").astype("Int64")

    if future_prices is not None and not future_prices.empty:
        price_by_month = {
            str(row["month"]): float(row["price"])
            for row in future_prices.to_dict("records")
            if is_valid_number(row.get("price", math.nan))
        }
        out["underlyingPrice"] = out["underlyingMonth"].map(price_by_month)
        out["strikeDistance"] = (out["strike"] - pd.to_numeric(out["underlyingPrice"], errors="coerce")).abs()
    else:
        out["underlyingPrice"] = math.nan
        out["strikeDistance"] = math.nan

    return out.sort_values(
        ["dte", "underlyingMonth", "expiration", "right", "strike", "conId"],
        ignore_index=True,
    )


def discover_static_fop_universe(
    ib: IB,
    *,
    root: str = "ZF",
    future_months: str | Sequence[str] = ("202609", "202612"),
    exchange: str = "CBOT",
    fop_exchange: str = "CBOT",
    currency: str = "USD",
    market_data_type: int | None = None,
    min_expiration: str | None = None,
    max_expiration: str | None = None,
    qualify_batch_size: int = 300,
) -> dict[str, Any]:
    """
    Build a fresh qualified futures-option universe from IB security definitions.

    This is static contract-chain data. It deliberately does not load or write
    cache files, and it does not subscribe to market data.
    """
    root = root.upper()
    months = normalize_months(future_months)
    today = today_yyyymmdd()
    effective_min_expiration = str(min_expiration or today)
    if market_data_type is not None:
        ib.reqMarketDataType(int(market_data_type))

    future_prices = get_future_prices_for_months(
        ib,
        root,
        months,
        exchange=exchange,
        currency=currency,
        market_data_type=market_data_type,
        wait_seconds=2.0,
        raise_on_missing=False,
    )

    universe: UniverseResult = build_treasury_fop_universe(
        ib,
        root=root,
        future_months=months,
        exchange=exchange,
        fop_exchange=fop_exchange,
        currency=currency,
        min_expiration=effective_min_expiration,
        max_expiration=max_expiration,
        qualify_batch_size=int(qualify_batch_size),
    )
    metadata = enrich_static_metadata(
        universe.metadata,
        today=today,
        future_prices=future_prices,
    )

    by_conid = {int(getattr(contract, "conId", 0) or 0): contract for contract in universe.contracts}
    sorted_contracts = [
        by_conid[int(con_id)]
        for con_id in metadata["conId"].dropna().astype(int).tolist()
        if int(con_id) in by_conid
    ]

    return {
        "root": root,
        "months": months,
        "today": today,
        "min_expiration": effective_min_expiration,
        "max_expiration": max_expiration or "",
        "future_prices": future_prices,
        "chain_summary": universe.chain_summary,
        "contracts": sorted_contracts,
        "metadata": metadata,
        "contract_count": len(sorted_contracts),
        "universe_source": "ib_static_no_cache",
    }


def fetch_static_fop_chain_snapshot(
    ib: IB,
    *,
    root: str = "ZF",
    future_months: str | Sequence[str] = ("202609", "202612"),
    exchange: str = "CBOT",
    fop_exchange: str = "CBOT",
    currency: str = "USD",
    market_data_type: int | None = 3,
    min_expiration: str | None = None,
    max_expiration: str | None = None,
    qualify_batch_size: int = 300,
    batch_size: int = 150,
    wait_max_seconds: float = 10.0,
    wait_stable_seconds: float = 2.0,
    request_interval: float = 0.025,
    request_market_data: bool = True,
) -> dict[str, Any]:
    """
    Fetch a one-shot static chain snapshot.

    The market-data phase uses temporary subscriptions per batch to collect all
    IB ticker fields once, then cancels them. It is not a live refresher.
    """
    result = discover_static_fop_universe(
        ib,
        root=root,
        future_months=future_months,
        exchange=exchange,
        fop_exchange=fop_exchange,
        currency=currency,
        market_data_type=market_data_type,
        min_expiration=min_expiration,
        max_expiration=max_expiration,
        qualify_batch_size=qualify_batch_size,
    )

    snapshot = pd.DataFrame()
    monitor_frame = pd.DataFrame()
    if request_market_data and result["contracts"]:
        snapshot = snapshot_in_batches(
            ib,
            result["contracts"],
            batch_size=int(batch_size),
            wait_max_seconds=float(wait_max_seconds),
            wait_stable_seconds=float(wait_stable_seconds),
            request_interval=float(request_interval),
        )
        monitor_frame = snapshot_to_monitor_frame(snapshot, root=root)

    return {
        **result,
        "snapshot": snapshot,
        "monitor_frame": monitor_frame,
        "snapshot_count": len(snapshot),
    }


def save_static_chain_result(result: dict[str, Any], output_dir: str | Path = "data") -> dict[str, Path]:
    """Save static contracts, chain summary, future prices, and optional ticker snapshot."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    root = str(result.get("root", "ZF")).upper()
    month_key = "_".join(str(month) for month in result.get("months", [])) or "months"
    min_key = str(result.get("min_expiration", "") or "start")
    max_key = str(result.get("max_expiration", "") or "all")
    prefix = output_dir / f"{root}_FOP_Static_{month_key}_from_{min_key}_to_{max_key}"

    paths = {
        "metadata": prefix.with_name(prefix.name + "_contracts.csv"),
        "chain_summary": prefix.with_name(prefix.name + "_chain_summary.csv"),
        "future_prices": prefix.with_name(prefix.name + "_future_prices.csv"),
    }
    result["metadata"].to_csv(paths["metadata"], index=False, encoding="utf-8-sig")
    result["chain_summary"].to_csv(paths["chain_summary"], index=False, encoding="utf-8-sig")
    result["future_prices"].to_csv(paths["future_prices"], index=False, encoding="utf-8-sig")

    snapshot = result.get("snapshot", pd.DataFrame())
    if isinstance(snapshot, pd.DataFrame) and not snapshot.empty:
        paths["snapshot"] = prefix.with_name(prefix.name + "_snapshot.csv")
        snapshot.to_csv(paths["snapshot"], index=False, encoding="utf-8-sig")

    monitor_frame = result.get("monitor_frame", pd.DataFrame())
    if isinstance(monitor_frame, pd.DataFrame) and not monitor_frame.empty:
        paths["monitor_frame"] = prefix.with_name(prefix.name + "_monitor_frame.csv")
        monitor_frame.to_csv(paths["monitor_frame"], index=False, encoding="utf-8-sig")

    return paths
