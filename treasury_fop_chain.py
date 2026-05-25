from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
from ib_async import Contract, Future, FuturesOption, IB


TREASURY_ROOTS = {"ZT", "ZF", "ZN", "TN", "ZB", "UB"}
DEFAULT_GENERIC_TICKS = "100,101,106"
SNAPSHOT_FEATURE_COLUMNS = [
    "snapshotTimeUtc",
    "expiration",
    "strike",
    "right",
    "bid",
    "ask",
    "mid",
    "last",
    "close",
    "bidSize",
    "askSize",
    "openInterest",
    "impliedVolatility",
    "iv",
    "delta",
    "gamma",
    "theta",
    "vega",
    "optPrice",
    "undPrice",
]

SNAPSHOT_FEATURE_CN = {
    "snapshotTimeUtc": "快照时间_UTC",
    "snapshotTimeChina": "快照时间_北京时间",
    "expiration": "到期日",
    "daysToExpiry": "距离到期天数",
    "strike": "行权价",
    "right": "看涨看跌",
    "bid": "买价",
    "ask": "卖价",
    "mid": "买卖中间价",
    "last": "最新成交价",
    "close": "昨收价",
    "bidSize": "买量",
    "askSize": "卖量",
    "openInterest": "未平仓量",
    "impliedVolatility": "IB期权隐含波动率tick",
    "iv": "模型隐含波动率",
    "delta": "Delta",
    "gamma": "Gamma",
    "theta": "Theta",
    "vega": "Vega",
    "optPrice": "模型期权价",
    "undPrice": "模型标的价",
}


@dataclass(frozen=True)
class UniverseResult:
    contracts: list[Contract]
    metadata: pd.DataFrame
    chain_summary: pd.DataFrame


@dataclass
class StreamStats:
    requested: int
    quote_ready: int
    greek_ready: int
    oi_ready: int
    volume_ready: int
    elapsed_seconds: float


def is_valid_number(value: Any, *, allow_negative: bool = True) -> bool:
    if value is None:
        return False
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False
    if math.isnan(value):
        return False
    if value == -1.0:
        return False
    if not allow_negative and value < 0:
        return False
    return True


def clean_number(value: Any, *, allow_negative: bool = True) -> float:
    return float(value) if is_valid_number(value, allow_negative=allow_negative) else math.nan


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    try:
        number = float(value)
        if math.isnan(number):
            return ""
        if number.is_integer():
            return str(int(number))
    except (TypeError, ValueError):
        pass
    return str(value)


def chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


def attach_error_collector(ib: IB) -> tuple[list[dict[str, Any]], Any]:
    errors: list[dict[str, Any]] = []

    def on_error(req_id, error_code, error_string, contract):
        row = {
            "time": pd.Timestamp.utcnow().isoformat(),
            "reqId": req_id,
            "errorCode": error_code,
            "errorString": error_string,
            "contract": repr(contract),
        }
        errors.append(row)
        if error_code in {100, 101, 162, 200, 354, 420, 10197}:
            print(f"[IB error] {error_code}: {error_string}")

    ib.errorEvent += on_error
    return errors, on_error


def detach_error_collector(ib: IB, handler: Any) -> None:
    try:
        ib.errorEvent -= handler
    except ValueError:
        pass


def qualify_future(ib: IB, root: str, month: str, exchange: str = "CBOT", currency: str = "USD") -> Contract:
    contract = Future(
        symbol=root,
        lastTradeDateOrContractMonth=month,
        exchange=exchange,
        currency=currency,
    )
    qualified = ib.qualifyContracts(contract)
    if not qualified or contract.conId == 0:
        raise RuntimeError(f"Could not qualify future {root}{month} on {exchange}")
    return contract


def get_reference_price(ib: IB, contract: Contract, *, fallback: float | None = None) -> float:
    ticker = ib.reqTickers(contract)[0]
    for field in ("marketPrice", "last", "close", "bid", "ask"):
        value = getattr(ticker, field, None)
        value = value() if callable(value) else value
        if is_valid_number(value):
            return float(value)
    if fallback is not None:
        return fallback
    raise RuntimeError(f"Could not get a reference price for {contract}")


