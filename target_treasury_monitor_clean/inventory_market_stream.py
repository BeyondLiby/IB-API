from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
import math
import threading
import time
from typing import Any

from ib_async import Future, IB
from ib_async.ib import StartupFetch

from target_treasury_account_monitor.config import DEFAULT_GENERIC_TICKS
from target_treasury_account_monitor.contracts import (
    contract_cash_multiplier,
    is_treasury_contract,
    normalize_market_data_contract,
)
from target_treasury_account_monitor.greeks import read_ticker_greeks
from target_treasury_account_monitor.market_data import ticker_price, ticker_snapshot

from .settings import IBSettings


STREAM_PRODUCTS = ("ZF", "ZN", "ZC")
RECONNECT_CODES = {1100, 1101, 1102, 1300, 2110}
MARKET_DATA_FALLBACK_CODES = {354, 10167, 10197}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _ticker_time(ticker: Any) -> str:
    value = getattr(ticker, "time", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return ""


def _normalize_months(value: object) -> tuple[str, ...]:
    parts = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
    months: list[str] = []
    for part in parts:
        month = str(part).strip().replace("-", "")
        if len(month) == 6 and month.isdigit() and 1 <= int(month[-2:]) <= 12 and month not in months:
            months.append(month)
    return tuple(months)


def normalize_stream_future_months(value: dict[str, object] | None) -> dict[str, tuple[str, ...]]:
    source = value or {root: "202609" for root in STREAM_PRODUCTS}
    normalized: dict[str, tuple[str, ...]] = {}
    for root in STREAM_PRODUCTS:
        months = _normalize_months(source.get(root) or source.get(root.lower()))
        if months:
            normalized[root] = months
    return normalized


class InventoryMarketStream:
    """Keep held-position and selected-underlying quotes subscribed in one IB session."""

    def __init__(
        self,
        settings: IBSettings,
        *,
        future_months: dict[str, object] | None = None,
        sample_interval_seconds: float = 0.5,
        reconnect_backoff_seconds: float = 5.0,
        request_interval_seconds: float = 0.05,
    ) -> None:
        self.settings = settings
        self.sample_interval_seconds = max(float(sample_interval_seconds), 0.1)
        self.reconnect_backoff_seconds = max(float(reconnect_backoff_seconds), 1.0)
        self.request_interval_seconds = max(float(request_interval_seconds), 0.025)
        self._future_months = normalize_stream_future_months(future_months)
        self._future_months_version = 0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._snapshot: dict[str, object] = {
            "ok": False,
            "connected": False,
            "sampledAt": "",
            "dataMode": "connecting",
            "positions": [],
            "futures": [],
            "positionSubscriptions": 0,
            "futureSubscriptions": 0,
            "marketDataTypes": {},
            "configuredAccount": self.settings.account,
            "activeAccount": "",
            "accountFallback": False,
            "error": "行情服务正在启动",
        }

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="inventory-market-stream", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(float(timeout), 0.0))

    def set_future_months(self, value: dict[str, object]) -> None:
        normalized = normalize_stream_future_months(value)
        if not normalized:
            return
        with self._lock:
            if normalized == self._future_months:
                return
            self._future_months = normalized
            self._future_months_version += 1

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            payload = dict(self._snapshot)
            payload["positions"] = [dict(row) for row in self._snapshot.get("positions", [])]
            payload["futures"] = [dict(row) for row in self._snapshot.get("futures", [])]
            payload["marketDataTypes"] = dict(self._snapshot.get("marketDataTypes", {}))
            return payload

    def _set_status(self, **changes: object) -> None:
        with self._lock:
            self._snapshot = {**self._snapshot, **changes}

    def _future_config(self) -> tuple[dict[str, tuple[str, ...]], int]:
        with self._lock:
            return dict(self._future_months), self._future_months_version

    def _run(self) -> None:
        asyncio.set_event_loop(asyncio.new_event_loop())
        while not self._stop.is_set():
            try:
                self._stream_until_disconnect()
            except Exception as exc:
                self._set_status(
                    ok=False,
                    connected=False,
                    dataMode="offline",
                    error=f"{type(exc).__name__}: {exc}",
                )
            if not self._stop.is_set():
                self._stop.wait(self.reconnect_backoff_seconds)

    def _stream_until_disconnect(self) -> None:
        ib = IB()
        farm_connected = True
        resubscribe = False
        use_delayed_fallback = self.settings.market_data_type in {1, 3}
        last_error = ""

        def on_error(_req_id: int, error_code: int, error_string: str, _contract: Any) -> None:
            nonlocal farm_connected, resubscribe, use_delayed_fallback, last_error
            code = int(error_code or 0)
            if code in {1100, 1300, 2110}:
                farm_connected = False
            elif code == 1101:
                farm_connected = True
                resubscribe = True
            elif code == 1102:
                farm_connected = True
            elif code in MARKET_DATA_FALLBACK_CODES:
                # A competing live session or missing entitlement can invalidate
                # existing live subscriptions. Requesting type 3 is safe here:
                # IB still returns type 1 whenever live data is available.
                use_delayed_fallback = True
                resubscribe = True
            if code in RECONNECT_CODES or code in {100, 101, 354, 10090, 10167, 10197}:
                last_error = f"IB {code}: {error_string}"

        ib.errorEvent += on_error
        position_tickers: dict[int, Any] = {}
        position_items: dict[int, Any] = {}
        future_tickers: dict[tuple[str, str], Any] = {}
        applied_future_version = -1
        try:
            ib.connect(
                self.settings.host,
                self.settings.port,
                clientId=self.settings.client_id,
                timeout=10,
                readonly=True,
                fetchFields=StartupFetch.POSITIONS,
            )
            ib.reqMarketDataType(3 if use_delayed_fallback else self.settings.market_data_type)
            self._set_status(ok=False, connected=True, dataMode="connecting", error="等待首批实时行情")
            last_position_sync = 0.0
            active_account = ""
            while not self._stop.is_set() and ib.isConnected():
                if resubscribe:
                    self._cancel_all(ib, position_tickers, future_tickers)
                    position_tickers.clear()
                    future_tickers.clear()
                    applied_future_version = -1
                    ib.reqMarketDataType(3 if use_delayed_fallback else self.settings.market_data_type)
                    last_position_sync = 0.0
                    resubscribe = False

                now = time.monotonic()
                if now - last_position_sync >= 1.0:
                    resolved_account = self._resolve_active_account(ib)
                    if resolved_account != active_account:
                        self._cancel_position_tickers(ib, position_tickers)
                        position_tickers.clear()
                        active_account = resolved_account
                    position_items = self._sync_position_tickers(
                        ib,
                        position_tickers,
                        active_account=active_account,
                    )
                    last_position_sync = now

                future_months, future_version = self._future_config()
                if future_version != applied_future_version:
                    self._sync_future_tickers(ib, future_tickers, future_months)
                    applied_future_version = future_version

                self._publish_snapshot(
                    ib,
                    farm_connected=farm_connected,
                    position_tickers=position_tickers,
                    position_items=position_items,
                    future_tickers=future_tickers,
                    active_account=active_account,
                    last_error=last_error,
                )
                ib.sleep(self.sample_interval_seconds)
        finally:
            self._cancel_all(ib, position_tickers, future_tickers)
            try:
                ib.errorEvent -= on_error
            except ValueError:
                pass
            if ib.isConnected():
                ib.disconnect()

    def _resolve_active_account(self, ib: IB) -> str:
        positions = list(ib.positions())
        managed = [str(value) for value in ib.managedAccounts() if str(value)]
        configured = str(self.settings.account or "")
        if configured and configured in managed:
            return configured

        relevant_counts: Counter[str] = Counter()
        for item in positions:
            account = str(getattr(item, "account", "") or "")
            contract = getattr(item, "contract", None)
            symbol = str(getattr(contract, "symbol", "") or "").upper()
            if (
                account
                and float(getattr(item, "position", 0) or 0) != 0
                and symbol in STREAM_PRODUCTS
                and is_treasury_contract(contract)
            ):
                relevant_counts[account] += 1
        if relevant_counts:
            eligible = [item for item in relevant_counts.items() if not managed or item[0] in managed]
            if eligible:
                return max(eligible, key=lambda item: (item[1], item[0]))[0]
        if managed:
            return managed[0]
        if configured and any(str(getattr(item, "account", "") or "") == configured for item in positions):
            return configured
        accounts = sorted({str(getattr(item, "account", "") or "") for item in positions} - {""})
        return accounts[0] if accounts else ""

    def _current_positions(self, ib: IB, *, active_account: str = "") -> dict[int, Any]:
        out: dict[int, Any] = {}
        for item in ib.positions():
            if active_account and str(getattr(item, "account", "")) != active_account:
                continue
            if float(getattr(item, "position", 0) or 0) == 0:
                continue
            contract = getattr(item, "contract", None)
            con_id = int(getattr(contract, "conId", 0) or 0)
            symbol = str(getattr(contract, "symbol", "") or "").upper()
            if con_id > 0 and symbol in STREAM_PRODUCTS and is_treasury_contract(contract):
                out[con_id] = item
        return out

    def _sync_position_tickers(
        self,
        ib: IB,
        tickers: dict[int, Any],
        *,
        active_account: str,
    ) -> dict[int, Any]:
        positions = self._current_positions(ib, active_account=active_account)
        for con_id in set(tickers) - set(positions):
            try:
                ib.cancelMktData(tickers[con_id].contract)
            except Exception:
                pass
            tickers.pop(con_id, None)

        for con_id in sorted(set(positions) - set(tickers)):
            contract = normalize_market_data_contract(positions[con_id].contract)
            try:
                ib.qualifyContracts(contract)
            except Exception:
                pass
            try:
                tickers[con_id] = ib.reqMktData(
                    contract,
                    genericTickList=DEFAULT_GENERIC_TICKS,
                    snapshot=False,
                    regulatorySnapshot=False,
                )
            except Exception:
                continue
            ib.sleep(self.request_interval_seconds)
        return positions

    @staticmethod
    def _cancel_position_tickers(ib: IB, tickers: dict[int, Any]) -> None:
        for ticker in tickers.values():
            try:
                ib.cancelMktData(ticker.contract)
            except Exception:
                pass

    def _sync_future_tickers(
        self,
        ib: IB,
        tickers: dict[tuple[str, str], Any],
        future_months: dict[str, tuple[str, ...]],
    ) -> None:
        wanted = {
            (root, month)
            for root, months in future_months.items()
            for month in months
        }
        for key in set(tickers) - wanted:
            try:
                ib.cancelMktData(tickers[key].contract)
            except Exception:
                pass
            tickers.pop(key, None)

        contracts: list[tuple[tuple[str, str], Any]] = []
        for root, month in sorted(wanted - set(tickers)):
            contracts.append(
                (
                    (root, month),
                    Future(
                        symbol=root,
                        lastTradeDateOrContractMonth=month,
                        exchange="CBOT",
                        currency="USD",
                    ),
                )
            )
        if contracts:
            try:
                ib.qualifyContracts(*(contract for _, contract in contracts))
            except Exception:
                pass
        for key, contract in contracts:
            if int(getattr(contract, "conId", 0) or 0) <= 0:
                continue
            try:
                tickers[key] = ib.reqMktData(
                    contract,
                    genericTickList="",
                    snapshot=False,
                    regulatorySnapshot=False,
                )
            except Exception:
                continue
            ib.sleep(self.request_interval_seconds)

    def _publish_snapshot(
        self,
        ib: IB,
        *,
        farm_connected: bool,
        position_tickers: dict[int, Any],
        position_items: dict[int, Any],
        future_tickers: dict[tuple[str, str], Any],
        active_account: str,
        last_error: str,
    ) -> None:
        position_rows: list[dict[str, object]] = []
        future_rows: list[dict[str, object]] = []
        market_data_types: Counter[int] = Counter()

        for con_id, item in position_items.items():
            ticker = position_tickers.get(con_id)
            contract = getattr(item, "contract", getattr(ticker, "contract", None))
            quote = ticker_snapshot(ticker) if ticker is not None else {}
            greek = read_ticker_greeks(ticker) if ticker is not None else {}
            price, price_source = ticker_price(ticker) if ticker is not None else (math.nan, "")
            market_data_type = int(getattr(ticker, "marketDataType", 0) or 0) if ticker is not None else 0
            if market_data_type:
                market_data_types[market_data_type] += 1
            quantity = float(getattr(item, "position", 0) or 0)
            average_cost = _finite(getattr(item, "avgCost", math.nan))
            cash_multiplier = contract_cash_multiplier(contract)
            market_value = quantity * price * cash_multiplier if math.isfinite(price) else math.nan
            position_rows.append({
                "conId": con_id,
                "symbol": str(getattr(contract, "symbol", "") or "").upper(),
                "localSymbol": str(getattr(contract, "localSymbol", "") or ""),
                "secType": str(getattr(contract, "secType", "") or ""),
                "exchange": str(getattr(contract, "exchange", "") or ""),
                "currency": str(getattr(contract, "currency", "") or ""),
                "expiry": str(getattr(contract, "lastTradeDateOrContractMonth", "") or ""),
                "strike": _finite(getattr(contract, "strike", math.nan)),
                "right": str(getattr(contract, "right", "") or "").upper(),
                "multiplier": str(getattr(contract, "multiplier", "") or ""),
                "position": quantity,
                "avgCost": average_cost,
                "costBasis": _finite(quantity * average_cost) if average_cost is not None else None,
                "marketDataType": market_data_type or None,
                "quoteTimeUtc": _ticker_time(ticker) if ticker is not None else "",
                "price": _finite(price),
                "priceSource": price_source,
                "marketValue": _finite(market_value),
                **{name: _finite(value) for name, value in quote.items()},
                **{name: (_finite(value) if name != "greekSource" else value) for name, value in greek.items()},
            })

        for (root, month), ticker in future_tickers.items():
            price, source = ticker_price(ticker)
            if root == "ZC" and math.isfinite(price) and abs(price) < 20:
                price *= 100
            market_data_type = int(getattr(ticker, "marketDataType", 0) or 0)
            if market_data_type:
                market_data_types[market_data_type] += 1
            future_rows.append({
                "root": root,
                "symbol": root,
                "month": month,
                "conId": int(getattr(ticker.contract, "conId", 0) or 0),
                "localSymbol": str(getattr(ticker.contract, "localSymbol", "") or ""),
                "price": _finite(price),
                "priceSource": f"stream_{source}" if source else "",
                "marketDataType": market_data_type or None,
                "quoteTimeUtc": _ticker_time(ticker),
            })

        observed_types = {key for key, count in market_data_types.items() if count > 0}
        if 1 in observed_types and observed_types <= {1}:
            data_mode = "live"
        elif 3 in observed_types and observed_types <= {3}:
            data_mode = "delayed"
        elif 1 in observed_types and 3 in observed_types:
            data_mode = "mixed"
        elif observed_types:
            data_mode = "other"
        else:
            data_mode = "connecting"
        connected = bool(ib.isConnected() and farm_connected)
        has_quotes = any(row.get("price") is not None for row in [*position_rows, *future_rows])
        self._set_status(
            ok=bool(connected and has_quotes),
            connected=connected,
            sampledAt=_utc_now(),
            dataMode=data_mode if connected else "offline",
            positions=position_rows,
            futures=future_rows,
            positionSubscriptions=len(position_tickers),
            futureSubscriptions=len(future_tickers),
            marketDataTypes={str(key): count for key, count in sorted(market_data_types.items())},
            configuredAccount=self.settings.account,
            activeAccount=active_account,
            accountFallback=bool(active_account and self.settings.account and active_account != self.settings.account),
            error=last_error if not connected or not has_quotes else "",
        )

    @staticmethod
    def _cancel_all(
        ib: IB,
        position_tickers: dict[int, Any],
        future_tickers: dict[tuple[str, str], Any],
    ) -> None:
        if not ib.isConnected():
            return
        seen: set[int] = set()
        for ticker in [*position_tickers.values(), *future_tickers.values()]:
            if id(ticker) in seen:
                continue
            seen.add(id(ticker))
            try:
                ib.cancelMktData(ticker.contract)
            except Exception:
                pass
