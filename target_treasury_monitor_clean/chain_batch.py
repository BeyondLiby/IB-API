from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from pandas.errors import EmptyDataError
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
    min_key = str(settings.min_expiration or today or "auto")
    max_key = str(settings.max_expiration or "all")
    return Path(settings.output_dir) / f"{root}_FOP_Static_{month_key}_from_{min_key}_to_{max_key}"


def _contract_cache_path(settings: StaticChainSettings) -> Path:
    """Return the CSV path containing already-qualified valid contracts."""
    prefix = _cache_prefix(settings)
    return prefix.with_name(prefix.name + "_contracts.csv")


def _find_existing_contract_cache(settings: StaticChainSettings, preferred: Path) -> Path:
    """Reuse a compatible full universe cache, never a filtered selection cache.

    ``*_selected_contracts.csv`` also matches the old glob.  Treating that
    filtered file as the qualified universe permanently shrinks later refreshes
    (and therefore removes expirations and strikes from the planner).  Prefer a
    cache with a non-empty chain-summary sidecar; a rebuilt full cache always
    writes that sidecar, while a poisoned/partial cache generally does not.
    """
    root = settings.root.upper()
    requested_months = set(normalize_months(settings.future_months))
    output_dir = Path(settings.output_dir)
    candidates = {
        path
        for path in output_dir.glob(f"{root}_FOP_Static_*_from_*_to_*_contracts.csv")
        if not path.name.endswith("_selected_contracts.csv")
        and requested_months.issubset(set(_cache_months(path, root)))
    }
    if preferred.exists():
        candidates.add(preferred)
    if not candidates:
        return preferred

    def cache_score(path: Path) -> tuple[int, int, int, float, str]:
        summary_path = _sidecar_path(path, "_chain_summary.csv")
        has_summary = int(summary_path.exists() and summary_path.stat().st_size > 4)
        cached_months = set(_cache_months(path, root))
        exact_months = int(cached_months == requested_months)
        return has_summary, exact_months, -len(cached_months - requested_months), path.stat().st_mtime, path.name

    return max(candidates, key=cache_score)


def _cache_months(path: Path, root: str) -> tuple[str, ...]:
    prefix = f"{root.upper()}_FOP_Static_"
    name = path.name
    if not name.startswith(prefix) or "_from_" not in name:
        return ()
    month_key = name[len(prefix):].split("_from_", 1)[0]
    return tuple(part for part in month_key.split("_") if len(part) == 6 and part.isdigit())


def _sidecar_path(contract_cache_path: Path, suffix: str) -> Path:
    """Return a sidecar path next to the contract cache."""
    name = contract_cache_path.name
    if name.endswith("_contracts.csv"):
        name = name[: -len("_contracts.csv")]
    return contract_cache_path.with_name(name + suffix)


def _read_csv_or_empty(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path)
    except (OSError, EmptyDataError):
        return pd.DataFrame()


def _fresh_or_cached_future_prices(
    ib: IB,
    settings: StaticChainSettings,
    months: list[str],
    future_prices_path: Path | None = None,
) -> tuple[pd.DataFrame, str, str]:
    """Prefer fresh future prices for filtering, falling back to cached sidecars."""
    root = settings.root.upper()
    error = ""
    if ib is not None:
        try:
            prices = get_future_prices_for_months(
                ib,
                root,
                months,
                market_data_type=None,
                wait_seconds=float(settings.future_price_wait_seconds),
                raise_on_missing=False,
            )
            if not prices.empty:
                prices = prices.copy()
                if root == "ZC" and "price" in prices.columns:
                    price = pd.to_numeric(prices["price"], errors="coerce")
                    prices["price"] = price.where(price.abs() >= 20, price * 100)
                valid = pd.to_numeric(prices.get("price"), errors="coerce").notna().sum() if "price" in prices.columns else 0
                if valid:
                    return _limit_future_prices_to_months(prices, months), "fresh", ""
                error = "fresh future price request returned no valid price"
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
    if future_prices_path is not None and future_prices_path.exists():
        prices = _read_csv_or_empty(future_prices_path)
        if root == "ZC" and "price" in prices.columns:
            price = pd.to_numeric(prices["price"], errors="coerce")
            prices["price"] = price.where(price.abs() >= 20, price * 100)
        if not prices.empty:
            return _limit_future_prices_to_months(prices, months), "cache", error
    return pd.DataFrame(), "missing", error


def _limit_future_prices_to_months(prices: pd.DataFrame, months: list[str]) -> pd.DataFrame:
    if prices.empty or "month" not in prices.columns:
        return prices.copy()
    allowed = set(str(month) for month in months)
    return prices[prices["month"].astype(str).isin(allowed)].copy().reset_index(drop=True)