def get_future_price(
    ib: IB,
    root: str,
    month: str,
    *,
    exchange: str = "CBOT",
    currency: str = "USD",
    market_data_type: int | None = None,
    fallback: float | None = None,
    wait_seconds: float = 2.0,
    raise_on_missing: bool = True,
) -> dict[str, Any]:
    """
    获取指定期货合约当前价格快照。

    返回字段：
      price       : 最终用于计算的价格
      priceSource : price 来自哪个字段
      marketPrice : ib_async Ticker.marketPrice()
      bid/ask/mid/last/close : 原始行情字段
      contract    : qualify 后的期货合约对象
      ticker      : reqTickers 返回的 Ticker 对象

    取价优先级：
      marketPrice -> mid -> last -> close -> bid -> ask -> fallback
    """
    if market_data_type is not None:
        ib.reqMarketDataType(market_data_type)

    contract = qualify_future(
        ib,
        root=root,
        month=month,
        exchange=exchange,
        currency=currency,
    )
    def values_from_ticker(ticker_obj: Any) -> dict[str, float]:
        bid = clean_number(getattr(ticker_obj, "bid", math.nan))
        ask = clean_number(getattr(ticker_obj, "ask", math.nan))
        mid = (bid + ask) / 2.0 if is_valid_number(bid) and is_valid_number(ask) else math.nan
        market_price_attr = getattr(ticker_obj, "marketPrice", None)
        market_price = market_price_attr() if callable(market_price_attr) else math.nan
        return {
            "marketPrice": clean_number(market_price),
            "mid": mid,
            "last": clean_number(getattr(ticker_obj, "last", math.nan)),
            "close": clean_number(getattr(ticker_obj, "close", math.nan)),
            "bid": bid,
            "ask": ask,
        }

    def select_price(values: dict[str, float]) -> tuple[float, str]:
        for source in ("marketPrice", "mid", "last", "close", "bid", "ask"):
            if is_valid_number(values[source]):
                return float(values[source]), source
        return math.nan, ""

    ticker = ib.reqTickers(contract)[0]
    values = values_from_ticker(ticker)
    price, price_source = select_price(values)

    if not is_valid_number(price) and wait_seconds > 0:
        stream_ticker = ib.reqMktData(contract, genericTickList="", snapshot=False)
        try:
            ib.sleep(wait_seconds)
            stream_values = values_from_ticker(stream_ticker)
            stream_price, stream_source = select_price(stream_values)
            if is_valid_number(stream_price):
                ticker = stream_ticker
                values = stream_values
                price = stream_price
                price_source = f"stream_{stream_source}"
        finally:
            ib.cancelMktData(contract)

    if not is_valid_number(price) and fallback is not None:
        price = float(fallback)
        price_source = "fallback"

    if not is_valid_number(price) and raise_on_missing:
        raise RuntimeError(f"Could not get a valid price for {root}{month}")

    return {
        "root": root,
        "month": month,
        "exchange": exchange,
        "conId": contract.conId,
        "localSymbol": contract.localSymbol,
        "price": price,
        "priceSource": price_source,
        **values,
        "contract": contract,
        "ticker": ticker,
    }


def discover_future_months(
    ib: IB,
    root: str,
    *,
    exchange: str = "CBOT",
    currency: str = "USD",
    min_month: str | None = None,
    max_count: int = 4,
) -> list[str]:
    template = Future(symbol=root, exchange=exchange, currency=currency)
    details = ib.reqContractDetails(template)
    months = sorted(
        {
            d.contract.lastTradeDateOrContractMonth[:6]
            for d in details
            if d.contract.secType == "FUT"
            and d.contract.symbol == root
            and d.contract.lastTradeDateOrContractMonth
        }
    )
    if min_month:
        months = [m for m in months if m >= min_month]
    return months[:max_count]


def build_fop_candidates_for_future(
    ib: IB,
    underlying: Contract,
    *,
    root: str,
    fop_exchange: str = "CBOT",
    currency: str = "USD",
    min_expiration: str | None = None,
    max_expiration: str | None = None,
) -> tuple[list[tuple[Contract, dict[str, Any]]], list[dict[str, Any]]]:
    chains = ib.reqSecDefOptParams(root, fop_exchange, "FUT", underlying.conId)
    candidates: list[tuple[Contract, dict[str, Any]]] = []
    summary_rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()

    for chain in chains:
        expirations = sorted(chain.expirations)
        strikes = sorted(float(s) for s in chain.strikes)
        if min_expiration:
            expirations = [e for e in expirations if e >= min_expiration]
        if max_expiration:
            expirations = [e for e in expirations if e <= max_expiration]

        summary_rows.append(
            {
                "underlyingConId": underlying.conId,
                "underlyingLocalSymbol": underlying.localSymbol,
                "underlyingMonth": underlying.lastTradeDateOrContractMonth[:6],
                "exchange": chain.exchange,
                "tradingClass": chain.tradingClass,
                "multiplier": chain.multiplier,
                "expirationCount": len(expirations),
                "strikeCount": len(strikes),
                "candidateCount": len(expirations) * len(strikes) * 2,
                "firstExpiration": expirations[0] if expirations else "",
                "lastExpiration": expirations[-1] if expirations else "",
                "minStrike": strikes[0] if strikes else math.nan,
                "maxStrike": strikes[-1] if strikes else math.nan,
            }
        )

        for exp in expirations:
            for strike in strikes:
                for right in ("C", "P"):
                    key = (
                        underlying.conId,
                        chain.exchange or fop_exchange,
                        chain.tradingClass,
                        chain.multiplier,
                        exp,
                        strike,
                        right,
                    )
                    if key in seen:
                        continue
                    seen.add(key)

                    contract = FuturesOption(
                        symbol=root,
                        lastTradeDateOrContractMonth=exp,
                        strike=strike,
                        right=right,
                        exchange=chain.exchange or fop_exchange,
                        multiplier=chain.multiplier or "",
                        currency=currency,
                        tradingClass=chain.tradingClass,
                    )
                    meta = {
                        "underlyingConId": underlying.conId,
                        "underlyingLocalSymbol": underlying.localSymbol,
                        "underlyingMonth": underlying.lastTradeDateOrContractMonth[:6],
                        "chainExchange": chain.exchange,
                        "chainTradingClass": chain.tradingClass,
                        "chainMultiplier": chain.multiplier,
                    }
                    candidates.append((contract, meta))

    return candidates, summary_rows


