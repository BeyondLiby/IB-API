from __future__ import annotations

from typing import Any


# priority: 0=持仓/期权仓位，1=重点研究池，2=广泛观察池。
WATCHLIST: dict[str, dict[str, Any]] = {
    "ORCL": {
        "exchange": "NYSE",
        "priority": 0,
        "aliases": ["Oracle", "Oracle Corp", "OCI"],
    },
    "SMCI": {
        "exchange": "NASDAQ",
        "priority": 0,
        "aliases": ["Super Micro Computer", "Supermicro", "SMCI"],
    },
    "AVAV": {
        "exchange": "NASDAQ",
        "priority": 1,
        "aliases": ["AeroVironment", "AVAV", "Switchblade"],
    },
}


def normalize_watchlist(
    watchlist: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """统一股票代码大小写，并确保 ticker 本身在 aliases 中。"""
    source = watchlist or WATCHLIST
    result: dict[str, dict[str, Any]] = {}

    for symbol, item in source.items():
        upper_symbol = symbol.upper()
        aliases = list(dict.fromkeys([upper_symbol, *item.get("aliases", [])]))
        result[upper_symbol] = {
            "exchange": item.get("exchange", "SMART"),
            "priority": int(item.get("priority", 1)),
            "aliases": aliases,
        }

    return result


def portfolio_symbols(watchlist: dict[str, dict[str, Any]] | None = None) -> set[str]:
    """返回 P0 股票集合，用于降低推送阈值。"""
    normalized = normalize_watchlist(watchlist)
    return {
        symbol
        for symbol, item in normalized.items()
        if int(item.get("priority", 1)) == 0
    }