def refresh_future_prices_sidecar(
    ib: IB,
    settings: StaticChainSettings,
) -> tuple[pd.DataFrame, str, str, Path]:
    """Refresh and save the futures price sidecar independently of option-chain refresh."""
    preferred_contract_cache_path = _contract_cache_path(settings)
    compatible_contract_cache_path = _find_existing_contract_cache(settings, preferred_contract_cache_path)
    future_prices_path = _sidecar_path(preferred_contract_cache_path, "_future_prices.csv")
    compatible_future_prices_path = _sidecar_path(compatible_contract_cache_path, "_future_prices.csv")
    cache_path = future_prices_path if future_prices_path.exists() else compatible_future_prices_path
    future_prices, source, error = _fresh_or_cached_future_prices(
        ib,
        settings,
        normalize_months(settings.future_months),
        cache_path,
    )
    if source == "fresh" and not future_prices.empty:
        future_prices_path.parent.mkdir(parents=True, exist_ok=True)
        future_prices.to_csv(future_prices_path, index=False, encoding="utf-8-sig")
    return future_prices, source, error, future_prices_path


def _filter_cached_universe_to_months(
    contracts: list[Any],
    metadata: pd.DataFrame,
    chain_summary: pd.DataFrame,
    settings: StaticChainSettings,
) -> tuple[list[Any], pd.DataFrame, pd.DataFrame]:
    """Reuse a broader cache while exposing only selected futures months.

    Current-position conIds remain included even if they fall outside the
    selected months, so a month choice can never hide a live held contract.
    """
    if metadata.empty or "underlyingMonth" not in metadata.columns:
        return contracts, metadata.copy(), chain_summary.copy()

    requested_months = set(normalize_months(settings.future_months))
    force_con_ids = {int(value) for value in settings.force_con_ids if int(value) > 0}
    out = metadata.copy()
    con_ids = pd.to_numeric(out.get("conId"), errors="coerce").astype("Int64")
    month_mask = out["underlyingMonth"].astype(str).isin(requested_months)
    force_mask = con_ids.isin(force_con_ids) if force_con_ids else False
    out = out[month_mask | force_mask].copy().reset_index(drop=True)
    kept_con_ids = set(pd.to_numeric(out.get("conId"), errors="coerce").dropna().astype(int).tolist())
    filtered_contracts = [
        contract for contract in contracts
        if int(getattr(contract, "conId", 0) or 0) in kept_con_ids
    ]

    filtered_summary = chain_summary.copy()
    if not filtered_summary.empty and "underlyingMonth" in filtered_summary.columns:
        filtered_summary = filtered_summary[
            filtered_summary["underlyingMonth"].astype(str).isin(requested_months)
        ].copy().reset_index(drop=True)
    return filtered_contracts, out, filtered_summary


def _dte(expiration: object) -> int | None:
    """Compute DTE using today's US/Eastern trade date."""
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
    if settings.root.upper() == "ZC":
        out["strike"] = out["strike"].where(out["strike"].abs() >= 20, out["strike"] * 100)
    if "expiration" in out.columns:
        out["expiration"] = out["expiration"].astype(str)
    else:
        out["expiration"] = out["lastTradeDateOrContractMonth"].astype(str)
    out["dteForFilter"] = out["expiration"].map(_dte)
    out["underlyingMonth"] = out.get("underlyingMonth", "").astype(str)

    price_by_month: dict[str, float] = {}
    if not future_prices.empty and {"month", "price"}.issubset(future_prices.columns):
        for row in future_prices.to_dict("records"):
            price = pd.to_numeric(row.get("price"), errors="coerce")
            if pd.notna(price):
                price_value = float(price)
                if settings.root.upper() == "ZC" and abs(price_value) < 20:
                    price_value *= 100
                price_by_month[str(row.get("month"))] = price_value

    out["underlyingPriceForFilter"] = out["underlyingMonth"].map(price_by_month)
    if "underlyingPrice" in out.columns:
        out["underlyingPriceForFilter"] = pd.to_numeric(
            out["underlyingPriceForFilter"], errors="coerce"
        ).fillna(pd.to_numeric(out["underlyingPrice"], errors="coerce"))

    out["strikeDistanceForFilter"] = (out["strike"] - out["underlyingPriceForFilter"]).abs()
    dte_numeric = pd.to_numeric(out["dteForFilter"], errors="coerce")
    out["filterWidth"] = settings.far_strike_width
    out.loc[dte_numeric <= int(settings.near_dte_days), "filterWidth"] = settings.near_strike_width

    base_mask = (
        out["underlyingPriceForFilter"].notna()
        & out["strikeDistanceForFilter"].notna()
        & (out["strikeDistanceForFilter"] <= out["filterWidth"])
        & dte_numeric.notna()
        & (dte_numeric >= 0)
    )
    if settings.market_data_max_dte is not None:
        base_mask = base_mask & (dte_numeric <= int(settings.market_data_max_dte))

    force_con_ids = {int(value) for value in settings.force_con_ids if int(value) > 0}
    force_mask = out["conId"].astype("Int64").isin(force_con_ids) if force_con_ids else False
    selected = out[base_mask | force_mask].copy()
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