def qualify_contract_meta(
    ib: IB,
    contract_meta: list[tuple[Contract, dict[str, Any]]],
    *,
    batch_size: int = 200,
    sleep_seconds: float = 0.05,
) -> tuple[list[Contract], pd.DataFrame]:
    valid: list[tuple[Contract, dict[str, Any]]] = []
    total = len(contract_meta)

    for batch_no, batch in enumerate(chunks(contract_meta, batch_size), start=1):
        contracts = [item[0] for item in batch]
        try:
            ib.qualifyContracts(*contracts)
        except Exception as exc:
            print(f"Batch {batch_no} failed; falling back to single-contract qualify: {exc}")
            for contract in contracts:
                try:
                    ib.qualifyContracts(contract)
                    ib.sleep(sleep_seconds)
                except Exception:
                    pass

        for contract, meta in batch:
            if contract.conId:
                valid.append((contract, meta))

        done = min(batch_no * batch_size, total)
        print(f"qualified {done}/{total}; valid {len(valid)}", end="\r")
        ib.sleep(sleep_seconds)

    print()

    by_conid: dict[int, tuple[Contract, dict[str, Any]]] = {}
    for contract, meta in valid:
        by_conid.setdefault(contract.conId, (contract, meta))

    contracts = [item[0] for item in by_conid.values()]
    metadata = contracts_to_dataframe(contracts, [item[1] for item in by_conid.values()])
    return contracts, metadata


def build_treasury_fop_universe(
    ib: IB,
    *,
    root: str,
    future_months: Sequence[str],
    exchange: str = "CBOT",
    fop_exchange: str = "CBOT",
    currency: str = "USD",
    min_expiration: str | None = None,
    max_expiration: str | None = None,
    qualify_batch_size: int = 200,
) -> UniverseResult:
    if root.upper() not in TREASURY_ROOTS:
        print(f"Warning: {root} is not in the common Treasury futures roots {sorted(TREASURY_ROOTS)}")
    root = root.upper()

    all_candidates: list[tuple[Contract, dict[str, Any]]] = []
    chain_summary_rows: list[dict[str, Any]] = []

    for month in future_months:
        underlying = qualify_future(ib, root, month, exchange=exchange, currency=currency)
        print(f"underlying {root}{month}: conId={underlying.conId}, localSymbol={underlying.localSymbol}")
        candidates, summary_rows = build_fop_candidates_for_future(
            ib,
            underlying,
            root=root,
            fop_exchange=fop_exchange,
            currency=currency,
            min_expiration=min_expiration,
            max_expiration=max_expiration,
        )
        print(f"  candidates from option chains: {len(candidates)}")
        all_candidates.extend(candidates)
        chain_summary_rows.extend(summary_rows)

    contracts, metadata = qualify_contract_meta(
        ib,
        all_candidates,
        batch_size=qualify_batch_size,
    )
    chain_summary = pd.DataFrame(chain_summary_rows)
    return UniverseResult(contracts=contracts, metadata=metadata, chain_summary=chain_summary)


def contracts_to_dataframe(contracts: Sequence[Contract], metadata: Sequence[dict[str, Any]] | None = None) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metadata = metadata or [{} for _ in contracts]
    for contract, meta in zip(contracts, metadata):
        row = dict(meta)
        row.update(
            {
                "conId": contract.conId,
                "secType": contract.secType,
                "symbol": contract.symbol,
                "localSymbol": contract.localSymbol,
                "tradingClass": contract.tradingClass,
                "lastTradeDateOrContractMonth": contract.lastTradeDateOrContractMonth,
                "strike": contract.strike,
                "right": contract.right,
                "multiplier": contract.multiplier,
                "exchange": contract.exchange,
                "currency": contract.currency,
            }
        )
        rows.append(row)
    df = pd.DataFrame(rows)
    sort_cols = [
        col
        for col in ["underlyingMonth", "lastTradeDateOrContractMonth", "right", "strike", "conId"]
        if col in df.columns
    ]
    return df.sort_values(sort_cols, ignore_index=True) if sort_cols else df


