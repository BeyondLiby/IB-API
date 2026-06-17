from __future__ import annotations

import math
from typing import Any

import pandas as pd

try:
    from .contracts import contract_label, contract_multiplier, is_treasury_contract, option_full_name
    from .greeks import read_ticker_greeks
    from .market_data import ticker_price, ticker_snapshot
    from .utils import clean_number, is_valid_number
except ImportError:
    from contracts import contract_label, contract_multiplier, is_treasury_contract, option_full_name
    from greeks import read_ticker_greeks
    from market_data import ticker_price, ticker_snapshot
    from utils import clean_number, is_valid_number


def positions_to_frame(
    positions: list[Any],
    tickers: dict[int, Any],
    portfolio_map: dict[int, Any],
) -> pd.DataFrame:
    """把 IB 持仓、报价和账户 portfolio 合并成监控主表。"""
    rows: list[dict[str, Any]] = []
    for pos in positions:
        contract = pos.contract
        sec_type = str(getattr(contract, "secType", "") or "").upper()
        con_id = int(getattr(contract, "conId", 0) or 0)
        quantity = float(getattr(pos, "position", 0) or 0)
        avg_cost = clean_number(getattr(pos, "avgCost", math.nan))
        ticker = tickers.get(con_id)
        portfolio_item = portfolio_map.get(con_id)
        has_ticker = ticker is not None
        has_portfolio_item = portfolio_item is not None
        quote = ticker_snapshot(ticker) if ticker is not None else {}

        # 价格优先使用 IB portfolio，缺失时再从实时/延迟 ticker 中兜底。
        price = math.nan
        price_source = ""
        if portfolio_item is not None:
            price = clean_number(getattr(portfolio_item, "marketPrice", math.nan))
            price_source = "portfolio" if is_valid_number(price) else ""
        if ticker is not None and not is_valid_number(price):
            price, price_source = ticker_price(ticker)

        # 市值优先使用 IB 账户口径；没有 portfolio item 时才用价格估算。
        multiplier = contract_multiplier(contract)
        market_value = (
            clean_number(getattr(portfolio_item, "marketValue", math.nan))
            if portfolio_item is not None
            else math.nan
        )
        value_source = "portfolio" if is_valid_number(market_value) else ""
        if not is_valid_number(market_value) and is_valid_number(price):
            market_value = quantity * price * multiplier
            value_source = f"estimated_from_{price_source}"

        # PnL 同样优先使用 IB，估算值只作为展示兜底。
        unrealized_pnl = (
            clean_number(getattr(portfolio_item, "unrealizedPNL", math.nan))
            if portfolio_item is not None
            else math.nan
        )
        realized_pnl = (
            clean_number(getattr(portfolio_item, "realizedPNL", math.nan))
            if portfolio_item is not None
            else math.nan
        )
        cost_basis = quantity * avg_cost if is_valid_number(avg_cost) else math.nan
        estimated_unrealized_pnl = (
            market_value - cost_basis
            if is_valid_number(market_value) and is_valid_number(cost_basis)
            else math.nan
        )
        if not is_valid_number(unrealized_pnl) and is_valid_number(estimated_unrealized_pnl):
            unrealized_pnl = estimated_unrealized_pnl

        greek = read_ticker_greeks(ticker)
        model_option_price = (
            greek["modelOptionPrice"]
            if is_valid_number(greek["modelOptionPrice"])
            else quote.get("modelOptionPrice", math.nan)
        )

        # 美债期货本身没有 option Greeks；这里按 1 手期货 delta=1 处理。
        if sec_type == "FUT" and not is_valid_number(greek["delta"]):
            greek.update(
                {
                    "greekSource": "future_delta_1",
                    "delta": 1.0,
                    "gamma": 0.0,
                    "theta": 0.0,
                    "vega": 0.0,
                }
            )
        quote_ready = is_valid_number(price)
        greek_ready = sec_type == "FUT" or is_valid_number(greek["delta"])
        missing_notes = []
        if not has_portfolio_item:
            missing_notes.append("no portfolio item")
        if not is_valid_number(unrealized_pnl):
            missing_notes.append("unPnL unavailable")
        if not has_ticker:
            missing_notes.append("no ticker")
        if has_ticker and not quote_ready:
            missing_notes.append("quote unavailable")
        if sec_type == "FOP" and not greek_ready:
            missing_notes.append("greeks unavailable")
        if is_valid_number(greek["delta"]) and not is_valid_number(greek["iv"]):
            missing_notes.append("iv unavailable")

        rows.append(
            {
                "account": getattr(pos, "account", ""),
                "symbol": getattr(contract, "symbol", ""),
                "localSymbol": contract_label(contract),
                "optionName": option_full_name(contract),
                "secType": sec_type,
                "expiry": getattr(contract, "lastTradeDateOrContractMonth", ""),
                "strike": clean_number(getattr(contract, "strike", math.nan)),
                "right": getattr(contract, "right", ""),
                "exchange": getattr(contract, "exchange", ""),
                "currency": getattr(contract, "currency", ""),
                "position": quantity,
                "avgCost": avg_cost,
                "costBasis": cost_basis,
                "bid": quote.get("bid", math.nan),
                "ask": quote.get("ask", math.nan),
                "mid": quote.get("mid", math.nan),
                "last": quote.get("last", math.nan),
                "markPrice": quote.get("markPrice", math.nan),
                "close": quote.get("close", math.nan),
                "delayedMid": quote.get("delayedMid", math.nan),
                "modelOptionPrice": model_option_price,
                "underlyingPrice": greek["underlyingPrice"],
                "price": price,
                "priceSource": price_source,
                "marketValue": market_value,
                "valueSource": value_source,
                "unrealizedPnL": unrealized_pnl,
                "estimatedUnrealizedPnL": estimated_unrealized_pnl,
                "realizedPnL": realized_pnl,
                "pnlSource": "portfolio" if has_portfolio_item else "",
                "multiplier": multiplier,
                "hasTicker": has_ticker,
                "hasPortfolioItem": has_portfolio_item,
                "quoteReady": quote_ready,
                "greekReady": greek_ready,
                "missingData": "; ".join(missing_notes),
                "greekSource": greek["greekSource"],
                "iv": greek["iv"],
                "delta": greek["delta"],
                "gamma": greek["gamma"],
                "theta": greek["theta"],
                "vega": greek["vega"],
                "systemDeltaContracts": quantity * greek["delta"] if is_valid_number(greek["delta"]) else math.nan,
                "systemDeltaMultiplier": quantity * greek["delta"] * multiplier if is_valid_number(greek["delta"]) else math.nan,
                "systemGammaMultiplier": quantity * greek["gamma"] * multiplier if is_valid_number(greek["gamma"]) else math.nan,
                "systemThetaMultiplier": quantity * greek["theta"] * multiplier if is_valid_number(greek["theta"]) else math.nan,
                "systemVegaMultiplier": quantity * greek["vega"] * multiplier if is_valid_number(greek["vega"]) else math.nan,
                "conId": con_id,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["symbol", "secType", "expiry", "strike", "right"], ignore_index=True)


def excluded_positions_frame(all_positions: list[Any]) -> pd.DataFrame:
    """列出被美债过滤器排除的非目标持仓，方便核对账户范围。"""
    rows = [
        {
            "account": getattr(pos, "account", ""),
            "localSymbol": contract_label(pos.contract),
            "secType": getattr(pos.contract, "secType", ""),
            "symbol": getattr(pos.contract, "symbol", ""),
            "position": float(getattr(pos, "position", 0) or 0),
        }
        for pos in all_positions
        if not is_treasury_contract(pos.contract)
    ]
    return pd.DataFrame(rows)
