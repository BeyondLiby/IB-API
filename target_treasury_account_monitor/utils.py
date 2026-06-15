from __future__ import annotations

import math
from typing import Any

import pandas as pd


def clean_number(value: Any) -> float:
    """Convert IB's mixed numeric values to float and treat missing sentinel values as NaN."""
    if value is None:
        return math.nan
    try:
        number = float(value)
    except (TypeError, ValueError):
        return math.nan
    if math.isnan(number) or number == -1.0:
        return math.nan
    return number


def is_valid_number(value: Any, *, allow_zero: bool = True) -> bool:
    """Return whether a value is a usable number for display and aggregation."""
    number = clean_number(value)
    if math.isnan(number):
        return False
    return allow_zero or number != 0.0


def fmt_number(value: Any, digits: int = 2) -> str:
    """Format a numeric value for compact dashboard display."""
    number = clean_number(value)
    if math.isnan(number):
        return "-"
    return f"{number:,.{digits}f}"


def fmt_money(value: Any, digits: int = 0) -> str:
    """Format a numeric value as USD money for account metrics."""
    number = clean_number(value)
    if math.isnan(number):
        return "-"
    return f"${number:,.{digits}f}"


def summary_value(summary: pd.DataFrame, tag: str) -> float:
    """Read one accountSummary tag from the normalized summary frame."""
    if summary.empty:
        return math.nan
    rows = summary[summary["tag"] == tag]
    if rows.empty:
        return math.nan
    return clean_number(rows.iloc[0]["value"])