def dataframe_to_contracts(df: pd.DataFrame) -> list[Contract]:
    contracts: list[Contract] = []
    for row in df.to_dict("records"):
        contracts.append(
            Contract(
                secType=clean_text(row.get("secType", "FOP")) or "FOP",
                conId=int(row["conId"]),
                symbol=clean_text(row.get("symbol", "")),
                localSymbol=clean_text(row.get("localSymbol", "")),
                tradingClass=clean_text(row.get("tradingClass", "")),
                lastTradeDateOrContractMonth=clean_text(row.get("lastTradeDateOrContractMonth", "")),
                strike=float(row.get("strike", 0.0)),
                right=clean_text(row.get("right", "")),
                multiplier=clean_text(row.get("multiplier", "")),
                exchange=clean_text(row.get("exchange", "CBOT")) or "CBOT",
                currency=clean_text(row.get("currency", "USD")) or "USD",
            )
        )
    return contracts


def save_universe(universe: UniverseResult, path: str | Path) -> None:
    path = Path(path)
    universe.metadata.to_csv(path, index=False, encoding="utf-8-sig")
    summary_path = path.with_name(path.stem + "_chain_summary.csv")
    universe.chain_summary.to_csv(summary_path, index=False, encoding="utf-8-sig")


def load_universe(path: str | Path) -> tuple[list[Contract], pd.DataFrame]:
    df = pd.read_csv(path)
    return dataframe_to_contracts(df), df


def select_atm_contracts(
    contracts: Sequence[Contract],
    metadata: pd.DataFrame,
    *,
    spot: float,
    expiration_after: str | None = None,
    underlying_month: str | None = None,
    max_expirations: int = 3,
    strikes_each_side: int = 8,
) -> tuple[list[Contract], pd.DataFrame]:
    df = metadata.copy()
    df["expiration"] = df["lastTradeDateOrContractMonth"].astype(str)
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    df["conId"] = pd.to_numeric(df["conId"], errors="coerce").astype("Int64")

    if expiration_after:
        df = df[df["expiration"] > expiration_after]
    if underlying_month and "underlyingMonth" in df.columns:
        df = df[df["underlyingMonth"].astype(str) == str(underlying_month)]

    expirations = sorted(df["expiration"].dropna().unique())[:max_expirations]
    df = df[df["expiration"].isin(expirations)].copy()
    df["atmDistance"] = (df["strike"] - float(spot)).abs()

    selected = []
    for exp in expirations:
        one_exp = df[df["expiration"] == exp].copy()
        strikes = (
            one_exp[["strike", "atmDistance"]]
            .drop_duplicates()
            .sort_values(["atmDistance", "strike"])
            .head(strikes_each_side * 2 + 1)["strike"]
            .tolist()
        )
        selected.append(one_exp[one_exp["strike"].isin(strikes)])

    if not selected:
        return [], df.iloc[0:0]

    sample_df = pd.concat(selected, ignore_index=True).sort_values(
        ["expiration", "strike", "right"],
        ignore_index=True,
    )
    by_conid = {contract.conId: contract for contract in contracts}
    sample_contracts = [
        by_conid[int(con_id)]
        for con_id in sample_df["conId"].dropna().tolist()
        if int(con_id) in by_conid
    ]
    return sample_contracts, sample_df


