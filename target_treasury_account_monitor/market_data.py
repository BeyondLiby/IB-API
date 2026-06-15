from __future__ import annotations

import math
from typing import Any

try:
    from .utils import clean_number, is_valid_number
except ImportError:
    from utils import clean_number, is_valid_number


def ticker_mid(ticker: Any) -> float:
    """Calculate bid/ask midpoint from an IB ticker when both sides are valid."""
    bid = clean_number(getattr(ticker, "bid", math.nan))
    ask = clean_number(getattr(ticker, "ask", math.nan))
    if is_valid_number(bid) and is_valid_number(ask):
        return (bid + ask) / 2.0
    return math.nan


def greek_option_price(ticker: Any) -> tuple[float, str]:
    """Read IB option model prices as a last-resort option price fallback."""
    for greek_name in ("modelGreeks", "lastGreeks", "askGreeks", "bidGreeks"):
        greeks = getattr(ticker, greek_name, None)
        price = clean_number(getattr(greeks, "optPrice", math.nan)) if greeks is not None else math.nan
        if not math.isnan(price):
            return price, f"{greek_name}.optPrice"
    return math.nan, ""


def ticker_snapshot(ticker: Any) -> dict[str, float]:
    """Normalize common quote fields into one dictionary for diagnostics."""
    bid = clean_number(getattr(ticker, "bid", math.nan))
    ask = clean_number(getattr(ticker, "ask", math.nan))
    delayed_bid = clean_number(getattr(ticker, "delayedBid", math.nan))
    delayed_ask = clean_number(getattr(ticker, "delayedAsk", math.nan))
    delayed_mid = (delayed_bid + delayed_ask) / 2.0 if is_valid_number(delayed_bid) and is_valid_number(delayed_ask) else math.nan
    model_price, _ = greek_option_price(ticker)
    return {
        "bid": bid,
        "ask": ask,
        "mid": ticker_mid(ticker),
        "last": clean_number(getattr(ticker, "last", math.nan)),
        "markPrice": clean_number(getattr(ticker, "markPrice", math.nan)),
        "close": clean_number(getattr(ticker, "close", math.nan)),
        "delayedBid": delayed_bid,
        "delayedAsk": delayed_ask,
        "delayedMid": delayed_mid,
        "delayedLast": clean_number(getattr(ticker, "delayedLast", math.nan)),
        "delayedClose": clean_number(getattr(ticker, "delayedClose", math.nan)),
        "modelOptionPrice": model_price,
    }


def ticker_price(ticker: Any) -> tuple[float, str]:
    """Pick the best live price from a ticker and return the value plus source name."""
    market_price_attr = getattr(ticker, "marketPrice", None)
    market_price = market_price_attr() if callable(market_price_attr) else math.nan
    model_price, model_price_source = greek_option_price(ticker)
    candidates = [
        ("market", market_price),
        ("mid", ticker_mid(ticker)),
        ("last", getattr(ticker, "last", math.nan)),
        ("mark", getattr(ticker, "markPrice", math.nan)),
        ("close", getattr(ticker, "close", math.nan)),
        ("bid", getattr(ticker, "bid", math.nan)),
        ("ask", getattr(ticker, "ask", math.nan)),
        (model_price_source, model_price),
        ("delayed_mid", ticker_snapshot(ticker)["delayedMid"]),
        ("delayed_last", getattr(ticker, "delayedLast", math.nan)),
        ("delayed_close", getattr(ticker, "delayedClose", math.nan)),
        ("delayed_bid", getattr(ticker, "delayedBid", math.nan)),
        ("delayed_ask", getattr(ticker, "delayedAsk", math.nan)),
    ]
    for source, value in candidates:
        number = clean_number(value)
        if not math.isnan(number):
            return number, source
    return math.nan, ""


def ticker_has_price(ticker: Any) -> bool:
    """Return whether a ticker already has any usable price field."""
    price, _ = ticker_price(ticker)
    return not math.isnan(price)
