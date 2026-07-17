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

    def test_zc_portfolio_value_with_raw_contract_multiplier_is_normalized(self) -> None:
        contract = SimpleNamespace(
            conId=2,
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
            marketValue=-3125.0,
            unrealizedPNL=-3090.52,
            realizedPNL=math.nan,
        )

        row = positions_to_frame([position], {}, {2: portfolio_item}).iloc[0]

        self.assertEqual(row["marketValue"], -31.25)
        self.assertEqual(row["valueSource"], "normalized_from_portfolio")
        self.assertAlmostEqual(row["unrealizedPnL"], 3.23)

    def test_negative_ib_option_price_sentinel_uses_nonnegative_quote(self) -> None:
        contract = SimpleNamespace(
            conId=4,
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
            marketPrice=-100.0,
            marketValue=5000.0,
            unrealizedPNL=5034.48,
            realizedPNL=math.nan,
        )
        ticker = SimpleNamespace(
            bid=math.nan,
            ask=0.125,
            last=-100.0,
            markPrice=math.nan,
            close=0.125,
            delayedBid=math.nan,
            delayedAsk=math.nan,
            delayedLast=math.nan,
            delayedClose=math.nan,
            modelGreeks=None,
            lastGreeks=None,
            askGreeks=None,
            bidGreeks=None,
            marketPrice=lambda: -100.0,
        )

        row = positions_to_frame([position], {4: ticker}, {4: portfolio_item}).iloc[0]

        self.assertEqual(row["price"], 0.125)
        self.assertEqual(row["priceSource"], "close")
        self.assertEqual(row["marketValue"], -6.25)
        self.assertAlmostEqual(row["unrealizedPnL"], 28.23)

    def test_zc_futures_portfolio_value_is_not_option_normalized(self) -> None:
        contract = SimpleNamespace(
            conId=3,
            symbol="ZC",
            localSymbol="ZCU6",
            secType="FUT",
            lastTradeDateOrContractMonth="202609",
            strike=0.0,
            right="",
            exchange="CBOT",
            currency="USD",
            multiplier="5000",
        )
        position = SimpleNamespace(contract=contract, position=1, avgCost=0.0, account="U1")
        portfolio_item = SimpleNamespace(
            marketPrice=450.5,
            marketValue=2252500.0,
            unrealizedPNL=100.0,
            realizedPNL=math.nan,
        )

        row = positions_to_frame([position], {}, {3: portfolio_item}).iloc[0]

        self.assertEqual(row["marketValue"], 2252500.0)
        self.assertEqual(row["valueSource"], "portfolio")


if __name__ == "__main__":
    unittest.main()