def filter_contracts_by_moneyness(
    contracts: Sequence[Contract],
    metadata: pd.DataFrame,
    *,
    spot_by_underlying_month: dict[str, float] | None = None,
    default_spot: float | None = None,
    today: str | pd.Timestamp | None = None,
    dte0_width: float = 2.0,
    non_dte0_width: float = 5.0,
) -> tuple[list[Contract], pd.DataFrame]:
    """
    按当前期货价格过滤期权 universe。

    规则：
      0DTE      : abs(strike - spot) <= dte0_width
      非 0DTE   : abs(strike - spot) <= non_dte0_width

    spot_by_underlying_month 用于不同底层月份分别匹配价格，例如：
      {"202606": 107.1, "202609": 106.7, "202612": 106.2}
    如果某个月份没有单独价格，则使用 default_spot。
    """
    if today is None:
        today_str = pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y%m%d")
    else:
        today_str = pd.Timestamp(today).strftime("%Y%m%d") if not isinstance(today, str) else today

    spot_by_underlying_month = {
        str(k): float(v) for k, v in (spot_by_underlying_month or {}).items()
    }

    df = metadata.copy()
    df["expiration"] = df["lastTradeDateOrContractMonth"].astype(str)
    df["strike"] = pd.to_numeric(df["strike"], errors="coerce")
    if "underlyingMonth" in df.columns:
        df["underlyingMonth"] = df["underlyingMonth"].astype(str)
    else:
        df["underlyingMonth"] = ""
    df["dte0"] = df["expiration"] == today_str

    def spot_for_row(row: pd.Series) -> float:
        spot = spot_by_underlying_month.get(str(row.get("underlyingMonth", "")))
        if spot is None:
            spot = default_spot
        return float(spot) if spot is not None else math.nan

    df["underlyingPriceForFilter"] = df.apply(spot_for_row, axis=1)
    df["strikeDistance"] = (df["strike"] - df["underlyingPriceForFilter"]).abs()
    df["filterWidth"] = df["dte0"].map(lambda is_0dte: dte0_width if is_0dte else non_dte0_width)
    filtered = df[
        df["underlyingPriceForFilter"].notna()
        & df["strikeDistance"].notna()
        & (df["strikeDistance"] <= df["filterWidth"])
    ].copy()

    by_conid = {contract.conId: contract for contract in contracts}
    filtered["conId"] = pd.to_numeric(filtered["conId"], errors="coerce").astype("Int64")
    filtered_contracts = [
        by_conid[int(con_id)]
        for con_id in filtered["conId"].dropna().tolist()
        if int(con_id) in by_conid
    ]
    return filtered_contracts, filtered.sort_values(
        ["underlyingMonth", "expiration", "right", "strike", "conId"],
        ignore_index=True,
    )


def get_future_prices_for_months(
    ib: IB,
    root: str,
    months: Sequence[str],
    *,
    exchange: str = "CBOT",
    currency: str = "USD",
    market_data_type: int | None = None,
    fallback_by_month: dict[str, float] | None = None,
    wait_seconds: float = 2.0,
    raise_on_missing: bool = False,
) -> pd.DataFrame:
    rows = []
    fallback_by_month = {str(k): float(v) for k, v in (fallback_by_month or {}).items()}
    for month in months:
        info = get_future_price(
            ib,
            root,
            month,
            exchange=exchange,
            currency=currency,
            market_data_type=market_data_type,
            fallback=fallback_by_month.get(str(month)),
            wait_seconds=wait_seconds,
            raise_on_missing=raise_on_missing,
        )
        rows.append({k: v for k, v in info.items() if k not in {"contract", "ticker"}})
    return pd.DataFrame(rows)


def prepare_snapshot_features(
    df: pd.DataFrame,
    *,
    include_chinese_columns: bool = True,
    timezone: str = "Asia/Shanghai",
    today: str | pd.Timestamp | None = None,
) -> pd.DataFrame:
    """
    整理快照输出：
      1. 解析 snapshotTimeUtc
      2. 增加北京时间和 DTE
      3. 只保留用户关心的 feature
      4. 可选改为中文列名
    """
    if df.empty:
        return df.copy()

    out = df.copy()
    out["snapshotTimeUtc"] = pd.to_datetime(out["snapshotTimeUtc"], utc=True, errors="coerce")
    out["snapshotTimeChina"] = out["snapshotTimeUtc"].dt.tz_convert(timezone)
    out["snapshotTimeUtc"] = out["snapshotTimeUtc"].dt.strftime("%Y-%m-%d %H:%M:%S.%f%z")
    out["snapshotTimeChina"] = out["snapshotTimeChina"].dt.strftime("%Y-%m-%d %H:%M:%S")

    exp_dt = pd.to_datetime(out["expiration"].astype(str), format="%Y%m%d", errors="coerce")
    if today is None:
        today_dt = pd.Timestamp.now(tz=timezone).normalize().tz_localize(None)
    else:
        today_dt = pd.Timestamp(today).normalize().tz_localize(None) if pd.Timestamp(today).tzinfo else pd.Timestamp(today).normalize()
    out["daysToExpiry"] = (exp_dt - today_dt).dt.days

    wanted = ["snapshotTimeChina", "daysToExpiry"] + SNAPSHOT_FEATURE_COLUMNS
    cols = [col for col in wanted if col in out.columns]
    out = out[cols]

    if include_chinese_columns:
        out = out.rename(columns={col: SNAPSHOT_FEATURE_CN.get(col, col) for col in out.columns})
    return out


