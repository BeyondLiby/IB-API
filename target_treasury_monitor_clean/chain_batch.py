from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from ib_async import IB

from target_treasury_account_monitor.option_chain_view import snapshot_to_monitor_frame
from target_treasury_account_monitor.static_option_chain import normalize_months, today_yyyymmdd
from target_treasury_account_monitor.static_option_chain import (
    fetch_static_fop_chain_snapshot,
    save_static_chain_result,
)
from treasury_fop_chain import get_future_prices_for_months, load_universe, snapshot_in_batches

from .settings import StaticChainSettings


@dataclass
class StaticChainResult:
    """Normalized result for a one-shot chain refresh."""

    raw: dict[str, Any]
    saved_paths: dict[str, Path]

    @property
    def metadata(self) -> pd.DataFrame:
        return self.raw.get("metadata", pd.DataFrame())

    @property
    def snapshot(self) -> pd.DataFrame:
        return self.raw.get("snapshot", pd.DataFrame())

    @property
    def monitor_frame(self) -> pd.DataFrame:
        return self.raw.get("monitor_frame", pd.DataFrame())


def _cache_prefix(settings: StaticChainSettings, *, today: str | None = None) -> Path:
    """Return the stable file prefix for the qualified contract universe."""
    root = settings.root.upper()
    months = normalize_months(settings.future_months)
    month_key = "_".join(months)
    min_key = str(settings.min_expiration or today or today_yyyymmdd())
    max_key = str(settings.max_expiration or "all")
    return Path(settings.output_dir) / f"{root}_FOP_Static_{month_key}_from_{min_key}_to_{max_key}"


def _contract_cache_path(settings: StaticChainSettings) -> Path:
    """Return the CSV path containing already-qualified valid contracts."""
    prefix = _cache_prefix(settings)
    return prefix.with_name(prefix.name + "_contracts.csv")


def _sidecar_path(contract_cache_path: Path, suffix: str) -> Path:
    """Return a sidecar path next to the contract cache."""
    name = contract_cache_path.name
    if name.endswith("_contracts.csv"):
        name = name[: -len("_contracts.csv")]
    return contract_cache_path.with_name(name + suffix)


def _fresh_or_cached_future_prices(
    ib: IB,
    settings: StaticChainSettings,
    months: list[str],
    future_prices_path: Path | None = None,
) -> pd.DataFrame:
    """Prefer fresh future prices for filtering, falling back to cached sidecars."""
    root = settings.root.upper()
    if ib is not None:
        try:
            prices = get_future_prices_for_months(
                ib,
                root,
                months,
                market_data_type=None,
                wait_seconds=2.0,
                raise_on_missing=False,
            )
            if not prices.empty:
                return prices
        except Exception:
            pass
    if future_prices_path is not None and future_prices_path.exists():
        return pd.read_csv(future_prices_path)
    return pd.DataFrame()


def _dte(expiration: object) -> int | None:
    """Compute DTE using today's Asia/Shanghai date."""
    exp_dt = pd.to_datetime(str(expiration)[:8], format="%Y%m%d", errors="coerce")
    today_dt = pd.to_datetime(today_yyyymmdd(), format="%Y%m%d", errors="coerce")
    if pd.isna(exp_dt) or pd.isna(today_dt):
        return None
    return int((exp_dt - today_dt).days)


def _select_market_data_contracts(
    contracts: list[Any],
    metadata: pd.DataFrame,
    future_prices: pd.DataFrame,
    settings: StaticChainSettings,
) -> tuple[list[Any], pd.DataFrame]:
    """Filter contracts before market-data subscription by DTE-aware moneyness."""
    if not settings.filter_market_data_by_moneyness or metadata.empty:
        return contracts, metadata.copy()

    out = metadata.copy()
    out["conId"] = pd.to_numeric(out["conId"], errors="coerce").astype("Int64")
    out["strike"] = pd.to_numeric(out["strike"], errors="coerce")
    out["expiration"] = out.get("expiration", out["lastTradeDateOrContractMonth"]).astype(str)
    out["dteForFilter"] = out["expiration"].map(_dte)
    out["underlyingMonth"] = out.get("underlyingMonth", "").astype(str)

    price_by_month: dict[str, float] = {}
    if not future_prices.empty and {"month", "price"}.issubset(future_prices.columns):
        for row in future_prices.to_dict("records"):
            price = pd.to_numeric(row.get("price"), errors="coerce")
            if pd.notna(price):
                price_by_month[str(row.get("month"))] = float(price)

    out["underlyingPriceForFilter"] = out["underlyingMonth"].map(price_by_month)
    if "underlyingPrice" in out.columns:
        out["underlyingPriceForFilter"] = pd.to_numeric(
            out["underlyingPriceForFilter"], errors="coerce"
        ).fillna(pd.to_numeric(out["underlyingPrice"], errors="coerce"))

    out["strikeDistanceForFilter"] = (out["strike"] - out["underlyingPriceForFilter"]).abs()
    dte_numeric = pd.to_numeric(out["dteForFilter"], errors="coerce")
    out["filterWidth"] = settings.far_strike_width
    out.loc[dte_numeric <= int(settings.near_dte_days), "filterWidth"] = settings.near_strike_width

    selected = out[
        out["underlyingPriceForFilter"].notna()
        & out["strikeDistanceForFilter"].notna()
        & (out["strikeDistanceForFilter"] <= out["filterWidth"])
    ].copy()
    selected = selected.sort_values(
        ["dteForFilter", "underlyingMonth", "expiration", "right", "strike", "conId"],
        ignore_index=True,
    )

    by_conid = {int(getattr(contract, "conId", 0) or 0): contract for contract in contracts}
    selected_contracts = [
        by_conid[int(con_id)]
        for con_id in selected["conId"].dropna().astype(int).tolist()
        if int(con_id) in by_conid
    ]
    return selected_contracts, selected


