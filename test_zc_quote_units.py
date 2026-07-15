from __future__ import annotations

import math
from types import SimpleNamespace
import unittest

import pandas as pd

from target_treasury_account_monitor.frames import positions_to_frame
from target_treasury_account_monitor.option_chain_view import snapshot_to_monitor_frame


class ZcQuoteUnitTests(unittest.TestCase):
    def test_estimated_zc_position_value_uses_dollars_per_cent(self) -> None:
        contract = SimpleNamespace(
            conId=1,
            symbol="ZC",
            localSymbol="ZC3N6 P0425",
            secType="FOP",
            lastTradeDateOrContractMonth="20260717",
            strike=4.25,
            right="P",
            exchange="CBOT",
            currency="USD",
            multiplier="5000",
        )
        position = SimpleNamespace(contract=contract, position=-1, avgCost=34.48, account="U1")
        portfolio_item = SimpleNamespace(
            marketPrice=0.625,
            marketValue=math.nan,
            unrealizedPNL=math.nan,
            realizedPNL=math.nan,
        )

        row = positions_to_frame([position], {}, {1: portfolio_item}).iloc[0]

        self.assertEqual(row["contractMultiplier"], 5000.0)
        self.assertEqual(row["multiplier"], 50.0)
        self.assertEqual(row["marketValue"], -31.25)
        self.assertAlmostEqual(row["unrealizedPnL"], 3.23)

    def test_zc_chain_rows_use_the_same_cash_multiplier(self) -> None:
        snapshot = pd.DataFrame(
            [{"expiration": "20260717", "strike": 425, "right": "P", "bid": 0.5, "ask": 0.75, "multiplier": 5000}]
        )

        row = snapshot_to_monitor_frame(snapshot, root="ZC").iloc[0]

        self.assertEqual(row["multiplier"], 50.0)


if __name__ == "__main__":
    unittest.main()
