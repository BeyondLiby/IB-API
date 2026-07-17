from __future__ import annotations

import math
from typing import Any

import pandas as pd

try:
    from .config import DEFAULT_TICK_SIZE
    from .contracts import contract_cash_multiplier, contract_label, contract_multiplier, is_treasury_contract, option_full_name
    from .greeks import read_ticker_greeks
    from .market_data import ticker_price, ticker_snapshot
    from .utils import clean_number, is_valid_number
except ImportError:
    from config import DEFAULT_TICK_SIZE
    from contracts import contract_cash_multiplier, contract_label, contract_multiplier, is_treasury_contract, option_full_name
    from greeks import read_ticker_greeks
    from market_data import ticker_price, ticker_snapshot
    from utils import clean_number, is_valid_number


def positions_to_frame(
    positions: list[Any],
    tickers: dict[int, Any],
    portfolio_map: dict[int, Any],
    *,
    reference_price: float = math.nan,
    tick_size: float = DEFAULT_TICK_SIZE,
) -> pd.DataFrame:
    """Convert target treasury positions plus live IB data into a display frame."""
    rows: list[dict[str, Any]] = []
    for pos in positions:
        contract = pos.contract
        con_id = int(getattr(contract, "conId", 0) or 0)
        quantity = float(getattr(pos, "position", 0) or 0)
        avg_cost = clean_number(getattr(pos, "avgCost", math.nan))
        ticker = tickers.get(con_id)
        portfolio_item = portfolio_map.get(con_id)
        has_ticker = ticker is not None
        has_portfolio_item = portfolio_item is not None
        quote = ticker_snapshot(ticker) if ticker is not None else {}
        symbol = str(getattr(contract, "symbol", "") or "").upper()
        sec_type = str(getattr(contract, "secType", "") or "").upper()
        is_option = sec_type in {"FOP", "OPT"}

        def valid_price(value: float) -> bool:
            # IB commonly uses -1/-100 as an unavailable option-price sentinel.
            # A listed option price cannot be negative, so never let those values
            # flow into premium or market-value calculations.
            return is_valid_number(value) and (not is_option or value >= 0)

        price = math.nan
        price_source = ""
        if portfolio_item is not None:
            price = clean_number(getattr(portfolio_item, "marketPrice", math.nan))
            price_source = "portfolio" if valid_price(price) else ""
            if not valid_price(price):
                price = math.nan
        if ticker is not None and not valid_price(price):
            price, price_source = ticker_price(ticker)
        if is_option and not valid_price(price):
            price = math.nan
            price_source = ""
            for quote_name in (
                "mid",
                "last",
                "markPrice",
                "close",
                "delayedMid",
                "delayedLast",
                "delayedClose",
                "bid",
                "ask",
                "modelOptionPrice",
            ):
                candidate = clean_number(quote.get(quote_name, math.nan))
                if valid_price(candidate):
                    price = candidate
                    price_source = quote_name
                    break

        contract_multiplier_value = contract_multiplier(contract)
        multiplier = contract_cash_multiplier(contract)
        market_value = (
            clean_number(getattr(portfolio_item, "marketValue", math.nan))
            if portfolio_item is not None
            else math.nan
        )
        value_source = "portfolio" if is_valid_number(market_value) else ""
        if not is_valid_number(market_value) and is_valid_number(price):
            market_value = quantity * price * multiplier
            value_source = f"estimated_from_{price_source}"

        # IB reports ZC option quotes in cents, while the contract metadata still
        # carries the raw 5,000 bushel multiplier.  A stale/derived portfolio value
        # can therefore be exactly 100x too large.  Prefer the cash-value convention
        # ($50 per quoted cent) whenever the portfolio value is implausibly far from
        # the live price-derived value.
        expected_market_value = quantity * price * multiplier if is_valid_number(price) else math.nan
        value_ratio = (
            abs(market_value / expected_market_value)
            if is_valid_number(market_value) and is_valid_number(expected_market_value) and abs(expected_market_value) > 1e-9
            else math.nan
        )
        normalized_zc_value = (
            symbol == "ZC"
            and sec_type in {"FOP", "OPT"}
            and is_valid_number(expected_market_value)
            and is_valid_number(value_ratio)
            and value_ratio > 10.0
        )
        if normalized_zc_value:
            market_value = expected_market_value
            value_source = "normalized_from_portfolio"

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
        if (normalized_zc_value or not is_valid_number(unrealized_pnl)) and is_valid_number(estimated_unrealized_pnl):
            unrealized_pnl = estimated_unrealized_pnl

        greek = read_ticker_greeks(ticker)
        if str(getattr(contract, "secType", "") or "").upper() == "FUT" and not is_valid_number(greek["delta"]):
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
        greek_ready = str(getattr(contract, "secType", "") or "").upper() == "FUT" or is_valid_number(greek["delta"])
        missing_notes = []
        if not has_portfolio_item:
            missing_notes.append("no portfolio item")
        if not is_valid_number(unrealized_pnl):
            missing_notes.append("unPnL unavailable")
        if not has_ticker:
            missing_notes.append("no ticker")
        if has_ticker and not quote_ready:
            missing_notes.append("quote unavailable")
        if str(getattr(contract, "secType", "") or "").upper() == "FOP" and not greek_ready:
            missing_notes.append("greeks unavailable")
        if is_valid_number(greek["delta"]) and not is_valid_number(greek["iv"]):
            missing_notes.append("iv unavailable")
        strike = clean_number(getattr(contract, "strike", math.nan))
        right = str(getattr(contract, "right", "") or "").upper()
        signed_distance_ticks = (
            (strike - reference_price) / tick_size
            if is_valid_number(strike) and is_valid_number(reference_price) and tick_size > 0
            else math.nan
        )
        abs_distance_ticks = abs(signed_distance_ticks) if is_valid_number(signed_distance_ticks) else math.nan
        if right == "C" and is_valid_number(signed_distance_ticks):
            otm_ticks = max(signed_distance_ticks, 0.0)
        elif right == "P" and is_valid_number(signed_distance_ticks):
            otm_ticks = max(-signed_distance_ticks, 0.0)
        else:
            otm_ticks = math.nan
        if is_valid_number(otm_ticks) and otm_ticks > 0:
            moneyness = "far OTM" if otm_ticks > 2.5 else "OTM"
        elif is_valid_number(abs_distance_ticks) and abs_distance_ticks <= 0.5:
            moneyness = "ATM"
        elif right in {"C", "P"} and is_valid_number(otm_ticks):
            moneyness = "ITM"
        else:
            moneyness = ""

        rows.append(
            {
                "account": getattr(pos, "account", ""),
                "symbol": getattr(contract, "symbol", ""),
                "localSymbol": contract_label(contract),
                "optionName": option_full_name(contract),
                "secType": getattr(contract, "secType", ""),
                "expiry": getattr(contract, "lastTradeDateOrContractMonth", ""),
                "strike": strike,
                "right": right,
                "referencePrice": reference_price,
                "signedDistanceTicks": round(signed_distance_ticks, 1) if is_valid_number(signed_distance_ticks) else math.nan,
                "absDistanceTicks": round(abs_distance_ticks, 1) if is_valid_number(abs_distance_ticks) else math.nan,
                "otmTicks": round(otm_ticks, 1) if is_valid_number(otm_ticks) else math.nan,
                "moneyness": moneyness,
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
                "modelOptionPrice": greek["modelOptionPrice"] if is_valid_number(greek["modelOptionPrice"]) else quote.get("modelOptionPrice", math.nan),
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
                "contractMultiplier": contract_multiplier_value,
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
                "midGreekStatus": "TODO",
                "conId": con_id,
            }
        )
    frame = pd.DataFrame(rows)
    if frame.empty:
        return frame
    return frame.sort_values(["symbol", "secType", "expiry", "strike", "right"], ignore_index=True)


def excluded_positions_frame(all_positions: list[Any]) -> pd.DataFrame:
    """Build an audit table of positions excluded by the treasury filter."""
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