def _load_cached_chain(ib: IB, settings: StaticChainSettings, contract_cache_path: Path) -> dict[str, Any]:
    """Load valid contracts from cache and optionally refresh only market data."""
    contracts, metadata = load_universe(contract_cache_path)
    months = normalize_months(settings.future_months)
    root = settings.root.upper()

    chain_summary_path = _sidecar_path(contract_cache_path, "_chain_summary.csv")
    future_prices_path = _sidecar_path(contract_cache_path, "_future_prices.csv")
    chain_summary = pd.read_csv(chain_summary_path) if chain_summary_path.exists() else pd.DataFrame()
    future_prices = _fresh_or_cached_future_prices(ib, settings, months, future_prices_path)
    selected_contracts, selected_metadata = _select_market_data_contracts(
        contracts,
        metadata,
        future_prices,
        settings,
    )

    snapshot = pd.DataFrame()
    monitor_frame = pd.DataFrame()
    if settings.request_market_data and selected_contracts:
        snapshot = snapshot_in_batches(
            ib,
            selected_contracts,
            batch_size=int(settings.batch_size),
            wait_max_seconds=float(settings.wait_max_seconds),
            wait_stable_seconds=float(settings.wait_stable_seconds),
            request_interval=float(settings.request_interval),
            inter_batch_pause_seconds=float(settings.inter_batch_pause_seconds),
            empty_batch_retries=int(settings.empty_batch_retries),
            empty_batch_retry_pause_seconds=float(settings.empty_batch_retry_pause_seconds),
        )
        monitor_frame = snapshot_to_monitor_frame(snapshot, root=root)

    return {
        "root": root,
        "months": months,
        "today": today_yyyymmdd(),
        "min_expiration": settings.min_expiration or today_yyyymmdd(),
        "max_expiration": settings.max_expiration or "",
        "future_prices": future_prices,
        "chain_summary": chain_summary,
        "contracts": contracts,
        "metadata": metadata,
        "selected_metadata": selected_metadata,
        "contract_count": len(contracts),
        "selected_contract_count": len(selected_contracts),
        "snapshot_scope": "dte_moneyness_filter" if settings.filter_market_data_by_moneyness else "full_chain",
        "market_data_filter": {
            "enabled": settings.filter_market_data_by_moneyness,
            "near_dte_days": settings.near_dte_days,
            "near_strike_width": settings.near_strike_width,
            "far_strike_width": settings.far_strike_width,
        },
        "universe_source": "contract_cache",
        "contract_cache_path": str(contract_cache_path),
        "snapshot": snapshot,
        "monitor_frame": monitor_frame,
        "snapshot_count": len(snapshot) if not snapshot.empty else 0,
    }


def refresh_static_chain(ib: IB, settings: StaticChainSettings) -> StaticChainResult:
    """Refresh a static FOP chain, reusing qualified contracts when available."""
    contract_cache_path = _contract_cache_path(settings)
    if (
        settings.use_contract_cache
        and not settings.force_rebuild_contract_cache
        and contract_cache_path.exists()
    ):
        result = _load_cached_chain(ib, settings, contract_cache_path)
    else:
        result = fetch_static_fop_chain_snapshot(
            ib,
            root=settings.root,
            future_months=settings.future_months,
            market_data_type=None,
            min_expiration=settings.min_expiration,
            max_expiration=settings.max_expiration,
            qualify_batch_size=settings.qualify_batch_size,
            batch_size=settings.batch_size,
            wait_max_seconds=settings.wait_max_seconds,
            wait_stable_seconds=settings.wait_stable_seconds,
            request_interval=settings.request_interval,
            inter_batch_pause_seconds=settings.inter_batch_pause_seconds,
            empty_batch_retries=settings.empty_batch_retries,
            empty_batch_retry_pause_seconds=settings.empty_batch_retry_pause_seconds,
            request_market_data=False,
        )
        selected_contracts, selected_metadata = _select_market_data_contracts(
            result["contracts"],
            result["metadata"],
            result["future_prices"],
            settings,
        )
        snapshot = pd.DataFrame()
        monitor_frame = pd.DataFrame()
        if settings.request_market_data and selected_contracts:
            snapshot = snapshot_in_batches(
                ib,
                selected_contracts,
                batch_size=int(settings.batch_size),
                wait_max_seconds=float(settings.wait_max_seconds),
                wait_stable_seconds=float(settings.wait_stable_seconds),
                request_interval=float(settings.request_interval),
                inter_batch_pause_seconds=float(settings.inter_batch_pause_seconds),
                empty_batch_retries=int(settings.empty_batch_retries),
                empty_batch_retry_pause_seconds=float(settings.empty_batch_retry_pause_seconds),
            )
            monitor_frame = snapshot_to_monitor_frame(snapshot, root=settings.root)
        result["selected_metadata"] = selected_metadata
        result["selected_contract_count"] = len(selected_contracts)
        result["snapshot_scope"] = "dte_moneyness_filter" if settings.filter_market_data_by_moneyness else "full_chain"
        result["market_data_filter"] = {
            "enabled": settings.filter_market_data_by_moneyness,
            "near_dte_days": settings.near_dte_days,
            "near_strike_width": settings.near_strike_width,
            "far_strike_width": settings.far_strike_width,
        }
        result["snapshot"] = snapshot
        result["monitor_frame"] = monitor_frame
        result["snapshot_count"] = len(snapshot)
        result["universe_source"] = "ib_rebuilt"
        result["contract_cache_path"] = str(_contract_cache_path(settings))
    paths = save_static_chain_result(result, settings.output_dir)
    return StaticChainResult(raw=result, saved_paths=paths)