def _load_cached_chain(
    ib: IB,
    settings: StaticChainSettings,
    contract_cache_path: Path,
    *,
    future_prices: pd.DataFrame | None = None,
    future_price_source: str | None = None,
    future_price_error: str = "",
) -> dict[str, Any]:
    """Load valid contracts from cache and optionally refresh only market data."""
    contracts, metadata = load_universe(contract_cache_path)
    months = normalize_months(settings.future_months)
    root = settings.root.upper()

    chain_summary_path = _sidecar_path(contract_cache_path, "_chain_summary.csv")
    future_prices_path = _sidecar_path(contract_cache_path, "_future_prices.csv")
    chain_summary = _read_csv_or_empty(chain_summary_path) if chain_summary_path.exists() else pd.DataFrame()
    contracts, metadata, chain_summary = _filter_cached_universe_to_months(
        contracts,
        metadata,
        chain_summary,
        settings,
    )
    if future_prices is None:
        future_prices, future_price_source, future_price_error = _fresh_or_cached_future_prices(
            ib,
            settings,
            months,
            future_prices_path,
        )
    else:
        future_prices = _limit_future_prices_to_months(future_prices, months)
        future_price_source = future_price_source or "provided"
    selected_contracts, selected_metadata = _select_market_data_contracts(
        contracts,
        metadata,
        future_prices,
        settings,
    )

    snapshot = pd.DataFrame()
    monitor_frame = pd.DataFrame()
    if settings.request_market_data and selected_contracts:
        print(f"{root} option quotes: selected={len(selected_contracts)}, months={','.join(months)}", flush=True)
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
        "min_expiration": settings.min_expiration or "auto",
        "max_expiration": settings.max_expiration or "",
        "future_prices": future_prices,
        "future_price_source": future_price_source,
        "future_price_error": future_price_error,
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


def refresh_static_chain(
    ib: IB,
    settings: StaticChainSettings,
    *,
    future_prices: pd.DataFrame | None = None,
    future_price_source: str | None = None,
    future_price_error: str = "",
) -> StaticChainResult:
    """Refresh a static FOP chain, reusing qualified contracts when available."""
    preferred_contract_cache_path = _contract_cache_path(settings)
    contract_cache_path = _find_existing_contract_cache(settings, preferred_contract_cache_path)
    if (
        settings.use_contract_cache
        and not settings.force_rebuild_contract_cache
        and contract_cache_path.exists()
    ):
        result = _load_cached_chain(
            ib,
            settings,
            contract_cache_path,
            future_prices=future_prices,
            future_price_source=future_price_source,
            future_price_error=future_price_error,
        )
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
        future_prices_path = _sidecar_path(preferred_contract_cache_path, "_future_prices.csv")
        if future_prices is None:
            future_prices, future_price_source, future_price_error = _fresh_or_cached_future_prices(
                ib,
                settings,
                normalize_months(settings.future_months),
                future_prices_path,
            )
        else:
            future_prices = _limit_future_prices_to_months(
                future_prices,
                normalize_months(settings.future_months),
            )
            future_price_source = future_price_source or "provided"
        if not future_prices.empty:
            result["future_prices"] = future_prices
        result["future_price_source"] = future_price_source
        result["future_price_error"] = future_price_error
        selected_contracts, selected_metadata = _select_market_data_contracts(
            result["contracts"],
            result["metadata"],
            result["future_prices"],
            settings,
        )
        snapshot = pd.DataFrame()
        monitor_frame = pd.DataFrame()
        if settings.request_market_data and selected_contracts:
            print(
                f"{settings.root.upper()} option quotes: selected={len(selected_contracts)}, "
                f"months={','.join(normalize_months(settings.future_months))}",
                flush=True,
            )
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
        result["contract_cache_path"] = str(preferred_contract_cache_path)
        if not settings.min_expiration:
            result["min_expiration"] = "auto"
    paths = save_static_chain_result(result, settings.output_dir)
    return StaticChainResult(raw=result, saved_paths=paths)
