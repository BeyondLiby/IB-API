from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


ANALYTICS_FILENAME = "option_analytics_history.csv"
ANALYTICS_COLUMNS = [
    "snapshotDate",
    "snapshotTimeUtc",
    "conId",
    "symbol",
    "expiration",
    "dte",
    "right",
    "strike",
    "underlyingPrice",
    "moneynessPct",
    "iv",
    "delta",
    "volume",
    "openInterest",
    "bid",
    "ask",
    "analyticsSample",
    "liquidityTicksRequested",
]


def _numeric_column(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    result = pd.Series(float("nan"), index=frame.index, dtype="float64")
    for name in names:
        if name in frame.columns:
            result = result.fillna(pd.to_numeric(frame[name], errors="coerce"))
    return result


def _text_column(frame: pd.DataFrame, names: tuple[str, ...]) -> pd.Series:
    result = pd.Series("", index=frame.index, dtype="object")
    for name in names:
        if name in frame.columns:
            values = frame[name].fillna("").astype(str).str.strip()
            result = result.mask(result.eq(""), values)
    return result


def prepare_option_analytics_snapshot(
    chain: pd.DataFrame,
    *,
    snapshot_date: str | date | None = None,
) -> pd.DataFrame:
    """Normalize the bounded daily IV sample without inventing OI/volume zeros."""
    if chain.empty:
        return pd.DataFrame(columns=ANALYTICS_COLUMNS)

    frame = chain.copy()
    if "analyticsSample" in frame.columns:
        sample_flag = frame["analyticsSample"].astype(str).str.lower().isin({"true", "1", "yes"})
        if sample_flag.any():
            frame = frame[sample_flag].copy()

    out = pd.DataFrame(index=frame.index)
    out["snapshotTimeUtc"] = _text_column(frame, ("snapshotTimeUtc", "snapshotTime", "updatedAt"))
    timestamps = pd.to_datetime(out["snapshotTimeUtc"], errors="coerce", utc=True)
    default_date = str(snapshot_date or date.today())
    out["snapshotDate"] = timestamps.dt.strftime("%Y-%m-%d").fillna(default_date)
    out["conId"] = _numeric_column(frame, ("conId",)).astype("Int64")
    out["symbol"] = _text_column(frame, ("symbol", "underlying", "root", "product")).str.upper()
    out["expiration"] = _text_column(frame, ("expiration", "expiry", "lastTradeDateOrContractMonth"))
    out["dte"] = _numeric_column(frame, ("dte",))
    out["right"] = _text_column(frame, ("right", "direction")).str.upper().str[:1]
    out["strike"] = _numeric_column(frame, ("strike",))
    out["underlyingPrice"] = _numeric_column(
        frame,
        ("undPrice", "underlyingPrice", "modelGreeks_undPrice", "bidGreeks_undPrice", "askGreeks_undPrice"),
    )
    zc_mask = out["symbol"].eq("ZC")
    out.loc[zc_mask & out["strike"].abs().lt(20), "strike"] *= 100.0
    out.loc[zc_mask & out["underlyingPrice"].abs().lt(20), "underlyingPrice"] *= 100.0
    out["iv"] = _numeric_column(
        frame,
        ("modelGreeks_impliedVol", "iv", "impliedVolatility", "bidGreeks_impliedVol", "askGreeks_impliedVol"),
    )
    out["delta"] = _numeric_column(frame, ("modelGreeks_delta", "delta"))
    # With the planner's standard FOP request, ib_async may expose a default
    # zero even when no volume/OI tick arrived.  Only positive observations are
    # treated as covered; otherwise the dashboard would turn missing data into
    # a misleading wall of zeros.
    out["volume"] = _numeric_column(frame, ("volume", "rawVolume", "callVolume", "putVolume"))
    out["openInterest"] = _numeric_column(
        frame,
        ("openInterest", "rawOpenInterest", "callOpenInterest", "putOpenInterest"),
    )
    out["volume"] = out["volume"].where(out["volume"] > 0)
    out["openInterest"] = out["openInterest"].where(out["openInterest"] > 0)
    out["bid"] = _numeric_column(frame, ("bid",))
    out["ask"] = _numeric_column(frame, ("ask",))
    out["analyticsSample"] = True
    if "liquidityTicksRequested" in frame.columns:
        out["liquidityTicksRequested"] = (
            frame["liquidityTicksRequested"]
            .astype(str)
            .str.lower()
            .isin({"true", "1", "yes"})
        )
    else:
        out["liquidityTicksRequested"] = False

    valid_underlying = out["underlyingPrice"].where(out["underlyingPrice"].abs() > 1e-12)
    out["moneynessPct"] = (out["strike"] / valid_underlying - 1.0) * 100.0
    out = out[
        out["symbol"].ne("")
        & out["expiration"].ne("")
        & out["right"].isin(["C", "P"])
        & out["strike"].notna()
    ].copy()
    out = out.sort_values(
        ["snapshotDate", "symbol", "dte", "expiration", "right", "strike", "conId"],
        ignore_index=True,
    )
    return out.reindex(columns=ANALYTICS_COLUMNS)


def update_option_analytics_history(
    chain: pd.DataFrame,
    path: str | Path,
    *,
    snapshot_date: str | date | None = None,
    retention_days: int = 30,
) -> Path:
    """Upsert one daily bounded sample and retain a compact rolling history."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    latest = prepare_option_analytics_snapshot(chain, snapshot_date=snapshot_date)
    try:
        previous = pd.read_csv(destination) if destination.exists() else pd.DataFrame()
    except (pd.errors.EmptyDataError, OSError):
        previous = pd.DataFrame()
    combined = pd.concat([previous, latest], ignore_index=True, sort=False)
    if combined.empty:
        combined = pd.DataFrame(columns=ANALYTICS_COLUMNS)
    else:
        combined["snapshotDate"] = combined["snapshotDate"].astype(str)
        dates = sorted(value for value in combined["snapshotDate"].dropna().unique() if value)
        if retention_days > 0 and len(dates) > retention_days:
            combined = combined[combined["snapshotDate"].isin(dates[-retention_days:])]
        keys = ["snapshotDate", "symbol", "expiration", "right", "strike"]
        if "conId" in combined.columns:
            keys.append("conId")
        combined = combined.drop_duplicates(keys, keep="last")
        combined = combined.sort_values(
            ["snapshotDate", "symbol", "dte", "expiration", "right", "strike"],
            ignore_index=True,
        )
        combined = combined.reindex(columns=ANALYTICS_COLUMNS)
    combined.to_csv(destination, index=False, encoding="utf-8-sig")
    return destination
