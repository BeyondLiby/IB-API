from __future__ import annotations

import math
from typing import Any

import pandas as pd


def clean_number(value: Any) -> float:
    """把 IB 返回的混合数值转成 float，并把缺失哨兵值转成 NaN。"""
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
    """判断数值是否可以参与展示和聚合。"""
    number = clean_number(value)
    if math.isnan(number):
        return False
    return allow_zero or number != 0.0


def fmt_number(value: Any, digits: int = 2) -> str:
    """格式化普通数字，用于仪表盘指标。"""
    number = clean_number(value)
    if math.isnan(number):
        return "-"
    return f"{number:,.{digits}f}"


def fmt_money(value: Any, digits: int = 0) -> str:
    """格式化美元金额，用于账户资金指标。"""
    number = clean_number(value)
    if math.isnan(number):
        return "-"
    return f"${number:,.{digits}f}"


def summary_value(summary: pd.DataFrame, tag: str) -> float:
    """从标准化后的 accountSummary 表中读取一个指标。"""
    if summary.empty:
        return math.nan
    rows = summary[summary["tag"] == tag]
    if rows.empty:
        return math.nan
    return clean_number(rows.iloc[0]["value"])