def compute_volume_delta_events(
    current: pd.DataFrame,
    previous: pd.DataFrame | None,
    *,
    min_delta: float = 1.0,
) -> pd.DataFrame:
    """
    用两次快照的累计 volume 差分估算区间新增成交。

    IB 对期权通常给的是当日累计成交量，不是逐笔成交。
    因此：
      volumeDelta = current.volume - previous.volume

    只有 volumeDelta >= min_delta 的行会保留。
    """
    if current.empty or previous is None or previous.empty:
        return pd.DataFrame()
    if "conId" not in current.columns or "conId" not in previous.columns:
        return pd.DataFrame()

    cols = [
        "conId",
        "snapshotTimeUtc",
        "expiration",
        "strike",
        "right",
        "bid",
        "ask",
        "mid",
        "last",
        "close",
        "volume",
        "openInterest",
        "iv",
        "delta",
        "gamma",
        "theta",
        "vega",
        "optPrice",
        "undPrice",
    ]
    curr_cols = [col for col in cols if col in current.columns]
    prev_cols = [col for col in ["conId", "volume"] if col in previous.columns]

    curr = current[curr_cols].copy()
    prev = previous[prev_cols].copy().rename(columns={"volume": "previousVolume"})
    curr["volume"] = pd.to_numeric(curr.get("volume"), errors="coerce")
    prev["previousVolume"] = pd.to_numeric(prev["previousVolume"], errors="coerce")

    merged = curr.merge(prev, on="conId", how="left")
    merged["previousVolume"] = merged["previousVolume"].fillna(merged["volume"])
    merged["volumeDelta"] = merged["volume"] - merged["previousVolume"]
    merged = merged[merged["volumeDelta"] >= min_delta].copy()
    if merged.empty:
        return merged

    ts = pd.to_datetime(merged["snapshotTimeUtc"], utc=True, errors="coerce")
    merged["tradeDate"] = ts.dt.tz_convert("America/Chicago").dt.strftime("%Y-%m-%d")
    merged["snapshotTimeChina"] = ts.dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d %H:%M:%S")
    merged["estimatedPremium"] = merged["volumeDelta"] * pd.to_numeric(merged["mid"], errors="coerce")
    merged["estimatedDeltaExposure"] = (
        merged["volumeDelta"] * pd.to_numeric(merged["delta"], errors="coerce")
    )
    return merged.sort_values(["snapshotTimeUtc", "expiration", "strike", "right"], ignore_index=True)


def append_flow_events_sqlite(events: pd.DataFrame, db_path: str | Path) -> int:
    if events.empty:
        return 0
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    keep_cols = [
        "tradeDate",
        "snapshotTimeUtc",
        "snapshotTimeChina",
        "conId",
        "expiration",
        "strike",
        "right",
        "volumeDelta",
        "volume",
        "previousVolume",
        "bid",
        "ask",
        "mid",
        "last",
        "close",
        "openInterest",
        "iv",
        "delta",
        "gamma",
        "theta",
        "vega",
        "optPrice",
        "undPrice",
        "estimatedPremium",
        "estimatedDeltaExposure",
    ]
    frame = events[[col for col in keep_cols if col in events.columns]].copy()
    with sqlite3.connect(db_path) as conn:
        frame.to_sql("option_flow_events", conn, if_exists="append", index=False)
    return len(frame)


def load_flow_events_sqlite(db_path: str | Path, *, limit: int | None = None) -> pd.DataFrame:
    db_path = Path(db_path)
    if not db_path.exists():
        return pd.DataFrame()
    query = "select * from option_flow_events order by snapshotTimeUtc"
    if limit is not None:
        query = f"select * from option_flow_events order by snapshotTimeUtc desc limit {int(limit)}"
    with sqlite3.connect(db_path) as conn:
        frame = pd.read_sql_query(query, conn)
    if limit is not None and not frame.empty:
        frame = frame.sort_values("snapshotTimeUtc", ignore_index=True)
    return frame


def greek_to_dict(prefix: str, comp: Any) -> dict[str, float]:
    fields = ("impliedVol", "delta", "gamma", "theta", "vega", "optPrice", "undPrice", "pvDividend")
    if comp is None:
        return {f"{prefix}_{field}": math.nan for field in fields}
    return {f"{prefix}_{field}": clean_number(getattr(comp, field, math.nan)) for field in fields}


