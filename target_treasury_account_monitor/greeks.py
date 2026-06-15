from __future__ import annotations

import math
from typing import Any

import pandas as pd

try:
    from .utils import clean_number
except ImportError:
    from utils import clean_number


def greek_source(ticker: Any) -> tuple[str, Any]:
    """Return the first available IB Greeks object and the field it came from."""
    for name in ("modelGreeks", "lastGreeks", "askGreeks", "bidGreeks"):
        value = getattr(ticker, name, None)
        if value is not None:
            return name, value
    return "", None


def read_ticker_greeks(ticker: Any) -> dict[str, Any]:
    """Normalize IB ticker Greeks into scalar values for one holding row."""
    greek_name, greeks = greek_source(ticker) if ticker is not None else ("", None)
    ticker_iv = clean_number(getattr(ticker, "impliedVolatility", math.nan)) if ticker is not None else math.nan
    model_iv = clean_number(getattr(greeks, "impliedVol", math.nan)) if greeks else math.nan
    return {
        "greekSource": greek_name,
        "iv": model_iv if not math.isnan(model_iv) else ticker_iv,
        "delta": clean_number(getattr(greeks, "delta", math.nan)) if greeks else math.nan,
        "gamma": clean_number(getattr(greeks, "gamma", math.nan)) if greeks else math.nan,
        "theta": clean_number(getattr(greeks, "theta", math.nan)) if greeks else math.nan,
        "vega": clean_number(getattr(greeks, "vega", math.nan)) if greeks else math.nan,
        "modelOptionPrice": clean_number(getattr(greeks, "optPrice", math.nan)) if greeks else math.nan,
        "underlyingPrice": clean_number(getattr(greeks, "undPrice", math.nan)) if greeks else math.nan,
    }


def greek_totals(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate position-level Greeks into account-level exposure rows."""
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "method",
                "deltaContracts",
                "deltaMultiplier",
                "gammaMultiplier",
                "thetaMultiplier",
                "vegaMultiplier",
            ]
        )
    return pd.DataFrame(
        [
            {
                "method": "IB system ticker Greeks",
                "deltaContracts": pd.to_numeric(frame["systemDeltaContracts"], errors="coerce").sum(),
                "deltaMultiplier": pd.to_numeric(frame["systemDeltaMultiplier"], errors="coerce").sum(),
                "gammaMultiplier": pd.to_numeric(frame["systemGammaMultiplier"], errors="coerce").sum(),
                "thetaMultiplier": pd.to_numeric(frame["systemThetaMultiplier"], errors="coerce").sum(),
                "vegaMultiplier": pd.to_numeric(frame["systemVegaMultiplier"], errors="coerce").sum(),
            },
            {
                "method": "Mid-price Greeks",
                "deltaContracts": math.nan,
                "deltaMultiplier": math.nan,
                "gammaMultiplier": math.nan,
                "thetaMultiplier": math.nan,
                "vegaMultiplier": math.nan,
            },
        ]
    )
