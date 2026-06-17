from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import pandas as pd

from .config import MonitorSettings
from .frames import positions_to_frame
from .ib_client import (
    account_summary_frame,
    fetch_target_positions,
    managed_accounts,
    portfolio_items_by_key,
    refresh_account_portfolio,
)

QuoteLoader = Callable[[list[Any]], dict[int, Any]]


@dataclass(frozen=True)
class TreasurySnapshot:
    """一次账户快照：包含原始持仓、清洗后的表格和账户资金摘要。"""

    positions: list[Any]
    all_positions: list[Any]
    frame: pd.DataFrame
    summary: pd.DataFrame
    accounts: list[str]
    updated_at: pd.Timestamp

    @property
    def excluded_count(self) -> int:
        """非美债持仓数量，仅用于页面审计提示。"""
        return max(len(self.all_positions) - len(self.positions), 0)


def build_snapshot(ib: Any, settings: MonitorSettings, quote_loader: QuoteLoader) -> TreasurySnapshot:
    """按固定顺序拉取账户快照，避免 UI 和测试脚本各自拼装数据。"""
    accounts = managed_accounts(ib)
    positions, all_positions = fetch_target_positions(ib, settings.account)
    tickers = quote_loader(positions)
    refresh_account_portfolio(ib, settings.account)
    frame = positions_to_frame(positions, tickers, portfolio_items_by_key(ib, settings.account))
    summary = account_summary_frame(ib, settings.account)
    return TreasurySnapshot(
        positions=positions,
        all_positions=all_positions,
        frame=frame,
        summary=summary,
        accounts=accounts,
        updated_at=pd.Timestamp.now(tz="Asia/Shanghai"),
    )
