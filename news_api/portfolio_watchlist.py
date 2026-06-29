from __future__ import annotations


# 你给的持仓列表。交易所不确定的先用 SMART，让 IB 自己路由。
PORTFOLIO_WATCHLIST: dict[str, dict] = {
    "AOSL": {
        "exchange": "NASDAQ",
        "currency": "USD",
        "priority": 0,
        "aliases": ["Alpha and Omega Semiconductor", "AOSL"],
    },
    "AVAV": {
        "exchange": "NASDAQ",
        "currency": "USD",
        "priority": 0,
        "aliases": ["AeroVironment", "AVAV", "Switchblade"],
    },
    "IBM": {
        "exchange": "NYSE",
        "currency": "USD",
        "priority": 0,
        "aliases": ["IBM", "International Business Machines"],
    },
    "LHX": {
        "exchange": "NYSE",
        "currency": "USD",
        "priority": 0,
        "aliases": ["L3Harris", "L3Harris Technologies", "LHX"],
    },
    "SNOW": {
        "exchange": "NYSE",
        "currency": "USD",
        "priority": 0,
        "aliases": ["Snowflake", "SNOW"],
    },
    "VRT": {
        "exchange": "NYSE",
        "currency": "USD",
        "priority": 0,
        "aliases": ["Vertiv", "VRT"],
    },
    "DRAM": {
        "exchange": "SMART",
        "currency": "USD",
        "priority": 0,
        "aliases": ["DRAM"],
    },
    "FCX": {
        "exchange": "NYSE",
        "currency": "USD",
        "priority": 0,
        "aliases": ["Freeport-McMoRan", "FCX"],
    },
    "IGV": {
        "exchange": "SMART",
        "currency": "USD",
        "priority": 0,
        "aliases": ["iShares Expanded Tech-Software", "IGV"],
    },
    "NET": {
        "exchange": "NYSE",
        "currency": "USD",
        "priority": 0,
        "aliases": ["Cloudflare", "NET"],
    },
    "PUMP": {
        "exchange": "NYSE",
        "currency": "USD",
        "priority": 0,
        "aliases": ["ProPetro", "PUMP"],
    },
    "TCOM": {
        "exchange": "NASDAQ",
        "currency": "USD",
        "priority": 0,
        "aliases": ["Trip.com", "Trip.com Group", "TCOM"],
    },
    "UUUU": {
        "exchange": "AMEX",
        "currency": "USD",
        "priority": 0,
        "aliases": ["Energy Fuels", "UUUU"],
    },
    "VELO": {
        "exchange": "SMART",
        "currency": "USD",
        "priority": 0,
        "aliases": ["VELO"],
    },
    "5803": {
        "exchange": "SMART",
        "currency": "JPY",
        "priority": 0,
        "aliases": ["5803"],
    },
    "6471": {
        "exchange": "SMART",
        "currency": "JPY",
        "priority": 0,
        "aliases": ["6471"],
    },
    "SIVE": {
        "exchange": "SMART",
        "currency": "USD",
        "priority": 0,
        "aliases": ["SIVE"],
    },
    "IQE": {
        "exchange": "SMART",
        "currency": "GBP",
        "priority": 0,
        "aliases": ["IQE"],
    },
    "XFAB": {
        "exchange": "SMART",
        "currency": "EUR",
        "priority": 0,
        "aliases": ["X-FAB", "XFAB"],
    },
}


ALL_NEWS_WATCHLIST = {
    "ALL": {
        "exchange": "NEWS",
        "currency": "",
        "priority": 0,
        "aliases": ["ALL"],
    }
}
