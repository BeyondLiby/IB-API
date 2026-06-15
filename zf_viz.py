"""Pure visualization helpers — no Streamlit dependency.

Import this in Jupyter or Streamlit alike:
    from zf_viz import build_chain_html, chart_iv_smile, chart_heatmap, ...
"""
from __future__ import annotations

import math

import altair as alt
import pandas as pd

alt.data_transformers.disable_max_rows()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def valid_price(value) -> bool:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(value) and value != -1.0


def fmt(value, digits: int = 3, na: str = "") -> str:
    if not valid_price(value):
        return na
    return f"{float(value):.{digits}f}"


def fmt_int(value, na: str = "") -> str:
    if not valid_price(value):
        return na
    return f"{int(float(value)):,}"


# ---------------------------------------------------------------------------
# Option chain HTML table
# ---------------------------------------------------------------------------

_CHAIN_CSS = """
<style>
:root {
    --bg: #0f1419; --panel: #151c23; --grid: #26323d;
    --text: #dce5ee; --muted: #8c9aa7;
    --green: #51c184; --red: #ef6b73; --amber: #e5b454; --blue: #69a7ff;
}
.chain-wrap {
    max-height: 700px; overflow: auto;
    border: 1px solid var(--grid); border-radius: 8px;
    background: var(--panel);
}
table.option-chain {
    width: 100%; border-collapse: separate; border-spacing: 0;
    font-size: 12px; font-variant-numeric: tabular-nums;
    color: var(--text); background: var(--panel);
}
.option-chain th {
    position: sticky; top: 0; z-index: 2;
    background: #1b2530; color: #aebdca;
    padding: 7px 6px; border-bottom: 1px solid var(--grid);
    text-align: right; white-space: nowrap;
}
.option-chain th.strike-head { text-align: center; background: #202b36; color: #fff; }
.option-chain td {
    padding: 5px 6px; border-bottom: 1px solid rgba(38,50,61,.75);
    text-align: right; white-space: nowrap;
}
.option-chain tr:hover td { background: rgba(105,167,255,.08); }
.option-chain td.strike {
    text-align: center; background: #111a22; color: #fff;
    font-weight: 700; border-left: 1px solid var(--grid); border-right: 1px solid var(--grid);
}
.option-chain tr.atm td {
    background: rgba(229,180,84,.16);
    border-top: 1px solid rgba(229,180,84,.45);
    border-bottom: 1px solid rgba(229,180,84,.45);
}
.call { color: var(--green); }
.put  { color: var(--red); }
</style>
"""


def build_chain_html(df: pd.DataFrame, expiration: str, spot: float | None = None) -> str:
    """Return a self-contained HTML string of the option chain for one expiration."""
    one = df[df["expiration"].astype(str) == str(expiration)].copy()
    if one.empty:
        return "<p style='color:#8c9aa7'>No contracts for this expiration.</p>"

    one["strike"] = pd.to_numeric(one["strike"], errors="coerce")
    strikes = sorted(one["strike"].dropna().unique())
    atm_strike = min(strikes, key=lambda x: abs(x - spot)) if spot is not None and strikes else None

    call = one[one["right"] == "C"].set_index("strike")
    put  = one[one["right"] == "P"].set_index("strike")

    headers = [
        ("call", "IV"), ("call", "Delta"), ("call", "OI"),
        ("call", "Bid"), ("call", "Ask"), ("call", "Mid"),
        ("strike-head", "Strike"),
        ("put", "Mid"), ("put", "Ask"), ("put", "Bid"),
        ("put", "OI"), ("put", "Delta"), ("put", "IV"),
    ]

    def cell(frame: pd.DataFrame, strike: float, col: str):
        if strike not in frame.index:
            return math.nan
        val = frame.loc[strike, col]
        return val.iloc[0] if isinstance(val, pd.Series) else val

    rows = ["<tr>"]
    for cls, label in headers:
        rows.append(f"<th class='{cls}'>{label}</th>")
    rows.append("</tr>")
    thead = "".join(rows)

    body_rows = []
    for strike in strikes:
        rc = "atm" if atm_strike is not None and strike == atm_strike else ""
        body_rows.append(f"<tr class='{rc}'>")
        body_rows.append(f"<td class='call'>{fmt(cell(call, strike, 'iv'), 3)}</td>")
        body_rows.append(f"<td class='call'>{fmt(cell(call, strike, 'delta'), 3)}</td>")
        body_rows.append(f"<td class='call'>{fmt_int(cell(call, strike, 'openInterest'))}</td>")
        body_rows.append(f"<td class='call'>{fmt(cell(call, strike, 'bid'), 4)}</td>")
        body_rows.append(f"<td class='call'>{fmt(cell(call, strike, 'ask'), 4)}</td>")
        body_rows.append(f"<td class='call'>{fmt(cell(call, strike, 'mid'), 4)}</td>")
        body_rows.append(f"<td class='strike'>{fmt(strike, 2)}</td>")
        body_rows.append(f"<td class='put'>{fmt(cell(put, strike, 'mid'), 4)}</td>")
        body_rows.append(f"<td class='put'>{fmt(cell(put, strike, 'ask'), 4)}</td>")
        body_rows.append(f"<td class='put'>{fmt(cell(put, strike, 'bid'), 4)}</td>")
        body_rows.append(f"<td class='put'>{fmt_int(cell(put, strike, 'openInterest'))}</td>")
        body_rows.append(f"<td class='put'>{fmt(cell(put, strike, 'delta'), 3)}</td>")
        body_rows.append(f"<td class='put'>{fmt(cell(put, strike, 'iv'), 3)}</td>")
        body_rows.append("</tr>")

    return (
        _CHAIN_CSS
        + "<div class='chain-wrap'>"
        + f"<table class='option-chain'><thead>{thead}</thead><tbody>"
        + "".join(body_rows)
        + "</tbody></table></div>"
    )


