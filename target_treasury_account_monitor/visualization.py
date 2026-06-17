from __future__ import annotations

import altair as alt
import pandas as pd

from .utils import summary_value

alt.data_transformers.disable_max_rows()


def _number_frame(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """复制表格并把指定字段转为数值，避免图表被字符串污染。"""
    data = frame.copy()
    for col in columns:
        if col in data.columns:
            data[col] = pd.to_numeric(data[col], errors="coerce")
    return data


def chart_greek_exposure(frame: pd.DataFrame) -> alt.Chart | None:
    """账户层面的 Delta/Gamma/Theta/Vega 风险柱状图。"""
    cols = {
        "systemDeltaMultiplier": "Delta x 乘数",
        "systemGammaMultiplier": "Gamma x 乘数",
        "systemThetaMultiplier": "Theta x 乘数",
        "systemVegaMultiplier": "Vega x 乘数",
    }
    if frame.empty:
        return None
    data = _number_frame(frame, list(cols)).rename(columns=cols)
    totals = data[list(cols.values())].sum(numeric_only=True).reset_index()
    totals.columns = ["希腊字母", "敞口"]
    return (
        alt.Chart(totals)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("希腊字母:N", title=""),
            y=alt.Y("敞口:Q", title="合约乘数口径", axis=alt.Axis(format=",.0f")),
            color=alt.Color("希腊字母:N", legend=None, scale=alt.Scale(scheme="tableau10")),
            tooltip=[alt.Tooltip("希腊字母:N"), alt.Tooltip("敞口:Q", format=",.2f")],
        )
        .properties(height=260, title="账户 Greeks 敞口")
    )


def chart_position_market_value(frame: pd.DataFrame) -> alt.Chart | None:
    """按持仓展示市值，快速看出风险集中在哪些合约。"""
    if frame.empty or "marketValue" not in frame.columns:
        return None
    data = _number_frame(frame, ["marketValue"]).dropna(subset=["marketValue"])
    if data.empty:
        return None
    data["合约"] = data["optionName"].fillna(data["localSymbol"]).astype(str)
    data = data.sort_values("marketValue", key=lambda s: s.abs(), ascending=False).head(25)
    return (
        alt.Chart(data)
        .mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(
            y=alt.Y("合约:N", sort="-x", title=""),
            x=alt.X("marketValue:Q", title="市值", axis=alt.Axis(format=",.0f")),
            color=alt.Color("secType:N", title="类型", scale=alt.Scale(domain=["FUT", "FOP"], range=["#4C78A8", "#F58518"])),
            tooltip=[
                alt.Tooltip("合约:N"),
                alt.Tooltip("position:Q", title="持仓", format=",.2f"),
                alt.Tooltip("marketValue:Q", title="市值", format=",.2f"),
                alt.Tooltip("price:Q", title="价格", format=",.5f"),
            ],
        )
        .properties(height=420, title="持仓市值分布")
    )


def chart_unrealized_pnl(frame: pd.DataFrame) -> alt.Chart | None:
    """未实现盈亏柱状图，颜色区分正负。"""
    if frame.empty or "unrealizedPnL" not in frame.columns:
        return None
    data = _number_frame(frame, ["unrealizedPnL"]).dropna(subset=["unrealizedPnL"])
    if data.empty:
        return None
    data["合约"] = data["optionName"].fillna(data["localSymbol"]).astype(str)
    data["方向"] = (data["unrealizedPnL"] >= 0).map({True: "盈利", False: "亏损"})
    data = data.sort_values("unrealizedPnL", key=lambda s: s.abs(), ascending=False).head(25)
    return (
        alt.Chart(data)
        .mark_bar(cornerRadiusTopRight=3, cornerRadiusBottomRight=3)
        .encode(
            y=alt.Y("合约:N", sort="-x", title=""),
            x=alt.X("unrealizedPnL:Q", title="未实现盈亏", axis=alt.Axis(format=",.0f")),
            color=alt.Color("方向:N", title="", scale=alt.Scale(domain=["盈利", "亏损"], range=["#54A24B", "#E45756"])),
            tooltip=[
                alt.Tooltip("合约:N"),
                alt.Tooltip("position:Q", title="持仓", format=",.2f"),
                alt.Tooltip("unrealizedPnL:Q", title="未实现盈亏", format=",.2f"),
                alt.Tooltip("missingData:N", title="缺失数据"),
            ],
        )
        .properties(height=420, title="未实现盈亏")
    )


def chart_liquidity(summary: pd.DataFrame) -> alt.Chart | None:
    """账户资金与保证金概览。"""
    tags = [
        ("NetLiquidation", "净清算值"),
        ("ExcessLiquidity", "剩余流动性"),
        ("AvailableFunds", "可用资金"),
        ("InitMarginReq", "初始保证金"),
        ("MaintMarginReq", "维持保证金"),
    ]
    rows = [{"指标": label, "金额": summary_value(summary, tag)} for tag, label in tags]
    data = pd.DataFrame(rows).dropna(subset=["金额"])
    if data.empty:
        return None
    return (
        alt.Chart(data)
        .mark_bar(cornerRadiusTopLeft=3, cornerRadiusTopRight=3)
        .encode(
            x=alt.X("指标:N", title=""),
            y=alt.Y("金额:Q", title="USD", axis=alt.Axis(format=",.0f")),
            color=alt.Color("指标:N", legend=None, scale=alt.Scale(scheme="set2")),
            tooltip=[alt.Tooltip("指标:N"), alt.Tooltip("金额:Q", format=",.2f")],
        )
        .properties(height=260, title="账户资金与保证金")
    )
