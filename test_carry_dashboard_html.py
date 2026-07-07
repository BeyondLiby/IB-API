from __future__ import annotations

import re
import unittest
from pathlib import Path


HTML_PATH = Path(__file__).with_name("carry_risk_dashboard.html")


class CarryDashboardHtmlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")

    def test_fixed_target_controls_replace_editable_target_inputs(self) -> None:
        self.assertIn("const FIXED_TARGET_RETURN = 10;", self.html)
        self.assertIn("const FIXED_CAPITAL_BASE = 20000;", self.html)
        self.assertIn("$2,000", self.html)
        self.assertNotIn('id="targetReturn"', self.html)
        self.assertNotIn('id="capitalBase"', self.html)
        self.assertNotIn('id="harvestThreshold"', self.html)
        self.assertNotIn("harvestThreshold", self.html)

    def test_product_tabs_and_sample_data_cover_zf_zn_and_zc(self) -> None:
        self.assertIn('id="productTabs"', self.html)
        self.assertIn("function renderProductTabs()", self.html)
        self.assertIn("const candidates = [", self.html)
        self.assertIn("raw.optionName", self.html)
        self.assertIn("raw.description", self.html)
        self.assertIn("/^OZN/.test(text)", self.html)
        self.assertIn("/^OZC/.test(text)", self.html)
        self.assertIn("[FGHJKMNQUVXZ]", self.html)
        self.assertIn("match[1] || match[2] || match[3]", self.html)
        self.assertNotIn("raw.localSymbol || raw.optionName", self.html)
        self.assertRegex(self.html, r'for \(const product of \["ZF", "ZN", "ZC"\]\)')
        self.assertIn('symbol: "ZF"', self.html)
        self.assertIn('symbol: "ZN"', self.html)
        self.assertIn('symbol: "ZC"', self.html)
        self.assertIn("const snapshotTimeUtc = new Date().toISOString();", self.html)
        self.assertIn("snapshotTimeUtc", self.html)

    def test_kline_panel_is_above_decision_cards(self) -> None:
        data_status = self.html.index("<h2>数据覆盖</h2>")
        kline = self.html.index("<h2>期货走势</h2>")
        income_map = self.html.index("<h2>收益地图</h2>")
        sell_matrix = self.html.index("<h2>目标缺口与补卖矩阵</h2>")
        self.assertLess(data_status, kline)
        self.assertLess(kline, income_map)
        self.assertLess(kline, sell_matrix)
        self.assertIn('id="klineChart"', self.html)
        self.assertIn("function renderKlineChart(rows)", self.html)
        self.assertIn("30 分钟K线", self.html)
        self.assertIn("31 * 48", self.html)
        self.assertIn("K线过期，最新约", self.html)
        self.assertIn("样本不足，低于", self.html)
        self.assertIn("MAX_BARS_AGE_HOURS", self.html)
        self.assertIn("MIN_BARS_ROWS", self.html)

    def test_option_chain_cross_board_has_required_quote_and_greek_columns(self) -> None:
        self.assertIn('id="optionChainBoard"', self.html)
        self.assertIn("function renderOptionChainBoard(chain, source = \"chain\")", self.html)
        self.assertIn("raw.expiry || raw.expiration || raw.lastTradeDateOrContractMonth", self.html)
        self.assertIn("function firstNum(...values)", self.html)
        self.assertIn("raw.callOpenInterest", self.html)
        self.assertIn("raw.putOpenInterest", self.html)
        self.assertIn("raw.rawOpenInterest", self.html)
        self.assertIn("raw.callVolume", self.html)
        self.assertIn("raw.putVolume", self.html)
        self.assertIn("raw.rawVolume", self.html)
        self.assertIn("raw.impliedVolatility", self.html)
        self.assertIn("raw.modelGreeks_impliedVol", self.html)
        self.assertIn("raw.mid", self.html)
        self.assertIn("raw.markPrice", self.html)
        self.assertIn("raw.modelOptionPrice", self.html)
        self.assertIn("raw.optPrice", self.html)
        self.assertIn("raw.modelGreeks_optPrice", self.html)
        self.assertIn("raw.bidGreeks_optPrice", self.html)
        self.assertIn("raw.askGreeks_optPrice", self.html)
        self.assertIn("const last = firstNum(raw.last, raw.close", self.html)
        self.assertIn("raw.modelGreeks_delta", self.html)
        self.assertIn("raw.bidGreeks_delta", self.html)
        self.assertIn("raw.askGreeks_delta", self.html)
        self.assertIn("raw.lastGreeks_delta", self.html)
        self.assertIn("raw.modelGreeks_theta", self.html)
        self.assertIn("raw.bidGreeks_theta", self.html)
        self.assertIn("raw.askGreeks_theta", self.html)
        self.assertIn("raw.lastGreeks_theta", self.html)
        self.assertIn("raw.modelGreeks_vega", self.html)
        for label in [
            "C Last",
            "C Bid",
            "C Ask",
            "C Delta",
            "C Gamma",
            "C IV",
            "C OI",
            "C Vol",
            "P Last",
            "P Bid",
            "P Ask",
            "P Delta",
            "P Gamma",
            "P IV",
            "P OI",
            "P Vol",
        ]:
            self.assertIn(label, self.html)

    def test_default_csv_inputs_are_loaded_with_cache_busting(self) -> None:
        for filename in [
            "data/carry_dashboard_positions.csv",
            "data/carry_dashboard_chain.csv",
            "data/carry_dashboard_bars.csv",
        ]:
            self.assertIn(filename, self.html)
        self.assertRegex(self.html, re.escape('fetch(`${path}?t=${Date.now()}`, { cache: "no-store" })'))


if __name__ == "__main__":
    unittest.main()
