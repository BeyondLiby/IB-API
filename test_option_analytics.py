from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from target_treasury_monitor_clean.chain_batch import (
    ANALYTICS_GENERIC_TICKS,
    _select_market_data_contracts,
    _snapshot_selected_market_data,
)
from target_treasury_monitor_clean.option_analytics import (
    prepare_option_analytics_snapshot,
    update_option_analytics_history,
)
from target_treasury_monitor_clean.settings import StaticChainSettings


class OptionAnalyticsTests(unittest.TestCase):
    def sample(self, *, snapshot: str = "2026-07-23T04:00:00Z", iv: float = 0.12) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "snapshotTimeUtc": snapshot,
                    "conId": 101,
                    "symbol": "ZN",
                    "expiration": "20260807",
                    "dte": 15,
                    "right": "C",
                    "strike": 109.0,
                    "modelGreeks_undPrice": 108.5,
                    "modelGreeks_impliedVol": iv,
                    "modelGreeks_delta": 0.42,
                    "volume": 0,
                    "openInterest": pd.NA,
                    "bid": 0.2,
                    "ask": 0.25,
                    "analyticsSample": True,
                },
                {
                    "snapshotTimeUtc": snapshot,
                    "conId": 102,
                    "symbol": "ZN",
                    "expiration": "20260807",
                    "dte": 15,
                    "right": "P",
                    "strike": 108.0,
                    "modelGreeks_undPrice": 108.5,
                    "modelGreeks_impliedVol": 0.13,
                    "analyticsSample": False,
                },
            ]
        )

    def test_normalizes_bounded_sample_and_preserves_missing_oi(self) -> None:
        result = prepare_option_analytics_snapshot(self.sample())
        self.assertEqual(len(result), 1)
        self.assertAlmostEqual(result.iloc[0]["iv"], 0.12)
        self.assertTrue(pd.isna(result.iloc[0]["openInterest"]))
        self.assertTrue(pd.isna(result.iloc[0]["volume"]))
        self.assertFalse(bool(result.iloc[0]["liquidityTicksRequested"]))
        self.assertAlmostEqual(result.iloc[0]["moneynessPct"], (109 / 108.5 - 1) * 100)

    def test_daily_upsert_replaces_same_contract_but_keeps_prior_day(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.csv"
            update_option_analytics_history(self.sample(iv=0.12), path)
            update_option_analytics_history(self.sample(iv=0.14), path)
            update_option_analytics_history(
                self.sample(snapshot="2026-07-24T04:00:00Z", iv=0.15),
                path,
            )
            result = pd.read_csv(path)
        self.assertEqual(len(result), 2)
        self.assertEqual(result["snapshotDate"].tolist(), ["2026-07-23", "2026-07-24"])
        self.assertAlmostEqual(result.iloc[0]["iv"], 0.14)
        self.assertAlmostEqual(result.iloc[1]["iv"], 0.15)

    def test_market_data_selection_unions_bounded_surface_sample(self) -> None:
        today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
        rows = []
        contracts = []
        con_id = 1
        for days in (1, 7, 30):
            expiration = (today + pd.Timedelta(days=days)).strftime("%Y%m%d")
            for strike in range(94, 107):
                for right in ("C", "P"):
                    rows.append(
                        {
                            "conId": con_id,
                            "underlyingMonth": "202609",
                            "expiration": expiration,
                            "strike": float(strike),
                            "right": right,
                        }
                    )
                    contracts.append(type("Contract", (), {"conId": con_id})())
                    con_id += 1
        metadata = pd.DataFrame(rows)
        prices = pd.DataFrame([{"month": "202609", "price": 100.0}])
        settings = StaticChainSettings(
            root="ZN",
            future_months="202609",
            near_strike_width=0.1,
            far_strike_width=0.1,
            analytics_strikes_each_side=2,
            analytics_max_expirations=3,
        )
        selected_contracts, selected = _select_market_data_contracts(contracts, metadata, prices, settings)
        analytics = selected[selected["analyticsSample"]]
        self.assertEqual(len(selected_contracts), len(selected))
        self.assertEqual(analytics["expiration"].nunique(), 3)
        self.assertEqual(analytics.groupby("expiration")["strike"].nunique().max(), 5)
        self.assertTrue((analytics["strike"] - 100).abs().max() <= 2)
        self.assertTrue(analytics["liquidityTicksRequested"].all())

    def test_liquidity_generic_ticks_only_apply_to_analytics_sample(self) -> None:
        contracts = [SimpleNamespace(conId=value) for value in (1, 2, 3)]
        metadata = pd.DataFrame(
            [
                {"conId": 1, "analyticsSample": False},
                {"conId": 2, "analyticsSample": True},
                {"conId": 3, "analyticsSample": False},
            ]
        )
        settings = StaticChainSettings(root="ZF", batch_size=10)

        def fake_snapshot(_ib: object, selected: list[object], **kwargs: object) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "conId": contract.conId,
                        "genericTicks": kwargs["generic_ticks"],
                    }
                    for contract in selected
                ]
            )

        with patch(
            "target_treasury_monitor_clean.chain_batch.snapshot_in_batches",
            side_effect=fake_snapshot,
        ) as snapshot:
            result = _snapshot_selected_market_data(object(), contracts, metadata, settings)

        self.assertEqual(snapshot.call_count, 2)
        standard_call, analytics_call = snapshot.call_args_list
        self.assertEqual([contract.conId for contract in standard_call.args[1]], [1, 3])
        self.assertEqual(standard_call.kwargs["generic_ticks"], "")
        self.assertEqual(standard_call.kwargs["stability_fields"], ("quote", "greeks"))
        self.assertEqual([contract.conId for contract in analytics_call.args[1]], [2])
        self.assertEqual(analytics_call.kwargs["generic_ticks"], ANALYTICS_GENERIC_TICKS)
        self.assertEqual(
            analytics_call.kwargs["stability_fields"],
            ("quote", "greeks", "oi", "volume"),
        )
        self.assertEqual(set(result["conId"].astype(int)), {1, 2, 3})

    def test_zc_underlying_price_is_normalized_to_display_cents(self) -> None:
        row = self.sample().iloc[[0]].copy()
        row["symbol"] = "ZC"
        row["strike"] = 455.0
        row["modelGreeks_undPrice"] = 4.50
        result = prepare_option_analytics_snapshot(row)
        self.assertAlmostEqual(result.iloc[0]["underlyingPrice"], 450.0)
        self.assertAlmostEqual(result.iloc[0]["moneynessPct"], (455 / 450 - 1) * 100)


if __name__ == "__main__":
    unittest.main()