def ticker_to_record(ticker: Any) -> dict[str, Any]:
    contract = ticker.contract
    bid = clean_number(getattr(ticker, "bid", math.nan))
    ask = clean_number(getattr(ticker, "ask", math.nan))
    mid = (bid + ask) / 2.0 if is_valid_number(bid) and is_valid_number(ask) else math.nan

    if contract.right == "C":
        side_volume = clean_number(getattr(ticker, "callVolume", math.nan), allow_negative=False)
        side_open_interest = clean_number(getattr(ticker, "callOpenInterest", math.nan), allow_negative=False)
    elif contract.right == "P":
        side_volume = clean_number(getattr(ticker, "putVolume", math.nan), allow_negative=False)
        side_open_interest = clean_number(getattr(ticker, "putOpenInterest", math.nan), allow_negative=False)
    else:
        side_volume = math.nan
        side_open_interest = math.nan

    if not is_valid_number(side_volume, allow_negative=False):
        side_volume = clean_number(getattr(ticker, "volume", math.nan), allow_negative=False)
    if not is_valid_number(side_open_interest, allow_negative=False):
        side_open_interest = clean_number(getattr(ticker, "openInterest", math.nan), allow_negative=False)

    primary = (
        getattr(ticker, "modelGreeks", None)
        or getattr(ticker, "lastGreeks", None)
        or getattr(ticker, "askGreeks", None)
        or getattr(ticker, "bidGreeks", None)
    )
    row = {
        "snapshotTimeUtc": pd.Timestamp.utcnow().isoformat(),
        "marketDataType": getattr(ticker, "marketDataType", math.nan),
        "conId": contract.conId,
        "symbol": contract.symbol,
        "localSymbol": contract.localSymbol,
        "tradingClass": contract.tradingClass,
        "expiration": contract.lastTradeDateOrContractMonth,
        "strike": contract.strike,
        "right": contract.right,
        "exchange": contract.exchange,
        "currency": contract.currency,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": clean_number(getattr(ticker, "last", math.nan)),
        "close": clean_number(getattr(ticker, "close", math.nan)),
        "markPrice": clean_number(getattr(ticker, "markPrice", math.nan)),
        "bidSize": clean_number(getattr(ticker, "bidSize", math.nan), allow_negative=False),
        "askSize": clean_number(getattr(ticker, "askSize", math.nan), allow_negative=False),
        "lastSize": clean_number(getattr(ticker, "lastSize", math.nan), allow_negative=False),
        "volume": side_volume,
        "openInterest": side_open_interest,
        "callVolume": clean_number(getattr(ticker, "callVolume", math.nan), allow_negative=False),
        "putVolume": clean_number(getattr(ticker, "putVolume", math.nan), allow_negative=False),
        "callOpenInterest": clean_number(getattr(ticker, "callOpenInterest", math.nan), allow_negative=False),
        "putOpenInterest": clean_number(getattr(ticker, "putOpenInterest", math.nan), allow_negative=False),
        "rawVolume": clean_number(getattr(ticker, "volume", math.nan), allow_negative=False),
        "rawOpenInterest": clean_number(getattr(ticker, "openInterest", math.nan), allow_negative=False),
        "impliedVolatility": clean_number(getattr(ticker, "impliedVolatility", math.nan)),
        "iv": clean_number(getattr(primary, "impliedVol", math.nan)) if primary else math.nan,
        "delta": clean_number(getattr(primary, "delta", math.nan)) if primary else math.nan,
        "gamma": clean_number(getattr(primary, "gamma", math.nan)) if primary else math.nan,
        "theta": clean_number(getattr(primary, "theta", math.nan)) if primary else math.nan,
        "vega": clean_number(getattr(primary, "vega", math.nan)) if primary else math.nan,
        "optPrice": clean_number(getattr(primary, "optPrice", math.nan)) if primary else math.nan,
        "undPrice": clean_number(getattr(primary, "undPrice", math.nan)) if primary else math.nan,
        "tickCount": len(getattr(ticker, "ticks", []) or []),
    }
    row.update(greek_to_dict("bidGreeks", getattr(ticker, "bidGreeks", None)))
    row.update(greek_to_dict("askGreeks", getattr(ticker, "askGreeks", None)))
    row.update(greek_to_dict("lastGreeks", getattr(ticker, "lastGreeks", None)))
    row.update(greek_to_dict("modelGreeks", getattr(ticker, "modelGreeks", None)))
    return row


