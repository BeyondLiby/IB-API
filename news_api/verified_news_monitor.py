from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from ib_async import Contract, IB
except ImportError:  # 纯规则测试不要求安装 IB 客户端。
    Contract = None  # type: ignore[assignment]
    IB = None  # type: ignore[assignment]


IB_STATUS_CODES = {2104, 2106, 2107, 2108, 2119, 2158}
REJECTED_IB_CODES = {200, 300, 321, 354, 10167, 10089, 10090}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def news_epoch_to_utc(value: int | float | str) -> datetime | None:
    """IB tickNews normally uses Unix seconds; tolerate millisecond values too."""
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp > 10_000_000_000:
        timestamp /= 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def historical_time_to_utc(value: str) -> datetime | None:
    raw = (value or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def broadtape_contract_candidates(
    provider_codes: Iterable[str],
) -> list[tuple[str, str, str]]:
    """Return (label, symbol, source exchange) for known IB NEWS channels."""
    result: list[tuple[str, str, str]] = []

    def add(label: str, symbol: str, exchange: str) -> None:
        candidate = (label, symbol, exchange)
        if candidate not in result:
            result.append(candidate)

    for provider in provider_codes:
        code = provider.strip().upper()
        if not code:
            continue
        if code == "DJ-N":
            add("DJ-GLOBAL-TRADER", "DJ:N_DJGT", "DJ")
        elif code == "DJ-RTA":
            add("DJTOP-ASIAPAC", "DJTOP:ASIAPAC", "DJTOP")
        elif code == "DJ-RTE":
            add("DJTOP-EMEA", "DJTOP:EMEA", "DJTOP")
        elif code == "DJ-RTG":
            add("DJTOP-GLOBAL", "DJTOP:GLBNEWS", "DJTOP")
        elif code in {"DJ-RTPRO", "DJ-RT"}:
            add("DJTOP-NORTHAM", "DJTOP:NORTHAM", "DJTOP")
            add("DJTOP-COMPANY", "DJTOP:COMPNEWS", "DJTOP")
            add("DJTOP-MARKET", "DJTOP:MKTDRVE", "DJTOP")
        else:
            add(code, f"{code}:{code}_ALL", code)
    return result


def classify_delivery(
    *,
    article_key: str,
    published_at: datetime | None,
    received_at: datetime,
    subscription_started_at: datetime,
    baseline_article_keys: set[str],
    seen_article_keys: set[str],
    warmup_seconds: float = 15.0,
    clock_skew_seconds: float = 2.0,
) -> tuple[str, str]:
    """Conservatively decide whether a callback proves that the feed advanced."""
    if article_key in seen_article_keys:
        return "DUPLICATE", "article_id already seen on this subscription"
    if article_key in baseline_article_keys:
        return "SNAPSHOT", "article_id was present in pre-subscription history"

    elapsed = (received_at - subscription_started_at).total_seconds()
    if published_at is None:
        if elapsed < warmup_seconds:
            return "WARMUP", "missing/invalid news timestamp during warmup"
        return "UNVERIFIED", "missing/invalid news timestamp"

    if published_at >= subscription_started_at - timedelta(seconds=clock_skew_seconds):
        return "LIVE", "new article_id with publication time at/after subscription start"
    if elapsed < warmup_seconds:
        return "WARMUP", "older callback received during subscription warmup"
    return "BACKFILL", "publication time predates this subscription"


@dataclass(slots=True)
class SubscriptionState:
    req_id: int
    name: str
    symbol: str
    kind: str
    provider_codes: str
    contract: Any
    started_at: datetime
    baseline_article_keys: set[str] = field(default_factory=set)
    seen_article_keys: set[str] = field(default_factory=set)
    status: str = "PENDING"
    callbacks: int = 0
    unique_callbacks: int = 0
    live_callbacks: int = 0
    snapshot_callbacks: int = 0
    backfill_callbacks: int = 0
    warmup_callbacks: int = 0
    duplicate_callbacks: int = 0
    unverified_callbacks: int = 0
    first_callback_at: datetime | None = None
    last_callback_at: datetime | None = None
    last_live_at: datetime | None = None
    last_published_at: datetime | None = None
    errors: list[dict[str, Any]] = field(default_factory=list)
    history_gap_count: int = 0

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("contract", None)
        data["started_at"] = iso(self.started_at)
        for key in (
            "first_callback_at",
            "last_callback_at",
            "last_live_at",
            "last_published_at",
        ):
            data[key] = iso(getattr(self, key))
        data["baseline_article_keys"] = len(self.baseline_article_keys)
        data["seen_article_keys"] = len(self.seen_article_keys)
        return data


def monitoring_verdict(states: Iterable[SubscriptionState]) -> str:
    items = list(states)
    if any(item.status == "DEGRADED_HISTORY_GAP" for item in items):
        return "DEGRADED_HISTORY_GAP"
    if any(item.live_callbacks > 0 for item in items):
        return "VERIFIED_LIVE"
    unusable = {"NOT_ENTITLED", "REJECTED", "ERROR"}
    if items and all(item.status in unusable for item in items):
        return "NO_USABLE_SUBSCRIPTIONS"
    return "CONNECTED_BUT_NO_LIVE_REFRESH_PROVEN"


class JsonlAuditLog:
    """Append-only evidence log; repeated screen redraws cannot masquerade as data."""

    def __init__(self, path: Path, session_id: str) -> None:
        self.path = path
        self.session_id = session_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def emit(self, event: str, **payload: Any) -> None:
        row = {
            "audit_version": 1,
            "session_id": self.session_id,
            "event": event,
            "recorded_at": iso(utc_now()),
            **payload,
        }
        encoded = json.dumps(row, ensure_ascii=False, default=str)
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()


class VerifiedIBNewsMonitor:
    """IB news receiver with per-request attribution and explicit freshness proof."""

    def __init__(
        self,
        *,
        audit_path: Path,
        live_sink: Callable[..., bool] | None = None,
        warmup_seconds: float = 15.0,
        history_results: int = 50,
    ) -> None:
        if IB is None or Contract is None:
            raise RuntimeError(
                "未安装 ib_async。请使用 conda ib 环境，或安装 requirements_news.txt。"
            )
        self.ib = IB()
        # ib_async defaults to no request timeout. A bad NEWS source can then
        # wait forever because some TWS versions send an error without the
        # matching end marker.
        self.ib.RequestTimeout = 15.0
        self.session_id = uuid.uuid4().hex
        self.session_started_at = utc_now()
        self.audit = JsonlAuditLog(audit_path, self.session_id)
        self.live_sink = live_sink
        self.warmup_seconds = warmup_seconds
        self.history_results = history_results
        self.news_providers: list[dict[str, str]] = []
        self.subscriptions: dict[int, SubscriptionState] = {}
        self.discovery_errors: list[dict[str, Any]] = []
        self.ib_status: dict[str, dict[str, Any]] = {}
        self._printed_ib_status: set[tuple[int, str]] = set()
        self._tick_news_original: Callable[..., Any] | None = None
        self._heartbeat_index = 0
        self._last_heartbeat_live_total = 0
        self._history_known: dict[str, set[str]] = {}
        self._history_pending_gaps: dict[tuple[str, str], datetime] = {}
        self._history_reported_gaps: set[tuple[str, str]] = set()

    def connect(
        self,
        host: str,
        port: int,
        client_id: int,
        timeout: float = 20.0,
    ) -> None:
        self._install_tick_news_hook()
        self.ib.errorEvent += self._on_error
        self.ib.connect(
            host,
            port,
            clientId=client_id,
            timeout=timeout,
            readonly=True,
        )
        providers = self.ib.reqNewsProviders()
        self.news_providers = [
            {"code": str(item.code), "name": str(item.name)} for item in providers
        ]
        self.audit.emit(
            "session_start",
            host=host,
            port=port,
            client_id=client_id,
            readonly=True,
            providers=self.news_providers,
            session_started_at=iso(self.session_started_at),
        )

    def _install_tick_news_hook(self) -> None:
        wrapper = self.ib.wrapper
        self._tick_news_original = wrapper.tickNews

        def tracked_tick_news(
            req_id: int,
            timestamp: int,
            provider_code: str,
            article_id: str,
            headline: str,
            extra_data: str,
        ) -> None:
            self._on_tick_news(
                req_id,
                timestamp,
                provider_code,
                article_id,
                headline,
                extra_data,
            )
            assert self._tick_news_original is not None
            self._tick_news_original(
                req_id,
                timestamp,
                provider_code,
                article_id,
                headline,
                extra_data,
            )

        wrapper.tickNews = tracked_tick_news

    @property
    def visible_provider_codes(self) -> list[str]:
        return [item["code"] for item in self.news_providers]

    def qualify_stock(self, symbol: str, item: dict[str, Any]) -> Any | None:
        exchange = str(item.get("exchange", "SMART") or "SMART")
        contract = Contract(
            symbol=symbol,
            secType=str(item.get("sec_type", "STK")),
            exchange="SMART",
            currency=str(item.get("currency", "USD")),
        )
        if exchange.upper() != "SMART":
            contract.primaryExchange = exchange
        qualified = self.ib.qualifyContracts(contract)
        if not qualified:
            self.audit.emit(
                "stock_qualification_failed",
                symbol=symbol,
                exchange=exchange,
                currency=contract.currency,
            )
            return None
        return qualified[0]

    def fetch_history(
        self,
        *,
        symbol: str,
        con_id: int,
        provider_codes: str,
    ) -> list[dict[str, Any]]:
        try:
            rows = self.ib.reqHistoricalNews(
                con_id,
                provider_codes,
                "",
                "",
                self.history_results,
            )
        except Exception as exc:
            self.audit.emit(
                "historical_news_error",
                symbol=symbol,
                provider_codes=provider_codes,
                error=f"{type(exc).__name__}: {exc}",
            )
            return []
        result = []
        for row in rows:
            result.append(
                {
                    "key": f"{row.providerCode}:{row.articleId}",
                    "published_at": iso(historical_time_to_utc(str(row.time))),
                    "provider": str(row.providerCode),
                    "article_id": str(row.articleId),
                    "headline": str(row.headline),
                }
            )
        self.audit.emit(
            "historical_news_snapshot",
            symbol=symbol,
            provider_codes=provider_codes,
            count=len(result),
            newest=result[0] if result else None,
        )
        return result

    def subscribe_stock(
        self,
        symbol: str,
        item: dict[str, Any],
        provider_codes: str,
    ) -> SubscriptionState | None:
        contract = self.qualify_stock(symbol, item)
        if contract is None:
            return None
        history = self.fetch_history(
            symbol=symbol,
            con_id=int(contract.conId),
            provider_codes=provider_codes,
        )
        baseline = {row["key"] for row in history}
        self._history_known[symbol] = set(baseline)
        started_at = utc_now()
        ticker = self.ib.reqMktData(
            contract,
            genericTickList=f"mdoff,292:{provider_codes}",
            snapshot=False,
            regulatorySnapshot=False,
        )
        req_id = int(self.ib.wrapper.ticker2ReqId["mktData"][ticker])
        state = SubscriptionState(
            req_id=req_id,
            name=f"stock:{symbol}",
            symbol=symbol,
            kind="stock",
            provider_codes=provider_codes,
            contract=contract,
            started_at=started_at,
            baseline_article_keys=baseline,
        )
        self.subscriptions[req_id] = state
        self.audit.emit("subscription_requested", **state.public_dict())
        return state

    def discover_broadtape_contracts(
        self,
        provider_codes: Iterable[str],
    ) -> list[tuple[str, Any]]:
        found: list[tuple[str, Any]] = []
        for label, symbol, source_exchange in broadtape_contract_candidates(
            provider_codes
        ):
            candidate = Contract(
                symbol=symbol,
                secType="NEWS",
                exchange=source_exchange,
            )
            try:
                details = self.ib.reqContractDetails(candidate)
            except TimeoutError:
                details = []
                self.audit.emit(
                    "broadtape_contract_timeout",
                    provider=label,
                    symbol=candidate.symbol,
                )
            if not details:
                self.audit.emit(
                    "broadtape_contract_missing",
                    provider=label,
                    symbol=candidate.symbol,
                )
                continue
            contract = details[0].contract
            # ContractDetails normalizes NEWS contracts to exchange="NEWS".
            # reqMktData is stricter and requires the source exchange instead.
            contract.exchange = source_exchange
            found.append((label, contract))
            self.audit.emit(
                "broadtape_contract_found",
                provider=label,
                symbol=contract.symbol,
                exchange=contract.exchange,
                con_id=int(contract.conId),
            )
        return found

    def subscribe_broadtape(self, provider: str, contract: Any) -> SubscriptionState:
        started_at = utc_now()
        ticker = self.ib.reqMktData(
            contract,
            genericTickList="mdoff,292",
            snapshot=False,
            regulatorySnapshot=False,
        )
        req_id = int(self.ib.wrapper.ticker2ReqId["mktData"][ticker])
        state = SubscriptionState(
            req_id=req_id,
            name=f"all:{provider}",
            symbol="ALL",
            kind="broadtape",
            provider_codes=provider,
            contract=contract,
            started_at=started_at,
        )
        source_exchange = str(contract.exchange).upper()
        visible = set(self.visible_provider_codes)
        entitled = (
            source_exchange in visible
            or (source_exchange == "DJ" and "DJ-N" in visible)
            or (
                source_exchange == "DJTOP"
                and any(code.startswith("DJ-RT") for code in visible)
            )
        )
        if not entitled:
            state.status = "NOT_ENTITLED"
        self.subscriptions[req_id] = state
        self.audit.emit("subscription_requested", **state.public_dict())
        if not entitled:
            self.audit.emit(
                "subscription_entitlement_missing",
                subscription=state.name,
                source_exchange=source_exchange,
                visible_provider_codes=sorted(visible),
            )
            print(
                f"[NOT_ENTITLED] {state.name}: {source_exchange} is absent "
                "from reqNewsProviders()",
                flush=True,
            )
        return state

    def _on_tick_news(
        self,
        req_id: int,
        timestamp: int,
        provider_code: str,
        article_id: str,
        headline: str,
        extra_data: str,
    ) -> None:
        received_at = utc_now()
        published_at = news_epoch_to_utc(timestamp)
        state = self.subscriptions.get(req_id)
        if state is None:
            self.audit.emit(
                "orphan_tick_news",
                req_id=req_id,
                provider=provider_code,
                article_id=article_id,
                headline=headline,
            )
            return

        article_key = f"{provider_code}:{article_id}"
        classification, reason = classify_delivery(
            article_key=article_key,
            published_at=published_at,
            received_at=received_at,
            subscription_started_at=state.started_at,
            baseline_article_keys=state.baseline_article_keys,
            seen_article_keys=state.seen_article_keys,
            warmup_seconds=self.warmup_seconds,
        )
        state.callbacks += 1
        state.first_callback_at = state.first_callback_at or received_at
        state.last_callback_at = received_at
        state.last_published_at = published_at
        if classification != "DUPLICATE":
            state.unique_callbacks += 1
            state.seen_article_keys.add(article_key)
        if classification == "LIVE":
            state.live_callbacks += 1
            state.last_live_at = received_at
            state.status = "VERIFIED_LIVE"
        elif classification == "SNAPSHOT":
            state.snapshot_callbacks += 1
            if state.status == "PENDING":
                state.status = "RECEIVING_SNAPSHOT_ONLY"
        elif classification == "BACKFILL":
            state.backfill_callbacks += 1
            if state.status == "PENDING":
                state.status = "RECEIVING_BACKFILL_ONLY"
        elif classification == "WARMUP":
            state.warmup_callbacks += 1
            if state.status == "PENDING":
                state.status = "RECEIVING_WARMUP"
        elif classification == "DUPLICATE":
            state.duplicate_callbacks += 1
        else:
            state.unverified_callbacks += 1

        self.audit.emit(
            "tick_news",
            req_id=req_id,
            subscription=state.name,
            symbol=state.symbol,
            provider=provider_code,
            article_id=article_id,
            headline=headline,
            extra_data=extra_data,
            published_at=iso(published_at),
            received_at=iso(received_at),
            classification=classification,
            classification_reason=reason,
        )
        print(
            f"[NEWS][{classification}][{state.name}] "
            f"published={iso(published_at)} {provider_code}:{article_id} {headline}",
            flush=True,
        )

        if classification == "LIVE" and self.live_sink is not None:
            self.live_sink(
                symbol=state.symbol,
                timestamp=iso(published_at) or str(timestamp),
                provider=provider_code,
                article_id=article_id,
                headline=headline,
                ticker_id=req_id,
                extra_data=extra_data,
            )

    def _on_error(
        self,
        req_id: int,
        error_code: int,
        error_string: str,
        contract: Any | None,
    ) -> None:
        if error_code in IB_STATUS_CODES:
            status_key = error_string.rsplit(":", 1)[-1].strip() or str(error_code)
            status = {
                "code": error_code,
                "message": error_string,
                "recorded_at": iso(utc_now()),
            }
            self.ib_status[status_key] = status
            self.audit.emit("ib_status", status_key=status_key, **status)
            print_key = (error_code, error_string)
            if print_key not in self._printed_ib_status:
                self._printed_ib_status.add(print_key)
                print(
                    f"[IB_STATUS] code={error_code} {error_string}",
                    flush=True,
                )
            return
        error = {
            "req_id": req_id,
            "code": error_code,
            "message": error_string,
            "contract": getattr(contract, "symbol", "") if contract else "",
        }
        state = self.subscriptions.get(req_id)
        if state is not None:
            state.errors.append(error)
            if error_code in REJECTED_IB_CODES:
                state.status = "REJECTED"
            elif state.status != "VERIFIED_LIVE":
                state.status = "ERROR"
            self.audit.emit("subscription_error", subscription=state.name, **error)
        else:
            self.discovery_errors.append(error)
            self.audit.emit("ib_error", **error)
        print(
            f"[IB_ERROR] reqId={req_id} code={error_code} {error_string}",
            flush=True,
        )

    def poll_history(
        self,
        symbol: str,
        *,
        gap_grace_seconds: float = 60.0,
    ) -> dict[str, Any]:
        state = next(
            (
                item
                for item in self.subscriptions.values()
                if item.kind == "stock" and item.symbol == symbol
            ),
            None,
        )
        if state is None:
            return {"symbol": symbol, "status": "NO_SUBSCRIPTION"}
        rows = self.fetch_history(
            symbol=symbol,
            con_id=int(state.contract.conId),
            provider_codes=state.provider_codes,
        )
        previous = self._history_known.setdefault(symbol, set())
        now = utc_now()
        new_rows = [row for row in rows if row["key"] not in previous]
        confirmed = 0
        pending = 0
        gaps = 0
        for row in new_rows:
            key = row["key"]
            published_at = (
                datetime.fromisoformat(row["published_at"])
                if row["published_at"]
                else None
            )
            if published_at and published_at < state.started_at - timedelta(seconds=2):
                continue
            if key in state.seen_article_keys:
                confirmed += 1
                self._history_pending_gaps.pop((symbol, key), None)
                continue
            gap_key = (symbol, key)
            if gap_key not in self._history_reported_gaps:
                self._history_pending_gaps.setdefault(gap_key, now)

        for gap_key, first_missing in list(self._history_pending_gaps.items()):
            pending_symbol, article_key = gap_key
            if pending_symbol != symbol:
                continue
            if article_key in state.seen_article_keys:
                confirmed += 1
                self._history_pending_gaps.pop(gap_key, None)
                continue
            if (now - first_missing).total_seconds() >= gap_grace_seconds:
                gaps += 1
                state.history_gap_count += 1
                self._history_reported_gaps.add(gap_key)
                self._history_pending_gaps.pop(gap_key, None)
            else:
                pending += 1
        previous.update(row["key"] for row in rows)
        if gaps and state.status != "REJECTED":
            state.status = "DEGRADED_HISTORY_GAP"
        result = {
            "symbol": symbol,
            "status": "GAP" if gaps else "OK",
            "new_historical": len(new_rows),
            "stream_confirmed": confirmed,
            "pending_delivery": pending,
            "gaps": gaps,
        }
        self.audit.emit("historical_cross_check", **result)
        return result

    def heartbeat(self) -> dict[str, Any]:
        self._heartbeat_index += 1
        now = utc_now()
        for state in self.subscriptions.values():
            if (
                state.status == "RECEIVING_WARMUP"
                and (now - state.started_at).total_seconds() >= self.warmup_seconds
            ):
                state.status = "RECEIVING_BACKFILL_ONLY"
        server_time: datetime | None = None
        heartbeat_error = ""
        try:
            server_time = self.ib.reqCurrentTime()
            if server_time.tzinfo is None:
                server_time = server_time.replace(tzinfo=timezone.utc)
        except Exception as exc:
            heartbeat_error = f"{type(exc).__name__}: {exc}"
        live_total = sum(item.live_callbacks for item in self.subscriptions.values())
        result = {
            "index": self._heartbeat_index,
            "connected": bool(self.ib.isConnected()),
            "client_ready": bool(self.ib.client.isReady()),
            "server_time": iso(server_time),
            "heartbeat_error": heartbeat_error,
            "subscriptions": len(self.subscriptions),
            "live_total": live_total,
            "new_live_since_last_heartbeat": live_total
            - self._last_heartbeat_live_total,
            "states": [item.public_dict() for item in self.subscriptions.values()],
            "ib_status": self.ib_status,
        }
        self._last_heartbeat_live_total = live_total
        self.audit.emit("heartbeat", **result)
        print(
            "[HEARTBEAT] "
            f"server={result['server_time'] or '<failed>'} "
            f"connected={result['connected']} "
            f"new_live={result['new_live_since_last_heartbeat']} "
            f"live_total={live_total}",
            flush=True,
        )
        for state in self.subscriptions.values():
            print(
                f"  {state.name}: {state.status} callbacks={state.callbacks} "
                f"live={state.live_callbacks} snapshot={state.snapshot_callbacks} "
                f"warmup={state.warmup_callbacks} backfill={state.backfill_callbacks} "
                f"duplicate={state.duplicate_callbacks} "
                f"last_live={iso(state.last_live_at) or '-'}",
                flush=True,
            )
        return result

    def run(
        self,
        *,
        seconds: float,
        heartbeat_seconds: float,
        history_audit_symbol: str = "",
        history_poll_seconds: float = 0,
        gap_grace_seconds: float = 60.0,
        stop_on_live: bool = False,
    ) -> None:
        deadline = utc_now() + timedelta(seconds=seconds)
        next_heartbeat = utc_now()
        next_history_poll = (
            utc_now() + timedelta(seconds=history_poll_seconds)
            if history_audit_symbol and history_poll_seconds > 0
            else None
        )
        while utc_now() < deadline:
            if stop_on_live and any(
                item.live_callbacks > 0 for item in self.subscriptions.values()
            ):
                self.audit.emit("stop_on_live_satisfied")
                print("[VERIFICATION] stop_on_live satisfied", flush=True)
                return
            now = utc_now()
            if now >= next_heartbeat:
                self.heartbeat()
                next_heartbeat = now + timedelta(seconds=heartbeat_seconds)
            if next_history_poll and now >= next_history_poll:
                result = self.poll_history(
                    history_audit_symbol,
                    gap_grace_seconds=gap_grace_seconds,
                )
                print(f"[HISTORY_AUDIT] {result}", flush=True)
                next_history_poll = now + timedelta(seconds=history_poll_seconds)
            remaining = max(0.0, (deadline - utc_now()).total_seconds())
            self.ib.sleep(min(0.25, remaining))

    def stop(self) -> None:
        summary = [item.public_dict() for item in self.subscriptions.values()]
        self.audit.emit("session_end", subscriptions=summary)
        for state in list(self.subscriptions.values()):
            try:
                self.ib.cancelMktData(state.contract)
            except Exception:
                pass
        if self.ib.isConnected():
            self.ib.disconnect()
