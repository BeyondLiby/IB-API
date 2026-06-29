from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import pandas as pd
from ib_async import IB, util
from ib_async.ib import StartupFetch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from target_treasury_account_monitor.config import DEFAULT_CLIENT_ID, DEFAULT_HOST, DEFAULT_PORT
from target_treasury_account_monitor.contracts import contract_label


def parse_args() -> argparse.Namespace:
    """Parse CLI args for inspecting combo/spread data sources from IB."""
    parser = argparse.ArgumentParser(description="Inspect whether IB API returns BAG/combo positions, portfolio items, orders, or executions.")
    parser.add_argument("--account", required=True)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID + 50)
    parser.add_argument("--include-executions", action="store_true", help="Also request executions, which can be slower/noisier.")
    return parser.parse_args()


def contract_row(source: str, account: str, contract: Any, quantity: Any = "") -> dict[str, Any]:
    """Flatten one contract-like object for source diagnostics."""
    return {
        "source": source,
        "account": account,
        "secType": getattr(contract, "secType", ""),
        "symbol": getattr(contract, "symbol", ""),
        "localSymbol": contract_label(contract),
        "conId": getattr(contract, "conId", ""),
        "exchange": getattr(contract, "exchange", ""),
        "currency": getattr(contract, "currency", ""),
        "quantity": quantity,
        "comboLegCount": len(getattr(contract, "comboLegs", []) or []),
    }


def print_source(name: str, rows: list[dict[str, Any]]) -> None:
    """Print source rows and a secType count summary."""
    print(f"\n=== {name} ===")
    if not rows:
        print("No rows.")
        return
    frame = pd.DataFrame(rows)
    print("secType counts:", dict(Counter(frame["secType"].astype(str))))
    print(frame.to_string(index=False))


def inspect_positions(ib: IB, account: str) -> list[dict[str, Any]]:
    """Inspect ib.positions() for BAG or leg-only holdings."""
    rows = []
    for pos in ib.positions():
        if str(getattr(pos, "account", "")) != account:
            continue
        rows.append(contract_row("positions", pos.account, pos.contract, getattr(pos, "position", "")))
    return rows


def inspect_portfolio(ib: IB, account: str) -> list[dict[str, Any]]:
    """Inspect ib.portfolio() for BAG or leg-only portfolio items."""
    rows = []
    for item in ib.portfolio():
        if str(getattr(item, "account", "")) != account:
            continue
        rows.append(contract_row("portfolio", item.account, item.contract, getattr(item, "position", "")))
    return rows


def inspect_open_trades(ib: IB, account: str) -> list[dict[str, Any]]:
    """Inspect open trades/orders for BAG combo contracts."""
    rows = []
    try:
        trades = ib.openTrades()
    except Exception:
        trades = []
    for trade in trades:
        order = getattr(trade, "order", None)
        if account and str(getattr(order, "account", "") or "") not in {"", account}:
            continue
        contract = getattr(trade, "contract", None)
        rows.append(contract_row("openTrades", getattr(order, "account", ""), contract, getattr(order, "totalQuantity", "")))
    return rows


def inspect_completed_orders(ib: IB, account: str) -> list[dict[str, Any]]:
    """Inspect completed orders when the connected IB API exposes them."""
    rows = []
    try:
        completed = ib.reqCompletedOrders(apiOnly=False)
    except Exception as exc:
        print(f"\ncompleted orders unavailable: {exc}")
        return rows
    for trade in completed or []:
        order = getattr(trade, "order", None)
        if account and str(getattr(order, "account", "") or "") not in {"", account}:
            continue
        contract = getattr(trade, "contract", None)
        rows.append(contract_row("completedOrders", getattr(order, "account", ""), contract, getattr(order, "totalQuantity", "")))
    return rows


def inspect_executions(ib: IB, account: str) -> list[dict[str, Any]]:
    """Inspect recent executions for BAG contracts or leg executions."""
    rows = []
    try:
        executions = ib.reqExecutions()
    except Exception as exc:
        print(f"\nexecutions unavailable: {exc}")
        return rows
    for fill in executions or []:
        execution = getattr(fill, "execution", None)
        if account and str(getattr(execution, "acctNumber", "") or "") != account:
            continue
        contract = getattr(fill, "contract", None)
        rows.append(contract_row("executions", getattr(execution, "acctNumber", ""), contract, getattr(execution, "shares", "")))
    return rows


def main() -> None:
    """Connect to IB and print all available combo-identifying sources."""
    args = parse_args()
    util.startLoop()
    ib = IB()
    ib.connect(
        args.host,
        args.port,
        clientId=args.client_id,
        readonly=True,
        timeout=10,
        fetchFields=StartupFetch.POSITIONS | StartupFetch.ACCOUNT_UPDATES | StartupFetch.SUB_ACCOUNT_UPDATES,
    )
    try:
        print_source("positions()", inspect_positions(ib, args.account))
        print_source("portfolio()", inspect_portfolio(ib, args.account))
        print_source("openTrades()", inspect_open_trades(ib, args.account))
        print_source("completedOrders()", inspect_completed_orders(ib, args.account))
        if args.include_executions:
            print_source("executions()", inspect_executions(ib, args.account))
        print("\nIf positions()/portfolio() contain only FOP legs and no BAG rows, IB is not returning current holdings grouped by original spread order.")
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