# ---------------------------------------------------------------------------
# Altair charts  (all return alt.Chart or None — display() in Jupyter,
#                 st.altair_chart() in Streamlit)
# ---------------------------------------------------------------------------

def chart_iv_smile(df: pd.DataFrame, expiration: str):
    one = df[df["expiration"].astype(str) == str(expiration)].copy()
    one["strike"] = pd.to_numeric(one["strike"], errors="coerce")
    one["iv"]     = pd.to_numeric(one["iv"],     errors="coerce")
    one = one.dropna(subset=["strike", "iv"])
    if one.empty:
        return None
    return (
        alt.Chart(one)
        .mark_line(point=alt.OverlayMarkDef(size=40), strokeWidth=2)
        .encode(
            x=alt.X("strike:Q", title="Strike"),
            y=alt.Y("iv:Q", title="IV", scale=alt.Scale(zero=False)),
            color=alt.Color(
                "right:N", title="",
                scale=alt.Scale(domain=["C", "P"], range=["#51c184", "#ef6b73"]),
            ),
            tooltip=["localSymbol:N", "strike:Q", "right:N", "iv:Q", "delta:Q", "bid:Q", "ask:Q"],
        )
        .properties(height=280, title=f"IV Smile — {expiration}")
    )


def chart_heatmap(df: pd.DataFrame, value_col: str, title: str):
    data = df.copy()
    data[value_col] = pd.to_numeric(data[value_col], errors="coerce")
    data["strike"]  = pd.to_numeric(data["strike"],  errors="coerce")
    data = data.dropna(subset=["strike", value_col])
    if data.empty:
        return None
    base = (
        alt.Chart(data)
        .mark_rect()
        .encode(
            x=alt.X("strike:O", title="Strike", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("expiration:O", title="Expiration"),
            color=alt.Color(f"{value_col}:Q", title=title, scale=alt.Scale(scheme="viridis")),
            tooltip=["expiration:N", "strike:Q", "right:N", f"{value_col}:Q"],
        )
        .properties(height=220)
    )
    return base.facet(row=alt.Row("right:N", title=""))


def chart_oi_bars(df: pd.DataFrame, expiration: str):
    one = df[df["expiration"].astype(str) == str(expiration)].copy()
    one["strike"]       = pd.to_numeric(one["strike"],       errors="coerce")
    one["openInterest"] = pd.to_numeric(one["openInterest"], errors="coerce")
    one = one.dropna(subset=["strike", "openInterest"])
    if one.empty:
        return None
    return (
        alt.Chart(one)
        .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("strike:O", title="Strike", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("openInterest:Q", title="Open Interest"),
            color=alt.Color(
                "right:N", title="",
                scale=alt.Scale(domain=["C", "P"], range=["#51c184", "#ef6b73"]),
            ),
            tooltip=["strike:Q", "right:N", "openInterest:Q", "iv:Q", "delta:Q"],
        )
        .properties(height=260, title=f"Open Interest — {expiration}")
    )


def chart_intraday_candles(bars: pd.DataFrame):
    if bars.empty:
        return None
    data = bars.copy()
    data["direction"] = (data["close"] >= data["open"]).map({True: "up", False: "down"})
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["dateChina"], empty=False)
    base = alt.Chart(data).encode(
        x=alt.X("dateChina:T", title="Time"),
        tooltip=[
            alt.Tooltip("dateChina:T", title="Time"),
            alt.Tooltip("open:Q",  title="Open",  format=".4f"),
            alt.Tooltip("high:Q",  title="High",  format=".4f"),
            alt.Tooltip("low:Q",   title="Low",   format=".4f"),
            alt.Tooltip("close:Q", title="Close", format=".4f"),
            alt.Tooltip("volume:Q", title="Volume", format=",.0f"),
        ],
    )
    color_scale = alt.Scale(domain=["up", "down"], range=["#51c184", "#ef6b73"])
    rule = base.mark_rule().encode(
        y=alt.Y("low:Q", title="Price", scale=alt.Scale(zero=False)),
        y2="high:Q",
        color=alt.Color("direction:N", legend=None, scale=color_scale),
    )
    body = base.mark_bar(size=5).encode(
        y=alt.Y("open:Q", title="Price", scale=alt.Scale(zero=False)),
        y2="close:Q",
        color=alt.Color("direction:N", legend=None, scale=color_scale),
    )
    points = base.mark_point(opacity=0).add_params(nearest)
    return (rule + body + points).properties(height=260, title="ZF Intraday")


