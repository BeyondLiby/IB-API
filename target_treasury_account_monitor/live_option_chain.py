from __future__ import annotations

import math
import re
from typing import Any, Sequence

import pandas as pd
from ib_async import Contract, FuturesOption, IB

from treasury_fop_chain import (
    get_future_prices_for_months,
    qualify_contract_meta,
    qualify_future,
    snapshot_in_batches,
)

try:
    from .option_chain_view import snapshot_to_monitor_frame
    from .utils import is_valid_number
except ImportError:
    from option_chain_view import snapshot_to_monitor_frame
    from utils import is_valid_number


def normalize_months(value: str | Sequence[str]) -> list[str]:
    """Normalize YYYYMM month input."""
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


def dte_from_expiration(expiration: str, *, today: str) -> int | None:
    exp_dt = pd.to_datetime(str(expiration)[:8], format="%Y%m%d", errors="coerce")
    today_dt = pd.to_datetime(str(today)[:8], format="%Y%m%d", errors="coerce")
    if pd.isna(exp_dt) or pd.isna(today_dt):
        return None
    return int((exp_dt - today_dt).days)


def select_expirations(
    expirations: Sequence[str],
    *,
    today: str,
    max_dte: int | None,
    max_expirations: int | None,
) -> list[str]:
    """Keep nearest expirations including 0DTE when available."""
    selected: list[str] = []
    for exp in sorted({str(item) for item in expirations if str(item) >= today}):
        dte = dte_from_expiration(exp, today=today)
        if dte is None:
            continue
        if max_dte is not None and dte > int(max_dte):
            continue
        selected.append(exp)
    if max_expirations is not None:
        selected = selected[: int(max_expirations)]
    return selected


