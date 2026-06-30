from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
from ib_async import IB

from target_treasury_account_monitor.live_option_chain import discover_near_expiry_fop_contracts
from target_treasury_account_monitor.option_chain_view import snapshot_to_monitor_frame
from treasury_fop_chain import (
    FOPMarketDataStreamer,
    append_flow_events_sqlite,
    compute_volume_delta_events,
)

from .settings import LiveChainSettings


@dataclass
class LiveChainSnapshot:
    """One fast read from persistent option-chain subscriptions."""

    raw_snapshot: pd.DataFrame
    monitor_frame: pd.DataFrame
    flow_events: pd.DataFrame
    readiness: Any
    output_path: Path | None


class LiveChainMonitor:
    """Persistent near-expiry FOP monitor.

    The expensive part is creating subscriptions. After `start()`, each
    `snapshot()` only reads the already-live ticker objects, so refreshes are
    much faster than repeatedly doing batch snapshots.
    """

    def __init__(self, ib: IB, settings: LiveChainSettings) -> None:
        self.ib = ib
        self.settings = settings
        self.discovery: dict[str, Any] | None = None
        self.streamer: FOPMarketDataStreamer | None = None
        self.previous_snapshot: pd.DataFrame | None = None

    def start(self) -> dict[str, Any]:
        """Discover focused contracts, subscribe once, and wait for initial data."""
        self.discovery = discover_near_expiry_fop_contracts(
            self.ib,
            root=self.settings.root,
            future_months=self.settings.future_months,
            market_data_type=None,
            max_dte=self.settings.max_dte,
            max_expirations=self.settings.max_expirations,
            strikes_each_side=self.settings.strikes_each_side,
            strike_width=self.settings.strike_width,
            qualify_batch_size=self.settings.qualify_batch_size,
        )
        self.streamer = FOPMarketDataStreamer(
            self.ib,
            request_interval=self.settings.request_interval,
        )
        self.streamer.subscribe(self.discovery["contracts"])
        self.streamer.wait_until_stable(
            max_seconds=self.settings.warmup_seconds,
            stable_seconds=self.settings.stable_seconds,
        )
        return self.discovery

    def snapshot(self) -> LiveChainSnapshot:
        """Read current quotes/Greeks/OI/volume from active subscriptions."""
        if self.streamer is None:
            self.start()
        assert self.streamer is not None

        readiness = self.streamer.readiness()
        raw = self.streamer.snapshot()
        monitor_frame = snapshot_to_monitor_frame(raw, root=self.settings.root)
        events = compute_volume_delta_events(
            raw,
            self.previous_snapshot,
            min_delta=self.settings.min_volume_delta,
        )
        self.previous_snapshot = raw.copy()

        if self.settings.output_path is not None:
            self.settings.output_path.parent.mkdir(parents=True, exist_ok=True)
            raw.to_csv(self.settings.output_path, index=False, encoding="utf-8-sig")
        if self.settings.flow_db_path is not None and not events.empty:
            append_flow_events_sqlite(events, self.settings.flow_db_path)

        return LiveChainSnapshot(
            raw_snapshot=raw,
            monitor_frame=monitor_frame,
            flow_events=events,
            readiness=readiness,
            output_path=self.settings.output_path,
        )

    def run_forever(self, *, max_iterations: int | None = None) -> None:
        """Console loop for unattended monitoring."""
        self.start()
        iteration = 0
        while max_iterations is None or iteration < max_iterations:
            iteration += 1
            snap = self.snapshot()
            print(
                f"[{pd.Timestamp.now(tz='Asia/Shanghai'):%Y-%m-%d %H:%M:%S}] "
                f"rows={len(snap.raw_snapshot)} "
                f"quote={snap.readiness.quote_ready}/{snap.readiness.requested} "
                f"greeks={snap.readiness.greek_ready}/{snap.readiness.requested} "
                f"events={len(snap.flow_events)} "
                f"saved={snap.output_path or ''}"
            )
            self.ib.sleep(self.settings.poll_seconds)

    def close(self) -> None:
        """Cancel active market-data subscriptions."""
        if self.streamer is not None:
            self.streamer.cancel()
            self.streamer = None

    def __enter__(self) -> "LiveChainMonitor":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