def chart_flow_heatmap(flow: pd.DataFrame):
    if flow.empty:
        return None
    data = flow.copy()
    data["strike"]      = pd.to_numeric(data["strike"],      errors="coerce")
    data["volumeDelta"] = pd.to_numeric(data["volumeDelta"], errors="coerce")
    data = data.dropna(subset=["strike", "volumeDelta"])
    if data.empty:
        return None
    return (
        alt.Chart(data)
        .mark_circle(opacity=0.78)
        .encode(
            x=alt.X("snapshotTimeChina:T", title="Time"),
            y=alt.Y("strike:Q", title="Strike", scale=alt.Scale(zero=False)),
            size=alt.Size("volumeDelta:Q", title="Volume Δ", scale=alt.Scale(range=[20, 900])),
            color=alt.Color(
                "right:N", title="",
                scale=alt.Scale(domain=["C", "P"], range=["#51c184", "#ef6b73"]),
            ),
            tooltip=["snapshotTimeChina:T", "expiration:N", "strike:Q", "right:N", "volumeDelta:Q", "mid:Q", "iv:Q", "delta:Q"],
        )
        .properties(height=310, title="Flow Heatmap")
    )


def chart_flow_by_strike(flow: pd.DataFrame):
    if flow.empty:
        return None
    data = flow.copy()
    data["volumeDelta"] = pd.to_numeric(data["volumeDelta"], errors="coerce")
    data["strike"]      = pd.to_numeric(data["strike"],      errors="coerce")
    data = data.dropna(subset=["volumeDelta", "strike"])
    if data.empty:
        return None
    grouped = data.groupby(["tradeDate", "strike", "right"], as_index=False)["volumeDelta"].sum()
    return (
        alt.Chart(grouped)
        .mark_bar(cornerRadiusTopLeft=2, cornerRadiusTopRight=2)
        .encode(
            x=alt.X("strike:O", title="Strike", axis=alt.Axis(labelAngle=-45)),
            y=alt.Y("volumeDelta:Q", title="Volume Δ"),
            color=alt.Color(
                "right:N", title="",
                scale=alt.Scale(domain=["C", "P"], range=["#51c184", "#ef6b73"]),
            ),
            column=alt.Column("tradeDate:N", title="Trade Date"),
            tooltip=["tradeDate:N", "strike:Q", "right:N", "volumeDelta:Q"],
        )
        .properties(height=260, title="Flow by Strike")
    )
