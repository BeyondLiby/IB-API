from __future__ import annotations

import html
import math
from typing import Any

import pandas as pd

try:
    from .portfolio_view import PORTFOLIO_VIEW_COLUMNS, build_portfolio_view
except ImportError:
    from portfolio_view import PORTFOLIO_VIEW_COLUMNS, build_portfolio_view


def valid_number(value: Any) -> bool:
    """Return whether a value can be rendered as a finite number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return not math.isnan(number)


def fmt_number(value: Any, digits: int = 2) -> str:
    """Format a number for notebook HTML cells."""
    if not valid_number(value):
        return "-"
    return f"{float(value):,.{digits}f}"


def fmt_percent(value: Any, digits: int = 1) -> str:
    """Format a percentage value already expressed in percent units."""
    if not valid_number(value):
        return "-"
    return f"{float(value):,.{digits}f}%"


def pnl_class(value: Any) -> str:
    """Return a CSS class for positive or negative numbers."""
    if not valid_number(value):
        return ""
    number = float(value)
    if number > 0:
        return "pos"
    if number < 0:
        return "neg"
    return ""


def product_cell(row: pd.Series) -> str:
    """Render the product cell with bold product and muted description."""
    product_col = PORTFOLIO_VIEW_COLUMNS[0]
    desc_col = PORTFOLIO_VIEW_COLUMNS[1]
    structure_col = PORTFOLIO_VIEW_COLUMNS[14]
    detail_col = PORTFOLIO_VIEW_COLUMNS[15]
    product = html.escape(str(row.get(product_col, "")))
    desc = html.escape(str(row.get(desc_col, "")))
    structure = html.escape(str(row.get(structure_col, "")))
    detail = html.escape(str(row.get(detail_col, "")))
    return (
        f"<div class='product-main'>{product}</div>"
        f"<div class='product-sub'>{desc}</div>"
        f"<div class='product-note'>{structure} · {detail}</div>"
    )


def render_cell(row: pd.Series, col: str) -> str:
    """Render one notebook table cell based on the portfolio view column."""
    idx = PORTFOLIO_VIEW_COLUMNS.index(col)
    value = row.get(col, math.nan)
    if idx == 0:
        return product_cell(row)
    if idx in {2, 3, 4, 5, 11, 12, 13}:
        return fmt_number(value, 3 if idx in {2, 11, 12, 13} else 2)
    if idx in {6, 9, 10}:
        return fmt_percent(value, 1)
    if idx in {7, 8}:
        return fmt_number(value, 0)
    return html.escape(str(value)) if str(value) else "-"


def portfolio_view_to_html(
    view: pd.DataFrame,
    *,
    account: str = "",
    future_ref: dict[str, Any] | None = None,
    title: str = "投资组合",
) -> str:
    """Convert an IBKR-like portfolio view DataFrame into styled notebook HTML."""
    future_ref = future_ref or {}
    visible_cols = [
        PORTFOLIO_VIEW_COLUMNS[0],
        PORTFOLIO_VIEW_COLUMNS[2],
        PORTFOLIO_VIEW_COLUMNS[3],
        PORTFOLIO_VIEW_COLUMNS[4],
        PORTFOLIO_VIEW_COLUMNS[5],
        PORTFOLIO_VIEW_COLUMNS[6],
        PORTFOLIO_VIEW_COLUMNS[7],
        PORTFOLIO_VIEW_COLUMNS[8],
        PORTFOLIO_VIEW_COLUMNS[10],
        PORTFOLIO_VIEW_COLUMNS[11],
        PORTFOLIO_VIEW_COLUMNS[12],
        PORTFOLIO_VIEW_COLUMNS[13],
    ]
    account_html = html.escape(account or "")
    ref_label = html.escape(str(future_ref.get("localSymbol") or future_ref.get("symbol") or "ZF"))
    ref_price = fmt_number(future_ref.get("price", math.nan), 4)

    header = "".join(f"<th>{html.escape(col)}</th>" for col in visible_cols)
    rows = []
    for _, row in view.iterrows():
        cells = []
        for col in visible_cols:
            cls = "product-col" if col == PORTFOLIO_VIEW_COLUMNS[0] else "num"
            if col in {PORTFOLIO_VIEW_COLUMNS[4], PORTFOLIO_VIEW_COLUMNS[5], PORTFOLIO_VIEW_COLUMNS[6], PORTFOLIO_VIEW_COLUMNS[13]}:
                cls += f" {pnl_class(row.get(col))}"
            cells.append(f"<td class='{cls}'>{render_cell(row, col)}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    body = "\n".join(rows) if rows else f"<tr><td colspan='{len(visible_cols)}'>No positions</td></tr>"

    return f"""
    <style>
      .ibkr-wrap {{
        font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        color: #20242a;
        max-width: 100%;
      }}
      .ibkr-title {{
        font-size: 18px;
        font-weight: 650;
        margin-bottom: 2px;
      }}
      .ibkr-account {{
        color: #1f5fbf;
        font-size: 20px;
        font-weight: 720;
        margin-bottom: 10px;
      }}
      .ibkr-ref {{
        color: #606874;
        font-size: 13px;
        margin-bottom: 8px;
      }}
      table.ibkr-table {{
        width: 100%;
        border-collapse: collapse;
        table-layout: fixed;
        font-variant-numeric: tabular-nums;
      }}
      .ibkr-table th {{
        color: #7a7f87;
        font-size: 13px;
        font-weight: 650;
        text-align: right;
        border-top: 1px solid #e5e7eb;
        border-bottom: 1px solid #e5e7eb;
        padding: 8px 8px;
        background: #fff;
      }}
      .ibkr-table th:first-child {{
        text-align: left;
        width: 230px;
      }}
      .ibkr-table td {{
        text-align: right;
        border-bottom: 1px solid #eceff3;
        padding: 10px 8px;
        vertical-align: top;
        font-size: 15px;
      }}
      .ibkr-table td.product-col {{
        text-align: left;
      }}
      .product-main {{
        font-size: 16px;
        font-weight: 720;
        color: #1f2328;
      }}
      .product-sub {{
        color: #6b7280;
        font-size: 13px;
        margin-top: 2px;
      }}
      .product-note {{
        color: #8a9099;
        font-size: 12px;
        margin-top: 2px;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
      }}
      .pos {{ color: #078b45; }}
      .neg {{ color: #d12b38; }}
    </style>
    <div class="ibkr-wrap">
      <div class="ibkr-title">{html.escape(title)}</div>
      <div class="ibkr-account">{account_html}</div>
      <div class="ibkr-ref">{ref_label} reference: {ref_price}</div>
      <table class="ibkr-table">
        <thead><tr>{header}</tr></thead>
        <tbody>{body}</tbody>
      </table>
    </div>
    """


def render_ibkr_portfolio(
    frame: pd.DataFrame,
    spread_summary: pd.DataFrame | None = None,
    *,
    account: str = "",
    future_ref: dict[str, Any] | None = None,
    title: str = "投资组合",
) -> pd.DataFrame:
    """Display an IBKR-like portfolio table in a notebook and return the view DataFrame."""
    view = build_portfolio_view(frame, spread_summary)
    try:
        from IPython.display import HTML, display

        display(HTML(portfolio_view_to_html(view, account=account, future_ref=future_ref, title=title)))
    except ImportError:
        print(view.to_string(index=False))
    return view