def select_strikes(
    strikes: Sequence[float],
    *,
    spot: float,
    strikes_each_side: int,
    strike_width: float | None,
) -> list[float]:
    """Select strikes near a future price using both width and count caps."""
    clean = sorted({float(strike) for strike in strikes if is_valid_number(strike)})
    if not clean:
        return []
    if not is_valid_number(spot):
        center = clean[len(clean) // 2]
    else:
        center = float(spot)

    ranked = sorted(clean, key=lambda strike: (abs(strike - center), strike))
    if strike_width is not None:
        by_width = [strike for strike in clean if abs(strike - center) <= float(strike_width)]
    else:
        by_width = []
    by_count = ranked[: int(strikes_each_side) * 2 + 1]
    return sorted(set(by_width + by_count))


def discover_near_expiry_fop_contracts(
    ib: IB,
    *,
    root: str = "ZF",
    future_months: str | Sequence[str] = ("202609", "202612"),
    exchange: str = "CBOT",
    fop_exchange: str = "CBOT",
    currency: str = "USD",
    market_data_type: int | None = None,
    today: str | None = None,
    max_dte: int | None = 14,
    max_expirations: int | None = 8,
    strikes_each_side: int = 12,
    strike_width: float | None = 5.0,
    qualify_batch_size: int = 250,
) -> dict[str, Any]:
    """
    Discover qualified near-expiry FOP contracts directly from IB.

    This intentionally does not read local universe caches. It mirrors the debug
    notebook flow, but selects near expirations before qualification so 0DTE is
    easy to verify and the market-data request size stays bounded.
    """
    root = root.upper()
    months = normalize_months(future_months)
    today = str(today or today_yyyymmdd())
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
    spot_by_month = {
        str(row["month"]): float(row["price"])
        for row in future_prices.to_dict("records")
        if is_valid_number(row.get("price", math.nan))
    }

    candidates: list[tuple[Contract, dict[str, Any]]] = []
    summary_rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for month in months:
        underlying = qualify_future(ib, root, month, exchange=exchange, currency=currency)
        chains = ib.reqSecDefOptParams(root, fop_exchange, "FUT", underlying.conId)
        spot = spot_by_month.get(str(month), math.nan)
        print(f"underlying {root}{month}: conId={underlying.conId}, localSymbol={underlying.localSymbol}")

        month_candidate_count = 0
        for chain in chains:
            expirations = sorted(str(exp) for exp in chain.expirations)
            strikes = sorted(float(strike) for strike in chain.strikes)
            selected_expirations = select_expirations(
                expirations,
                today=today,
                max_dte=max_dte,
                max_expirations=max_expirations,
            )
            selected_strikes = select_strikes(
                strikes,
                spot=spot,
                strikes_each_side=strikes_each_side,
                strike_width=strike_width,
            )

            summary_rows.append(
                {
                    "underlyingConId": underlying.conId,
                    "underlyingLocalSymbol": underlying.localSymbol,
                    "underlyingMonth": underlying.lastTradeDateOrContractMonth[:6],
                    "chainExchange": chain.exchange,
                    "chainTradingClass": chain.tradingClass,
                    "chainMultiplier": chain.multiplier,
                    "allExpirationCount": len(expirations),
                    "selectedExpirationCount": len(selected_expirations),
                    "firstSelectedExpiration": selected_expirations[0] if selected_expirations else "",
                    "lastSelectedExpiration": selected_expirations[-1] if selected_expirations else "",
                    "allStrikeCount": len(strikes),
                    "selectedStrikeCount": len(selected_strikes),
                    "underlyingPriceForSelection": spot,
                    "rawCandidateCount": len(selected_expirations) * len(selected_strikes) * 2,
                }
            )

            for expiration in selected_expirations:
                dte = dte_from_expiration(expiration, today=today)
                for strike in selected_strikes:
                    for right in ("C", "P"):
                        key = (
                            underlying.conId,
                            chain.exchange or fop_exchange,
                            chain.tradingClass,
                            chain.multiplier,
                            expiration,
                            strike,
                            right,
                        )
                        if key in seen:
                            continue
                        seen.add(key)
                        contract = FuturesOption(
                            symbol=root,
                            lastTradeDateOrContractMonth=expiration,
                            strike=strike,
                            right=right,
                            exchange=chain.exchange or fop_exchange,
                            multiplier=chain.multiplier or "",
                            currency=currency,
                            tradingClass=chain.tradingClass,
                        )
                        candidates.append(
                            (
                                contract,
                                {
                                    "underlyingConId": underlying.conId,
                                    "underlyingLocalSymbol": underlying.localSymbol,
                                    "underlyingMonth": underlying.lastTradeDateOrContractMonth[:6],
                                    "chainExchange": chain.exchange,
                                    "chainTradingClass": chain.tradingClass,
                                    "chainMultiplier": chain.multiplier,
                                    "dte": dte,
                                    "underlyingPriceForSelection": spot,
                                },
                            )
                        )
                        month_candidate_count += 1

        print(f"  near-expiry candidates from option chains: {month_candidate_count}")

    contracts, metadata = qualify_contract_meta(
        ib,
        candidates,
        batch_size=int(qualify_batch_size),
    )
    if not metadata.empty:
        metadata["expiration"] = metadata["lastTradeDateOrContractMonth"].astype(str)
        metadata["dte"] = metadata["expiration"].map(lambda exp: dte_from_expiration(exp, today=today))
        metadata = metadata.sort_values(
            ["dte", "underlyingMonth", "expiration", "right", "strike", "conId"],
            ignore_index=True,
        )
        by_conid = {int(getattr(contract, "conId", 0) or 0): contract for contract in contracts}
        contracts = [
            by_conid[int(con_id)]
            for con_id in pd.to_numeric(metadata["conId"], errors="coerce").dropna().astype(int).tolist()
            if int(con_id) in by_conid
        ]

    return {
        "root": root,
        "months": months,
        "today": today,
        "future_prices": future_prices,
        "chain_summary": pd.DataFrame(summary_rows),
        "contracts": contracts,
        "metadata": metadata,
        "candidate_count": len(candidates),
        "qualified_count": len(contracts),
    }


def fetch_live_zf_near_expiry_chain(
    ib: IB,
    *,
    root: str = "ZF",
    future_months: str | Sequence[str] = ("202609", "202612"),
    market_data_type: int | None = 3,
    max_dte: int | None = 14,
    max_expirations: int | None = 8,
    strikes_each_side: int = 12,
    strike_width: float | None = 5.0,
    batch_size: int = 100,
    wait_max_seconds: float = 8.0,
    wait_stable_seconds: float = 1.5,
    request_interval: float = 0.025,
    snapshot: bool = True,
) -> dict[str, Any]:
    """Discover near-expiry ZF FOPs and optionally request live/delayed quotes."""
    result = discover_near_expiry_fop_contracts(
        ib,
        root=root,
        future_months=future_months,
        market_data_type=market_data_type,
        max_dte=max_dte,
        max_expirations=max_expirations,
        strikes_each_side=strikes_each_side,
        strike_width=strike_width,
    )

    snapshot_frame = pd.DataFrame()
    monitor_frame = pd.DataFrame()
    if snapshot and result["contracts"]:
        snapshot_frame = snapshot_in_batches(
            ib,
            result["contracts"],
            batch_size=int(batch_size),
            wait_max_seconds=float(wait_max_seconds),
            wait_stable_seconds=float(wait_stable_seconds),
            request_interval=float(request_interval),
        )
        monitor_frame = snapshot_to_monitor_frame(snapshot_frame, root=root)

    return {
        **result,
        "snapshot": snapshot_frame,
        "monitor_frame": monitor_frame,
        "snapshot_count": len(snapshot_frame),
    }
