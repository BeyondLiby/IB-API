from __future__ import annotations

from types import SimpleNamespace
import unittest

from ib_async import Contract

from target_treasury_monitor_clean.margin_whatif import (
    MarginWhatIfError,
    MarginWhatIfRequest,
    build_margin_whatif_order,
    margin_whatif_result,
    read_margin_account_snapshot,
    run_margin_whatif,
)


class FakeIB:
    def __init__(self) -> None:
        self.order = None
        self.contract = None

    def accountSummary(self, account: str):
        return [
            SimpleNamespace(account=account, tag="InitMarginReq", value="1,000.00", currency="USD"),
            SimpleNamespace(account=account, tag="MaintMarginReq", value="800.00", currency="USD"),
            SimpleNamespace(account=account, tag="AvailableFunds", value="5,000.00", currency="USD"),
            SimpleNamespace(account=account, tag="ExcessLiquidity", value="4,200.00", currency="USD"),
            SimpleNamespace(account=account, tag="LookAheadAvailableFunds", value="4,700.00", currency="USD"),
        ]

    def qualifyContracts(self, contract: Contract):
        contract.conId = 12345
        contract.localSymbol = "ZNU6 C10925"
        return [contract]

    def whatIfOrder(self, contract: Contract, order):
        self.contract = contract
        self.order = order
        return SimpleNamespace(
            initMarginBefore="1000",
            initMarginChange="250",
            initMarginAfter="1250",
            maintMarginBefore="800",
            maintMarginChange="200",
            maintMarginAfter="1000",
            equityWithLoanBefore="6000",
            equityWithLoanChange="-10",
            equityWithLoanAfter="5990",
            warningText="",
        )


class MarginWhatIfTests(unittest.TestCase):
    def test_snapshot_reads_relevant_account_values(self) -> None:
        snapshot = read_margin_account_snapshot(FakeIB(), "U16251798")

        self.assertEqual(snapshot.initial_margin, 1000.0)
        self.assertEqual(snapshot.available_funds, 5000.0)
        self.assertEqual(snapshot.look_ahead_available_funds, 4700.0)
        self.assertEqual(snapshot.currency, "USD")

    def test_market_and_limit_orders_are_validated(self) -> None:
        request = MarginWhatIfRequest(contract=Contract(conId=1), action="buy", quantity=2)
        order = build_margin_whatif_order(request, "U16251798")
        self.assertEqual(order.orderType, "MKT")
        self.assertEqual(order.account, "U16251798")

        with self.assertRaises(MarginWhatIfError):
            build_margin_whatif_order(
                MarginWhatIfRequest(contract=Contract(conId=1), action="SELL", quantity=1, order_type="LMT"),
                "U16251798",
            )

    def test_result_reports_portfolio_margin_change_and_linear_capacity(self) -> None:
        ib = FakeIB()
        result = run_margin_whatif(
            ib,
            "U16251798",
            MarginWhatIfRequest(contract=Contract(conId=12345, exchange="CBOT"), action="SELL", quantity=2),
        )

        self.assertEqual(ib.order.whatIf, False)
        self.assertEqual(ib.order.action, "SELL")
        self.assertEqual(result.initial_margin_change, 250.0)
        self.assertEqual(result.initial_margin_released, 0.0)
        self.assertEqual(result.maintenance_margin_change, 200.0)
        self.assertEqual(result.estimated_available_funds_change, -260.0)
        self.assertEqual(result.estimated_available_funds_after, 4740.0)
        self.assertEqual(result.linear_max_quantity_estimate, 38)
        self.assertEqual(result.contract_label, "ZNU6 C10925")

    def test_release_is_positive_when_a_closing_trade_reduces_margin(self) -> None:
        snapshot = read_margin_account_snapshot(FakeIB(), "U16251798")
        state = SimpleNamespace(
            initMarginBefore="1000",
            initMarginChange="-400",
            initMarginAfter="600",
            maintMarginBefore="800",
            maintMarginChange="-300",
            maintMarginAfter="500",
            equityWithLoanBefore="6000",
            equityWithLoanChange="0",
            equityWithLoanAfter="6000",
            warningText="",
        )
        result = margin_whatif_result(
            MarginWhatIfRequest(contract=Contract(conId=9), action="BUY", quantity=1),
            snapshot,
            Contract(conId=9, localSymbol="ZN close"),
            state,
        )

        self.assertEqual(result.initial_margin_released, 400.0)
        self.assertEqual(result.maintenance_margin_released, 300.0)
        self.assertEqual(result.estimated_available_funds_change, 400.0)
        self.assertIsNone(result.linear_max_quantity_estimate)


if __name__ == "__main__":
    unittest.main()
