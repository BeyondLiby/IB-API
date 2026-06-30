from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _numeric(frame: pd.DataFrame, column: str) -> pd.Series:
    """Return a numeric series, preserving the input index when missing."""
    if column not in frame.columns:
        return pd.Series(math.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _pct(numerator: float, denominator: float) -> float:
    return round(float(numerator) / float(denominator) * 100.0, 1) if denominator else 0.0


def _with_quality_columns(frame: pd.DataFrame, *, reference_price: float | None) -> pd.DataFrame:
    """Add derived fields used by the quality report without mutating input."""
    out = frame.copy()
    out["bid"] = _numeric(out, "bid")
    out["ask"] = _numeric(out, "ask")
    out["last"] = _numeric(out, "last")
    out["close"] = _numeric(out, "close")
    out["price"] = _numeric(out, "price")
    out["strike"] = _numeric(out, "strike")
    out["delta"] = _numeric(out, "delta")
    out["gamma"] = _numeric(out, "gamma")
    out["theta"] = _numeric(out, "theta")
    out["vega"] = _numeric(out, "vega")
    out["openInterest"] = _numeric(out, "openInterest")

    if "expiry" not in out.columns and "expiration" in out.columns:
        out["expiry"] = out["expiration"].astype(str)
    if "dte" not in out.columns and "expiry" in out.columns:
        exp = pd.to_datetime(out["expiry"].astype(str).str[:8], format="%Y%m%d", errors="coerce")
        today = pd.Timestamp.now(tz="Asia/Shanghai").normalize().tz_localize(None)
        out["dte"] = (exp - today).dt.days
    out["dte"] = _numeric(out, "dte")

    if reference_price is None:
        under = _numeric(out, "undPrice")
        if under.notna().any():
            out["referenceForDistance"] = under
        elif "underlyingPrice" in out.columns:
            out["referenceForDistance"] = _numeric(out, "underlyingPrice")
        else:
            out["referenceForDistance"] = math.nan
    else:
        out["referenceForDistance"] = float(reference_price)

    out["strikeDistance"] = (out["strike"] - out["referenceForDistance"]).abs()
    out["hasBid"] = out["bid"].notna()
    out["hasAsk"] = out["ask"].notna()
    out["hasBidAskAny"] = out["hasBid"] | out["hasAsk"]
    out["hasQuoteAny"] = out[["bid", "ask", "last", "close", "price"]].notna().any(axis=1)
    out["hasGreeks"] = out[["delta", "gamma", "theta", "vega"]].notna().any(axis=1)
    out["hasFullGreeks"] = out[["delta", "gamma", "theta", "vega"]].notna().all(axis=1)
    out["hasOpenInterest"] = out["openInterest"].notna()
    out["bidMissingAskPresent"] = ~out["hasBid"] & out["hasAsk"]
    out["bothBidAskMissing"] = ~out["hasBid"] & ~out["hasAsk"]
    out["tinyAsk"] = out["bidMissingAskPresent"] & (out["ask"] <= 0.01)
    if "qualityLabel" not in out.columns:
        out["qualityLabel"] = ""
    return out


def _coverage_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = len(frame)
    metrics = {
        "rows": rows,
        "has_quote_any": int(frame["hasQuoteAny"].sum()),
        "has_bid_or_ask": int(frame["hasBidAskAny"].sum()),
        "has_bid": int(frame["hasBid"].sum()),
        "has_ask": int(frame["hasAsk"].sum()),
        "bid_missing_ask_present": int(frame["bidMissingAskPresent"].sum()),
        "tiny_ask": int(frame["tinyAsk"].sum()),
        "both_bid_ask_missing": int(frame["bothBidAskMissing"].sum()),
        "has_greeks": int(frame["hasGreeks"].sum()),
        "has_full_greeks": int(frame["hasFullGreeks"].sum()),
        "has_open_interest": int(frame["hasOpenInterest"].sum()),
    }
    return pd.DataFrame(
        [
            {
                "metric": key,
                "count": value,
                "pct": _pct(value, rows) if key != "rows" else 100.0,
            }
            for key, value in metrics.items()
        ]
    )


def _group_coverage(frame: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    existing = [col for col in group_cols if col in frame.columns]
    if not existing or frame.empty:
        return pd.DataFrame()
    grouped = frame.groupby(existing, dropna=False, observed=False)
    out = grouped.agg(
        rows=("hasQuoteAny", "size"),
        quote=("hasQuoteAny", "sum"),
        bid_or_ask=("hasBidAskAny", "sum"),
        greeks=("hasGreeks", "sum"),
        full_greeks=("hasFullGreeks", "sum"),
        oi=("hasOpenInterest", "sum"),
        both_bid_ask_missing=("bothBidAskMissing", "sum"),
        tiny_ask=("tinyAsk", "sum"),
        min_strike=("strike", "min"),
        max_strike=("strike", "max"),
        median_distance=("strikeDistance", "median"),
    ).reset_index()
    for col in ["quote", "bid_or_ask", "greeks", "full_greeks", "oi", "both_bid_ask_missing", "tiny_ask"]:
        out[col + "_pct"] = (out[col] / out["rows"] * 100.0).round(1)
    return out


def evaluate_option_chain_data(
    frame: pd.DataFrame,
    *,
    reference_price: float | None = None,
    sample_size: int = 20,
) -> dict[str, Any]:
    """Return compact quality diagnostics for option-chain snapshot or monitor frames."""
    data = _with_quality_columns(frame, reference_price=reference_price)
    if data.empty:
        return {
            "data": data,
            "coverage": pd.DataFrame(),
            "quality_labels": pd.DataFrame(),
            "by_expiration": pd.DataFrame(),
            "by_dte": pd.DataFrame(),
            "by_distance": pd.DataFrame(),
            "samples": {},
        }

    distance_bins = [0, 0.5, 1, 2, 3, 5, 8, 12, 20, math.inf]
    distance_labels = ["0-0.5", "0.5-1", "1-2", "2-3", "3-5", "5-8", "8-12", "12-20", "20+"]
    data["distanceBucket"] = pd.cut(
        data["strikeDistance"],
        bins=distance_bins,
        labels=distance_labels,
        include_lowest=True,
        right=False,
    )

    quality_labels = (
        data["qualityLabel"]
        .fillna("")
        .replace("", "unlabeled")
        .value_counts(dropna=False)
        .rename_axis("qualityLabel")
        .reset_index(name="count")
    )
    quality_labels["pct"] = (quality_labels["count"] / len(data) * 100.0).round(1)

    sample_cols = [
        col
        for col in [
            "expiry",
            "expiration",
            "dte",
            "strike",
            "right",
            "bid",
            "ask",
            "price",
            "delta",
            "gamma",
            "theta",
            "vega",
            "openInterest",
            "strikeDistance",
            "qualityLabel",
            "qualityReason",
            "localSymbol",
        ]
        if col in data.columns
    ]
    samples = {
        "tiny_ask": data[data["tinyAsk"]][sample_cols].head(sample_size),
        "both_bid_ask_missing": data[data["bothBidAskMissing"]][sample_cols].head(sample_size),
        "near_missing_greeks": data[
            (data["strikeDistance"] <= 3.0) & (~data["hasGreeks"])
        ][sample_cols].head(sample_size),
    }

    return {
        "data": data,
        "coverage": _coverage_summary(data),
        "quality_labels": quality_labels,
        "by_expiration": _group_coverage(data, ["expiry"]),
        "by_dte": _group_coverage(data, ["dte"]),
        "by_distance": _group_coverage(data, ["distanceBucket"]),
        "samples": samples,
    }


def print_option_chain_quality_report(report: dict[str, Any]) -> None:
    """Print a readable text summary from evaluate_option_chain_data()."""
    coverage = report.get("coverage", pd.DataFrame())
    labels = report.get("quality_labels", pd.DataFrame())
    print("=== Coverage ===")
    print(coverage.to_string(index=False) if not coverage.empty else "(empty)")
    print("\n=== Quality Labels ===")
    print(labels.to_string(index=False) if not labels.empty else "(empty)")
