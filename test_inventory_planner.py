from __future__ import annotations

import unittest

from target_treasury_monitor_clean.inventory_planner import (
    PlannerConfig,
    aggregate_inventory,
    dte_bucket,
    node_exposure,
    parse_short_positions,
    portfolio_exposure,
    scan_candidates,
    stress_scenarios,
    target_pressure,
)


class InventoryPlannerTests(unittest.TestCase):
    def config(self) -> PlannerConfig:
        return PlannerConfig(
            capital=20_000,
            monthly_target_return=0.10,
            month_to_date_realized_pnl=500,
            remaining_trading_days=15,
            put_strike_zone={"ZF": (106.0, 107.0), "ZN": (109.0, 110.5)},
            call_strike_zone={"ZF": (108.0, 109.0), "ZN": (111.0, 112.5)},
        )

    def positions(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": "ZF",
                "secType": "FOP",
                "position": "-2",
                "expiry": "20260703",
                "strike": "106.5",
                "right": "P",
                "marketValue": "-120",
                "unrealizedPnL": "20",
                "delta": "-0.18",
                "gamma": "0.08",
                "theta": "-0.02",
                "vega": "0.01",
                "multiplier": "1000",
                "localSymbol": "ZF1N6 P1065",
            },
            {
                "symbol": "ZF",
                "secType": "FOP",
                "position": "1",
                "expiry": "20260703",
                "strike": "105.5",
                "right": "P",
                "marketValue": "20",
                "delta": "-0.08",
                "gamma": "0.04",
                "theta": "-0.01",
                "vega": "0.01",
            },
            {
                "symbol": "ZN",
                "secType": "FOP",
                "position": "-1",
                "expiry": "20260706",
                "strike": "111.5",
                "right": "C",
                "marketValue": "-80",
                "unrealizedPnL": "-5",
                "delta": "0.12",
                "gamma": "0.05",
                "theta": "-0.01",
                "vega": "0.02",
                "localSymbol": "OZNN6 C1115",
            },
            {
                "symbol": "ZN",
                "secType": "FUT",
                "position": "-1",
                "expiry": "20260706",
                "strike": "111.5",
                "right": "C",
            },
        ]

    def chain(self) -> list[dict[str, object]]:
        return [
            {
                "symbol": "ZF",
                "expiration": "20260704",
                "dte": "1",
                "strike": "106.75",
                "right": "P",
                "bid": "0.020",
                "ask": "0.024",
                "mid": "0.022",
                "delta": "-0.12",
                "gamma": "0.05",
                "theta": "-0.01",
                "vega": "0.01",
                "volume": "10",
                "openInterest": "50",
                "conId": "p1",
            },
            {
                "symbol": "ZF",
                "expiration": "20260704",
                "dte": "1",
                "strike": "108.25",
                "right": "C",
                "bid": "0.010",
                "ask": "0.013",
                "mid": "0.0115",
                "delta": "0.08",
                "gamma": "0.03",
                "theta": "-0.007",
                "vega": "0.01",
                "volume": "6",
                "openInterest": "44",
                "conId": "c1",
            },
            {
                "symbol": "ZF",
                "expiration": "20260703",
                "dte": "0",
                "strike": "106.5",
                "right": "P",
                "bid": "0.05",
                "ask": "0.06",
                "mid": "0.055",
                "delta": "-0.30",
                "gamma": "0.20",
                "theta": "-0.04",
                "vega": "0.02",
                "conId": "zero-dte",
            },
            {
                "symbol": "ZN",
                "expiration": "20260707",
                "dte": "4",
                "strike": "111.25",
                "right": "C",
                "bid": "0.030",
                "ask": "0.036",
                "mid": "0.033",
                "delta": "0.16",
                "gamma": "0.04",
                "theta": "-0.009",
                "vega": "0.01",
                "conId": "znc",
            },
        ]

    def test_short_only_filter_ignores_long_options_and_non_options(self) -> None:
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")

        self.assertEqual(len(parsed), 2)
        self.assertTrue(all(position.position < 0 for position in parsed))
        self.assertEqual({position.underlying for position in parsed}, {"ZF", "ZN"})

    def test_zc_short_option_is_kept_by_default_parser(self) -> None:
        rows = [
            {
                "symbol": "ZC",
                "secType": "FOP",
                "position": "-1",
                "expiry": "20260710",
                "strike": "425",
                "right": "P",
                "marketValue": "-250",
                "delta": "-0.20",
                "gamma": "0.01",
                "theta": "-0.02",
                "vega": "0.03",
                "localSymbol": "ZC2N6 P0425",
            }
        ]

        parsed = parse_short_positions(rows, PlannerConfig(), as_of="20260707")

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].underlying, "ZC")
        self.assertEqual(parsed[0].strike, 425)

    def test_zc_decimal_strike_is_normalized_to_cents(self) -> None:
        rows = [
            {
                "symbol": "ZC",
                "secType": "FOP",
                "position": "-1",
                "expiry": "20260710",
                "strike": "4.25",
                "right": "P",
                "marketValue": "-31",
                "delta": "-0.09",
                "localSymbol": "ZC2N6 P0425",
            }
        ]

        parsed = parse_short_positions(rows, PlannerConfig(), as_of="20260708")

        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0].strike, 425)

    def test_legacy_zc_estimated_value_is_rescaled_from_cents(self) -> None:
        rows = [
            {
                "symbol": "ZC",
                "secType": "FOP",
                "position": "-1",
                "expiry": "20260717",
                "strike": "4.25",
                "right": "P",
                "price": "0.625",
                "marketValue": "-3125",
                "valueSource": "estimated_from_market",
                "multiplier": "5000",
                "avgCost": "34.48",
                "costBasis": "-34.48",
            }
        ]

        parsed = parse_short_positions(rows, PlannerConfig(), as_of="20260714")

        self.assertEqual(parsed[0].multiplier, 50)
        self.assertEqual(parsed[0].remaining_premium, 31.25)
        self.assertAlmostEqual(parsed[0].unrealized_pnl, 3.23)

    def test_zc_portfolio_value_with_contract_multiplier_is_rescaled(self) -> None:
        rows = [
            {
                "symbol": "ZC",
                "secType": "FOP",
                "position": "-1",
                "expiry": "20260717",
                "strike": "4.25",
                "right": "P",
                "price": "0.625",
                "marketValue": "-3125",
                "valueSource": "portfolio",
                "contractMultiplier": "5000",
                "multiplier": "50",
                "costBasis": "-34.48",
            }
        ]

        parsed = parse_short_positions(rows, PlannerConfig(), as_of="20260714")

        self.assertEqual(parsed[0].remaining_premium, 31.25)
        self.assertAlmostEqual(parsed[0].unrealized_pnl, 3.23)

    def test_dte_bucket_assignment_does_not_make_0dte_special_risk(self) -> None:
        self.assertEqual(dte_bucket(0), "0DTE")
        self.assertEqual(dte_bucket(2), "1-2DTE")
        self.assertEqual(dte_bucket(4), "3-4DTE")
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")

        self.assertEqual(parsed[0].dte, 0)

    def test_inventory_aggregation_separates_side_dte_expiry_and_nodes(self) -> None:
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")
        inventory = aggregate_inventory(parsed)

        self.assertEqual(inventory["totalContracts"], 3)
        self.assertEqual({row["key"] for row in inventory["bySide"]}, {"P", "C"})
        self.assertIn("0DTE", {row["key"] for row in inventory["byDteBucket"]})
        self.assertTrue(any(row["key"].startswith("ZF-P-106.5") for row in inventory["byStrikeNode"]))

    def test_target_pressure_is_indicator_not_trade_requirement(self) -> None:
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")
        pressure = target_pressure(parsed, self.config())

        self.assertEqual(pressure["monthlyTargetProfit"], 2000)
        self.assertEqual(pressure["remainingTarget"], 1500)
        self.assertIn(pressure["targetPressureLabel"], {"LOW", "NORMAL", "ELEVATED", "HIGH"})

    def test_candidate_scanner_enforces_new_trade_windows_and_scores_not_only_premium(self) -> None:
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")
        candidates = scan_candidates(self.chain(), parsed, self.config(), as_of="20260703")

        self.assertNotIn("zero-dte", {candidate.candidate_id for candidate in candidates})
        self.assertIn("p1", {candidate.candidate_id for candidate in candidates})
        self.assertIn("c1", {candidate.candidate_id for candidate in candidates})
        self.assertTrue(all(candidate.final_score != candidate.income_score for candidate in candidates))

    def test_before_after_exposure_recomputes_after_manual_quantities(self) -> None:
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")
        candidates = scan_candidates(self.chain(), parsed, self.config(), as_of="20260703")
        selected = {candidates[0].candidate_id: 2}
        exposure = portfolio_exposure(parsed, candidates, selected, self.config())

        self.assertGreater(exposure["added"]["totalRemainingPremium"], 0)
        self.assertNotEqual(exposure["before"]["netDelta"], exposure["after"]["netDelta"])

    def test_node_exposure_warns_but_keeps_concentrated_nodes(self) -> None:
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")
        candidates = scan_candidates(self.chain(), parsed, self.config(), as_of="20260703")
        selected = {candidates[0].candidate_id: 1}
        nodes = node_exposure(parsed, candidates, selected)

        self.assertTrue(nodes)
        self.assertTrue(any("warnings" in node for node in nodes))
        self.assertTrue(any(node["totalContractsAfterAdjustment"] > node["currentContracts"] for node in nodes))

    def test_stress_scenarios_returns_worst_node_and_contributions(self) -> None:
        parsed = parse_short_positions(self.positions(), self.config(), as_of="20260703")
        scenarios = stress_scenarios(parsed, config=self.config())

        self.assertEqual(len(scenarios), 8)
        self.assertIn("worstNode", scenarios[0])
        self.assertIn("deltaContribution", scenarios[0])
        self.assertIn("gammaContribution", scenarios[0])


if __name__ == "__main__":
    unittest.main()
