from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import pandas as pd
from ib_async import IB, util
from ib_async.ib import StartupFetch

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from target_treasury_account_monitor.config import DEFAULT_CLIENT_ID, DEFAULT_HOST, DEFAULT_MARKET_DATA_LABEL, DEFAULT_PORT, DEFAULT_REFRESH_SECONDS, MARKET_DATA_TYPES, MonitorSettings
from target_treasury_account_monitor.ib_client import subscribe_quotes_for_positions
from target_treasury_account_monitor.margin import estimate_contract_capacity, what_if_order_margin
from target_treasury_account_monitor.snapshot import build_snapshot
from target_treasury_account_monitor.utils import is_valid_number


def parse_args() -> argparse.Namespace:
    """解析 IB what-if 保证金试算参数。"""
    parser = argparse.ArgumentParser(description="Run an IB what-if margin check for one existing treasury option contract.")
    parser.add_argument("--account", required=True, help="IB account ID, for example U1234567.")
    parser.add_argument("--contract", default="", help="optionName, localSymbol, or conId. Omit to list candidates.")
    parser.add_argument("--action", choices=["BUY", "SELL"], default="SELL")
    parser.add_argument("--quantity", type=float, default=1.0)
    parser.add_argument("--limit-price", type=float, default=math.nan)
    parser.add_argument("--safety-buffer", type=float, default=0.0)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--client-id", type=int, default=DEFAULT_CLIENT_ID + 40)
    parser.add_argument("--market-data", choices=list(MARKET_DATA_TYPES), default=DEFAULT_MARKET_DATA_LABEL)
    parser.add_argument("--wait", type=float, default=6.0)
    return parser.parse_args()


def make_settings(args: argparse.Namespace) -> MonitorSettings:
    """从命令行参数生成监控配置。"""
    return MonitorSettings(
        host=args.host,
        port=args.port,
        client_id=args.client_id,
        account=args.account,
        market_data_type=MARKET_DATA_TYPES[args.market_data],
        quote_wait_seconds=args.wait,
        refresh_seconds=DEFAULT_REFRESH_SECONDS,
        auto_refresh=False,
        auto_reconnect=False,
        reconnect_backoff_seconds=10,
        wechat_webhook_url="",
        wechat_push_enabled=False,
        wechat_min_interval_seconds=300,
        order_preview_enabled=True,
        readonly=False,
    )


def find_option_row(frame: pd.DataFrame, selector: str) -> pd.Series | None:
    """按 optionName、localSymbol 或 conId 找到一条期权持仓。"""
    if frame.empty or not selector:
        return None
    option_frame = frame[frame["secType"].astype(str) == "FOP"].copy()
    selector_text = str(selector).strip()
    matches = option_frame[
        (option_frame["optionName"].astype(str) == selector_text)
        | (option_frame["localSymbol"].astype(str) == selector_text)
        | (option_frame["conId"].astype(str) == selector_text)
    ]
    if matches.empty:
        return None
    return matches.iloc[0]


def print_candidates(frame: pd.DataFrame) -> None:
    """打印可用于 what-if 的期权候选合约。"""
    option_frame = frame[frame["secType"].astype(str) == "FOP"].copy() if not frame.empty else pd.DataFrame()
    if option_frame.empty:
        print("No option candidates found in current treasury positions.")
        return
    cols = ["optionName", "localSymbol", "position", "bid", "ask", "mid", "price", "priceSource", "conId"]
    print(option_frame[[col for col in cols if col in option_frame.columns]].to_string(index=False))


def main() -> None:
    """连接 IB，列出候选合约或执行一笔 what-if 试算，然后断开。"""
    args = parse_args()
    settings = make_settings(args)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)

    util.startLoop()
    ib = IB()
    ib.connect(
        settings.host,
        settings.port,
        clientId=settings.client_id,
        readonly=False,
        timeout=10,
        fetchFields=StartupFetch.POSITIONS | StartupFetch.ACCOUNT_UPDATES | StartupFetch.SUB_ACCOUNT_UPDATES,
    )
    try:
        snapshot = build_snapshot(ib, settings, lambda positions: subscribe_quotes_for_positions(ib, positions, settings))
        position_by_con_id = {int(getattr(pos.contract, "conId", 0) or 0): pos for pos in snapshot.positions}
        frame = snapshot.frame
        summary = snapshot.summary

        if not args.contract:
            print("Pass --contract using one of these optionName/localSymbol/conId values:")
            print_candidates(frame)
            return

        row = find_option_row(frame, args.contract)
        if row is None:
            print(f"Could not find contract: {args.contract}")
            print_candidates(frame)
            return

        limit_price = args.limit_price
        if not is_valid_number(limit_price):
            limit_price = row["price"] if is_valid_number(row.get("price")) else 0.01
        pos = position_by_con_id.get(int(row["conId"]))
        if pos is None:
            raise RuntimeError(f"Missing contract object for conId {row['conId']}")

        margin_row = what_if_order_margin(
            ib,
            pos.contract,
            action=args.action,
            quantity=args.quantity,
            limit_price=float(limit_price),
            account=settings.account,
        )
        capacity_row = estimate_contract_capacity(summary, margin_row, safety_buffer=args.safety_buffer)
        result = pd.DataFrame([{**row.to_dict(), **margin_row, **capacity_row}])
        cols = [
            "optionName",
            "localSymbol",
            "action",
            "quantity",
            "limitPrice",
            "excessLiquidity",
            "initMarginChange",
            "maintMarginChange",
            "bindingMarginChange",
            "usableLiquidity",
            "estimatedMaxContracts",
            "warningText",
        ]
        print(result[[col for col in cols if col in result.columns]].to_string(index=False))
    finally:
        ib.disconnect()


if __name__ == "__main__":
    main()
