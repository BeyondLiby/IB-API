from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
from ib_async import IB
from ib_async.ib import StartupFetch

from target_treasury_account_monitor.carry_view import account_positions_frame
from target_treasury_account_monitor.config import MonitorSettings
from target_treasury_account_monitor.frames import positions_to_frame
from target_treasury_account_monitor.greeks import greek_totals
from target_treasury_account_monitor.ib_client import (
    account_summary_frame,
    cancel_tickers,
    fetch_target_positions,
    get_future_reference,
    managed_accounts,
    portfolio_items_by_key,
    refresh_account_portfolio,
    subscribe_quotes_for_positions,
)
from target_treasury_account_monitor.spreads import add_empty_spread_columns, pair_vertical_spreads

from .ib_session import ib_connection
from .settings import AccountDashboardSettings, IBSettings


@dataclass
class AccountDashboardSnapshot:
    """All frames needed to render or export one account dashboard refresh."""

    visible_accounts: list[str]
    treasury_positions: list[Any]
    all_positions: list[Any]
    position_frame: pd.DataFrame
    spread_summary: pd.DataFrame
    account_summary: pd.DataFrame
    account_positions: pd.DataFrame
    greek_summary: pd.DataFrame
    future_reference: dict[str, Any]
    tickers: dict[int, Any]


def _legacy_monitor_settings(
    ib_settings: IBSettings,
    dashboard_settings: AccountDashboardSettings,
) -> MonitorSettings:
    """Build the older settings object used by the reusable low-level helpers."""
    return MonitorSettings(
        host=ib_settings.host,
        port=ib_settings.port,
        client_id=ib_settings.client_id,
        account=ib_settings.account,
        market_data_type=ib_settings.market_data_type,
        quote_wait_seconds=dashboard_settings.quote_wait_seconds,
        refresh_seconds=5,
        auto_refresh=False,
        auto_reconnect=False,
        reconnect_backoff_seconds=10,
        wechat_webhook_url="",
        wechat_push_enabled=False,
        wechat_min_interval_seconds=300,
        infer_spreads=dashboard_settings.infer_spreads,
    )


def fetch_account_dashboard(
    ib: IB,
    ib_settings: IBSettings,
    dashboard_settings: AccountDashboardSettings | None = None,
    *,
    previous_tickers: dict[int, Any] | None = None,
) -> AccountDashboardSnapshot:
    """Fetch account positions, live quotes, PnL, Greeks, and summary metrics."""
    dashboard_settings = dashboard_settings or AccountDashboardSettings()
    settings = _legacy_monitor_settings(ib_settings, dashboard_settings)

    if previous_tickers:
        cancel_tickers(ib, previous_tickers)

    visible_accounts = managed_accounts(ib)
    treasury_positions, all_positions = fetch_target_positions(ib, ib_settings.account)
    future_ref = get_future_reference(
        ib,
        treasury_positions,
        settings,
        root=dashboard_settings.reference_root,
    )
    tickers = subscribe_quotes_for_positions(ib, treasury_positions, settings)
    refresh_account_portfolio(ib, ib_settings.account)
    portfolio_map = portfolio_items_by_key(ib, ib_settings.account)

    position_frame = positions_to_frame(
        treasury_positions,
        tickers,
        portfolio_map,
        reference_price=float(future_ref.get("price", float("nan"))),
    )
    if dashboard_settings.infer_spreads:
        position_frame, spread_summary = pair_vertical_spreads(position_frame)
    else:
        position_frame = add_empty_spread_columns(position_frame)
        spread_summary = pd.DataFrame()

    account_summary = account_summary_frame(ib, ib_settings.account)
    all_position_frame = account_positions_frame(all_positions, portfolio_map)
    greek_summary = greek_totals(position_frame)

    return AccountDashboardSnapshot(
        visible_accounts=visible_accounts,
        treasury_positions=treasury_positions,
        all_positions=all_positions,
        position_frame=position_frame,
        spread_summary=spread_summary,
        account_summary=account_summary,
        account_positions=all_position_frame,
        greek_summary=greek_summary,
        future_reference=future_ref,
        tickers=tickers,
    )


def fetch_account_dashboard_once(
    ib_settings: IBSettings,
    dashboard_settings: AccountDashboardSettings | None = None,
) -> AccountDashboardSnapshot:
    """Convenience wrapper for scripts that need one complete account refresh."""
    fetch_fields = StartupFetch.POSITIONS | StartupFetch.ACCOUNT_UPDATES | StartupFetch.SUB_ACCOUNT_UPDATES
    with ib_connection(ib_settings, fetch_fields=fetch_fields) as ib:
        snapshot = fetch_account_dashboard(ib, ib_settings, dashboard_settings)
        cancel_tickers(ib, snapshot.tickers)
        return snapshot