class FOPMarketDataStreamer:
    def __init__(
        self,
        ib: IB,
        *,
        generic_ticks: str = DEFAULT_GENERIC_TICKS,
        request_interval: float = 0.025,
    ) -> None:
        self.ib = ib
        self.generic_ticks = generic_ticks
        self.request_interval = request_interval
        self.tickers: list[Any] = []

    def subscribe(self, contracts: Sequence[Contract]) -> list[Any]:
        self.cancel()
        self.tickers = []
        start = time.monotonic()
        for idx, contract in enumerate(contracts, start=1):
            ticker = self.ib.reqMktData(contract, genericTickList=self.generic_ticks, snapshot=False)
            self.tickers.append(ticker)
            if idx % 100 == 0 or idx == len(contracts):
                elapsed = time.monotonic() - start
                print(f"sent {idx}/{len(contracts)} market data requests in {elapsed:.1f}s", end="\r")
            self.ib.sleep(self.request_interval)
        print()
        return self.tickers

    def readiness(self) -> StreamStats:
        quote_ready = 0
        greek_ready = 0
        oi_ready = 0
        volume_ready = 0
        for ticker in self.tickers:
            has_quote = (
                is_valid_number(getattr(ticker, "bid", math.nan))
                or is_valid_number(getattr(ticker, "ask", math.nan))
                or is_valid_number(getattr(ticker, "last", math.nan))
                or is_valid_number(getattr(ticker, "close", math.nan))
            )
            quote_ready += int(has_quote)
            greek_ready += int(getattr(ticker, "modelGreeks", None) is not None)
            c = ticker.contract
            oi = getattr(ticker, "callOpenInterest", math.nan) if c.right == "C" else getattr(ticker, "putOpenInterest", math.nan)
            vol = getattr(ticker, "callVolume", math.nan) if c.right == "C" else getattr(ticker, "putVolume", math.nan)
            oi_ready += int(is_valid_number(oi, allow_negative=False))
            volume_ready += int(is_valid_number(vol, allow_negative=False))
        return StreamStats(
            requested=len(self.tickers),
            quote_ready=quote_ready,
            greek_ready=greek_ready,
            oi_ready=oi_ready,
            volume_ready=volume_ready,
            elapsed_seconds=0.0,
        )

    def wait_until_stable(
        self,
        *,
        min_seconds: float = 2.0,
        max_seconds: float = 15.0,
        stable_seconds: float = 2.0,
        poll_seconds: float = 0.25,
    ) -> StreamStats:
        start = time.monotonic()
        last_score = (-1, -1, -1, -1)
        last_change = start
        stats = self.readiness()

        while True:
            self.ib.sleep(poll_seconds)
            stats = self.readiness()
            now = time.monotonic()
            score = (stats.quote_ready, stats.greek_ready, stats.oi_ready, stats.volume_ready)
            if score != last_score:
                last_score = score
                last_change = now
            elapsed = now - start
            stats.elapsed_seconds = elapsed
            print(
                "ready "
                f"quote={stats.quote_ready}/{stats.requested}, "
                f"greeks={stats.greek_ready}/{stats.requested}, "
                f"oi={stats.oi_ready}/{stats.requested}, "
                f"volume={stats.volume_ready}/{stats.requested}, "
                f"elapsed={elapsed:.1f}s",
                end="\r",
            )
            if elapsed >= max_seconds:
                break
            if elapsed >= min_seconds and now - last_change >= stable_seconds:
                break
        print()
        return stats

    def snapshot(self) -> pd.DataFrame:
        rows = [ticker_to_record(ticker) for ticker in self.tickers]
        df = pd.DataFrame(rows)
        if df.empty:
            return df
        return df.sort_values(["expiration", "right", "strike", "conId"], ignore_index=True)

    def cancel(self) -> None:
        if not self.tickers:
            return
        for ticker in self.tickers:
            try:
                self.ib.cancelMktData(ticker.contract)
            except Exception:
                pass
        self.ib.sleep(0.5)
        self.tickers = []


def snapshot_in_batches(
    ib: IB,
    contracts: Sequence[Contract],
    *,
    batch_size: int = 300,
    wait_max_seconds: float = 15.0,
    wait_stable_seconds: float = 2.0,
    request_interval: float = 0.025,
    generic_ticks: str = DEFAULT_GENERIC_TICKS,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    total = len(contracts)
    for batch_no, batch in enumerate(chunks(list(contracts), batch_size), start=1):
        print(f"\nmarket data batch {batch_no}: {len(batch)} contracts")
        streamer = FOPMarketDataStreamer(
            ib,
            generic_ticks=generic_ticks,
            request_interval=request_interval,
        )
        try:
            streamer.subscribe(batch)
            streamer.wait_until_stable(
                max_seconds=wait_max_seconds,
                stable_seconds=wait_stable_seconds,
            )
            frames.append(streamer.snapshot())
        finally:
            streamer.cancel()
        print(f"finished {min(batch_no * batch_size, total)}/{total}")
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def summarize_snapshot(df: pd.DataFrame) -> pd.Series:
    if df.empty:
        return pd.Series(dtype="object")
    return pd.Series(
        {
            "contracts": len(df),
            "with_quote": int((df[["bid", "ask", "last", "close"]].notna().any(axis=1)).sum()),
            "with_model_greeks": int(df["modelGreeks_delta"].notna().sum()),
            "with_primary_delta": int(df["delta"].notna().sum()),
            "with_open_interest": int(df["openInterest"].notna().sum()),
            "with_volume": int(df["volume"].notna().sum()),
            "expirations": df["expiration"].nunique(),
            "min_expiration": df["expiration"].min(),
            "max_expiration": df["expiration"].max(),
        }
    )
