from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from target_treasury_monitor_clean.cli import (
    _format_carry_html_summary,
    _raise_if_carry_html_not_ready,
    build_parser,
    command_sync_carry_html,
    command_sync_latest_carry_html,
    command_validate_carry_html,
)
from target_treasury_monitor_clean.carry_dashboard_sync import (
    BARS_COLUMNS,
    _read_tabular_input,
    _row_count_by_product,
    discover_latest_carry_dashboard_inputs,
    sync_carry_dashboard_files,
    validate_carry_dashboard_files,
    write_carry_dashboard_files,
)
from target_treasury_monitor_clean.future_bars import _parse_bar_datetime, future_local_symbol, local_symbol_future_contract


class CarryDashboardSyncTests(unittest.TestCase):
    def test_reads_pandas_html_table_and_filters_ellipsis_row(self) -> None:
        html = """
        <table class="dataframe">
          <thead>
            <tr><th></th><th>symbol</th><th>expiry</th><th>strike</th></tr>
          </thead>
          <tbody>
            <tr><th>0</th><td>ZF</td><td>20260701</td><td>107</td></tr>
            <tr><th>...</th><td>...</td><td>...</td><td>...</td></tr>
            <tr><th>1</th><td>ZN</td><td>20260702</td><td>111</td></tr>
          </tbody>
        </table>
        """
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "table.txt"
            path.write_text(html, encoding="utf-8")

            frame = _read_tabular_input(path)

        self.assertEqual(frame["symbol"].tolist(), ["ZF", "ZN"])
        self.assertEqual(frame.shape, (2, 3))

    def test_product_counts_fall_back_to_option_name_and_local_symbol(self) -> None:
        frame = pd.DataFrame(
            [
                {"localSymbol": "GF5M6 P1065", "optionName": "ZF-20260630-106.5-P"},
                {"localSymbol": "OZNN6 C1110", "optionName": ""},
                {"localSymbol": "ZFU6", "optionName": ""},
                {"localSymbol": "ZNU6", "optionName": ""},
                {"localSymbol": "OZCU6 P420", "optionName": ""},
                {"localSymbol": "ZCU6", "optionName": ""},
            ]
        )

        self.assertEqual(_row_count_by_product(frame), {"ZC": 2, "ZF": 2, "ZN": 2})

    def test_discovers_latest_notebook_outputs_by_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "clean_verify"
            output_dir = root / "data"
            input_dir.mkdir()
            output_dir.mkdir()
            positions = input_dir / "dashboard_treasury_positions.csv"
            bars = output_dir / "carry_dashboard_bars.csv"
            positions.write_text("symbol,position\nZF,1\n", encoding="utf-8")
            bars.write_text("symbol,date,open,high,low,close\nZF,2026-07-03,1,1,1,1\n", encoding="utf-8")
            old_zf = input_dir / "ZF_FOP_Static_202609_202612_from_20260701_to_all_monitor_frame.csv"
            new_zf = input_dir / "ZF_FOP_Static_202609_202612_from_20260703_to_all_monitor_frame.csv"
            zn = input_dir / "ZN_FOP_Static_202609_202612_from_20260703_to_all_monitor_frame.csv"
            zc = input_dir / "ZC_FOP_Static_202609_from_20260703_to_all_monitor_frame.csv"
            for path in [old_zf, new_zf, zn, zc]:
                path.write_text("symbol,expiry,snapshotTimeUtc\n", encoding="utf-8")
            old_zf.touch()
            new_zf.touch()
            zn.touch()
            zc.touch()

            found = discover_latest_carry_dashboard_inputs(input_dir, output_dir=output_dir, products="ZF,ZN,ZC")

        self.assertEqual(found["positions"], positions)
        self.assertEqual(found["bars"], bars)
        self.assertEqual(found["expected_products"], "ZC,ZF,ZN")
        self.assertIn(str(new_zf), found["chain_arg"])
        self.assertIn(str(zn), found["chain_arg"])
        self.assertIn(str(zc), found["chain_arg"])

    def test_sync_creates_empty_bars_file_with_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions = root / "positions.csv"
            chain = root / "chain.csv"
            out = root / "out"
            pd.DataFrame([{"symbol": "ZF", "position": 1}]).to_csv(positions, index=False)
            pd.DataFrame([{"symbol": "ZF", "expiry": "20260701"}]).to_csv(chain, index=False)

            paths = sync_carry_dashboard_files(positions, chain, output_dir=out)
            bars = pd.read_csv(paths["bars"])

            self.assertEqual(list(bars.columns), BARS_COLUMNS)
            self.assertTrue(bars.empty)

    def test_sync_fills_missing_product_from_input_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions = root / "positions.csv"
            chain = root / "ZN_chain.csv"
            out = root / "out"
            pd.DataFrame([{"symbol": "ZN", "position": 1}]).to_csv(positions, index=False)
            pd.DataFrame([{"localSymbol": "OZNN6 C1110", "expiry": "20260701"}]).to_csv(chain, index=False)

            paths = sync_carry_dashboard_files(positions, chain, output_dir=out)
            synced = pd.read_csv(paths["chain"])

            self.assertEqual(synced["product"].tolist(), ["ZN"])
            self.assertEqual(_row_count_by_product(synced), {"ZN": 1})

    def test_sync_filename_product_does_not_override_conflicting_row_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions = root / "positions.csv"
            chain = root / "ZF_chain.csv"
            out = root / "out"
            pd.DataFrame([{"symbol": "ZN", "position": 1}]).to_csv(positions, index=False)
            pd.DataFrame([{"symbol": "ZN", "localSymbol": "ZNU6", "expiry": "20260701"}]).to_csv(chain, index=False)

            paths = sync_carry_dashboard_files(positions, chain, output_dir=out)
            synced = pd.read_csv(paths["chain"])

            self.assertEqual(_row_count_by_product(synced), {"ZN": 1})
            if "product" in synced.columns:
                self.assertNotEqual(synced["product"].iloc[0], "ZF")

    def test_write_creates_empty_bars_file_with_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            paths = write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZF", "position": 1}]),
                pd.DataFrame([{"symbol": "ZF", "expiry": "20260701"}]),
                output_dir=root,
            )
            bars = pd.read_csv(paths["bars"])

            self.assertEqual(list(bars.columns), BARS_COLUMNS)
            self.assertTrue(bars.empty)

    def test_validate_uses_thresholds_for_partial_chain_and_bars(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZF", "position": 1}, {"symbol": "ZN", "position": 1}]),
                pd.DataFrame([{"symbol": "ZF", "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T00:00:00+00:00"} for _ in range(10)]),
                bars=pd.DataFrame([{"symbol": "ZF", "date": "2026-07-01", "open": 1, "high": 1, "low": 1, "close": 1}]),
                output_dir=root,
            )

            report = validate_carry_dashboard_files(
                root,
                expected_products="ZF,ZN",
                min_chain_rows=50,
                min_bars_rows=100,
                as_of="2026-07-01T01:00:00+00:00",
            )

        self.assertEqual(report["product_status"]["ZF"]["chain_view"], "partial_chain")
        self.assertFalse(report["product_status"]["ZF"]["has_full_chain"])
        self.assertFalse(report["product_status"]["ZF"]["has_bars"])
        self.assertEqual(report["product_status"]["ZN"]["chain_view"], "position_fallback")
        self.assertEqual(report["readiness"]["missing_full_chain"], ["ZF", "ZN"])
        self.assertEqual(report["readiness"]["missing_bars"], ["ZF", "ZN"])

    def test_validate_marks_stale_chain_even_when_row_count_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZF", "position": 1}]),
                pd.DataFrame(
                    [{"symbol": "ZF", "expiry": "20260701", "snapshotTimeUtc": "2026-06-30T00:00:00+00:00"} for _ in range(60)]
                ),
                output_dir=root,
            )

            report = validate_carry_dashboard_files(
                root,
                expected_products="ZF",
                min_chain_rows=50,
                max_chain_age_hours=24,
                as_of="2026-07-02T00:00:00+00:00",
            )

        self.assertEqual(report["product_status"]["ZF"]["chain_view"], "stale_chain")
        self.assertFalse(report["product_status"]["ZF"]["has_full_chain"])
        self.assertEqual(report["readiness"]["missing_full_chain"], ["ZF"])

    def test_validate_chain_expirations_accepts_expiration_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZN", "position": 1}]),
                pd.DataFrame(
                    [{"symbol": "ZN", "expiration": "20260707", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"} for _ in range(60)]
                ),
                output_dir=root,
            )

            report = validate_carry_dashboard_files(
                root,
                expected_products="ZN",
                min_chain_rows=50,
                as_of="2026-07-02T00:00:00+00:00",
            )

        self.assertEqual(report["chain_expirations"]["ZN"], ["20260707"])
        self.assertTrue(report["product_status"]["ZN"]["has_full_chain"])

    def test_validate_marks_stale_bars_even_when_row_count_is_high(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZF", "position": 1}]),
                pd.DataFrame(
                    [{"symbol": "ZF", "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"} for _ in range(60)]
                ),
                bars=pd.DataFrame(
                    [{"symbol": "ZF", "date": "2026-06-28T00:00:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1} for _ in range(120)]
                ),
                output_dir=root,
            )

            report = validate_carry_dashboard_files(
                root,
                expected_products="ZF",
                min_chain_rows=50,
                min_bars_rows=100,
                max_bars_age_hours=72,
                as_of="2026-07-02T00:00:00+00:00",
            )

        self.assertTrue(report["product_status"]["ZF"]["has_full_chain"])
        self.assertFalse(report["product_status"]["ZF"]["bars_fresh"])
        self.assertFalse(report["product_status"]["ZF"]["has_bars"])
        self.assertEqual(report["readiness"]["missing_bars"], ["ZF"])

    def test_validate_uses_date_when_date_china_column_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZF", "position": 1}]),
                pd.DataFrame(
                    [{"symbol": "ZF", "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"} for _ in range(60)]
                ),
                bars=pd.DataFrame(
                    [{"symbol": "ZF", "date": "2026-07-01T23:30:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1} for _ in range(120)]
                ),
                output_dir=root,
            )

            report = validate_carry_dashboard_files(
                root,
                expected_products="ZF",
                min_chain_rows=50,
                min_bars_rows=100,
                as_of="2026-07-02T00:00:00+00:00",
            )

        self.assertTrue(report["product_status"]["ZF"]["has_bars"])
        self.assertTrue(report["readiness"]["ready_for_full_zf_zn_view"])

    def test_validate_ready_for_full_zf_zn_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions = pd.DataFrame([{"symbol": "ZF", "position": 1}, {"symbol": "ZN", "position": 1}])
            chain = pd.DataFrame(
                [
                    {"symbol": product, "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"}
                    for product in ["ZF", "ZN"]
                    for _ in range(60)
                ]
            )
            bars = pd.DataFrame(
                [
                    {"symbol": product, "date": "2026-07-01T23:30:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1}
                    for product in ["ZF", "ZN"]
                    for _ in range(120)
                ]
            )
            write_carry_dashboard_files(positions, chain, bars=bars, output_dir=root)

            report = validate_carry_dashboard_files(
                root,
                expected_products="ZF,ZN",
                min_chain_rows=50,
                min_bars_rows=100,
                as_of="2026-07-02T00:00:00+00:00",
            )

        self.assertTrue(report["readiness"]["ready_for_full_zf_zn_view"])
        self.assertEqual(report["readiness"]["missing_full_chain"], [])
        self.assertEqual(report["readiness"]["missing_bars"], [])

    def test_validate_cli_require_ready_allows_complete_zf_zn_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions = pd.DataFrame([{"symbol": "ZF", "position": 1}, {"symbol": "ZN", "position": 1}])
            chain = pd.DataFrame(
                [
                    {"symbol": product, "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"}
                    for product in ["ZF", "ZN"]
                    for _ in range(60)
                ]
            )
            bars = pd.DataFrame(
                [
                    {"symbol": product, "date": "2026-07-01T23:30:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1}
                    for product in ["ZF", "ZN"]
                    for _ in range(120)
                ]
            )
            write_carry_dashboard_files(positions, chain, bars=bars, output_dir=root)
            args = build_parser().parse_args(
                [
                    "validate-carry-html",
                    "--data-dir",
                    str(root),
                    "--expected-products",
                    "ZF,ZN",
                    "--as-of",
                    "2026-07-02T00:00:00+00:00",
                    "--require-ready",
                ]
            )

            with contextlib.redirect_stdout(io.StringIO()):
                command_validate_carry_html(args)

    def test_sync_cli_require_ready_allows_complete_zf_zn_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions_path = root / "positions.csv"
            chain_path = root / "chain.csv"
            bars_path = root / "bars.csv"
            out = root / "out"
            pd.DataFrame([{"symbol": "ZF", "position": 1}, {"symbol": "ZN", "position": 1}]).to_csv(positions_path, index=False)
            pd.DataFrame(
                [
                    {"symbol": product, "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"}
                    for product in ["ZF", "ZN"]
                    for _ in range(60)
                ]
            ).to_csv(chain_path, index=False)
            pd.DataFrame(
                [
                    {"symbol": product, "date": "2026-07-01T23:30:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1}
                    for product in ["ZF", "ZN"]
                    for _ in range(120)
                ]
            ).to_csv(bars_path, index=False)
            args = build_parser().parse_args(
                [
                    "sync-carry-html",
                    "--positions",
                    str(positions_path),
                    "--chain",
                    str(chain_path),
                    "--bars",
                    str(bars_path),
                    "--output-dir",
                    str(out),
                    "--expected-products",
                    "ZF,ZN",
                    "--as-of",
                    "2026-07-02T00:00:00+00:00",
                    "--summary-only",
                    "--require-ready",
                ]
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                command_sync_carry_html(args)

        self.assertIn("ready_for_full_zf_zn_view: true", stdout.getvalue())

    def test_sync_latest_cli_publishes_latest_notebook_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "clean_verify"
            out = root / "data"
            input_dir.mkdir()
            out.mkdir()
            positions = input_dir / "dashboard_treasury_positions.csv"
            positions_df = pd.DataFrame([{"symbol": "ZF", "position": 1}, {"symbol": "ZC", "position": 1}])
            chain_df = pd.DataFrame(
                [
                    {"symbol": product, "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"}
                    for product in ["ZF", "ZC"]
                    for _ in range(60)
                ]
            )
            bars_df = pd.DataFrame(
                [
                    {"symbol": product, "date": "2026-07-01T23:30:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1}
                    for product in ["ZF", "ZC"]
                    for _ in range(120)
                ]
            )
            positions_df.to_csv(positions, index=False)
            chain_df[chain_df["symbol"] == "ZF"].to_csv(input_dir / "ZF_FOP_Static_202609_from_20260701_to_all_monitor_frame.csv", index=False)
            chain_df[chain_df["symbol"] == "ZC"].to_csv(input_dir / "ZC_FOP_Static_202609_from_20260701_to_all_monitor_frame.csv", index=False)
            bars_df.to_csv(out / "carry_dashboard_bars.csv", index=False)
            args = build_parser().parse_args(
                [
                    "sync-latest-carry-html",
                    "--input-dir",
                    str(input_dir),
                    "--output-dir",
                    str(out),
                    "--products",
                    "ZF,ZN,ZC",
                    "--expected-products",
                    "ZF,ZC",
                    "--as-of",
                    "2026-07-02T00:00:00+00:00",
                    "--summary-only",
                    "--require-ready",
                ]
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                command_sync_latest_carry_html(args)

            output = stdout.getvalue()
            self.assertIn("positions input:", output)
            self.assertIn("ZC_FOP_Static", output)
            self.assertIn("ready_for_full_zf_zn_view: true", output)
            self.assertEqual(set(pd.read_csv(out / "carry_dashboard_chain.csv")["symbol"]), {"ZF", "ZC"})

    def test_bars_schema_accepts_datetime_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZN", "position": 1}]),
                pd.DataFrame(
                    [{"symbol": "ZN", "expiry": "20260701", "snapshotTimeUtc": "2026-07-01T23:00:00+00:00"} for _ in range(60)]
                ),
                bars=pd.DataFrame(
                    [{"symbol": "ZN", "datetime": "2026-07-01T23:30:00+00:00", "open": 1, "high": 1, "low": 1, "close": 1} for _ in range(120)]
                ),
                output_dir=root,
            )
            bars = pd.read_csv(root / "carry_dashboard_bars.csv")

            report = validate_carry_dashboard_files(
                root,
                expected_products="ZN",
                min_chain_rows=50,
                min_bars_rows=100,
                as_of="2026-07-02T00:00:00+00:00",
            )

        self.assertEqual(bars["date"].iloc[0], "2026-07-01T23:30:00+00:00")
        self.assertTrue(report["product_status"]["ZN"]["has_bars"])

    def test_validate_cli_require_ready_exits_nonzero_when_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZF", "position": 1}]),
                pd.DataFrame(),
                output_dir=root,
            )
            args = build_parser().parse_args(
                [
                    "validate-carry-html",
                    "--data-dir",
                    str(root),
                    "--expected-products",
                    "ZF",
                    "--require-ready",
                ]
            )

            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as context:
                command_validate_carry_html(args)

        self.assertNotEqual(context.exception.code, 0)
        self.assertIn("not ready", str(context.exception))

    def test_sync_cli_require_ready_exits_nonzero_when_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            positions_path = root / "positions.csv"
            chain_path = root / "chain.csv"
            out = root / "out"
            pd.DataFrame([{"symbol": "ZF", "position": 1}]).to_csv(positions_path, index=False)
            pd.DataFrame().to_csv(chain_path, index=False)
            args = build_parser().parse_args(
                [
                    "sync-carry-html",
                    "--positions",
                    str(positions_path),
                    "--chain",
                    str(chain_path),
                    "--output-dir",
                    str(out),
                    "--expected-products",
                    "ZF",
                    "--require-ready",
                ]
            )

            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as context:
                command_sync_carry_html(args)

        self.assertNotEqual(context.exception.code, 0)
        self.assertIn("not ready", str(context.exception))

    def test_validate_cli_summary_only_prints_concise_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_carry_dashboard_files(
                pd.DataFrame([{"symbol": "ZF", "position": 1}]),
                pd.DataFrame(),
                output_dir=root,
            )
            args = build_parser().parse_args(
                [
                    "validate-carry-html",
                    "--data-dir",
                    str(root),
                    "--expected-products",
                    "ZF",
                    "--summary-only",
                ]
            )

            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                command_validate_carry_html(args)

        output = stdout.getvalue()
        self.assertIn("ready_for_full_zf_zn_view: false", output)
        self.assertIn("ZF: chain=position_fallback", output)
        self.assertIn("missing_bars: ZF", output)

    def test_summary_formatter_includes_missing_items(self) -> None:
        text = _format_carry_html_summary(
            {
                "readiness": {
                    "ready_for_full_zf_zn_view": False,
                    "missing_full_chain": ["ZF"],
                    "missing_bars": ["ZN"],
                },
                "product_status": {
                    "ZF": {"chain_view": "stale_chain", "chain": 60, "bars": 0, "has_bars": False, "has_bars_rows": False},
                    "ZN": {"chain_view": "standard_chain", "chain": 60, "bars": 120, "has_bars": True},
                },
            }
        )

        self.assertIn("ZF: chain=stale_chain rows=60; bars=missing rows=0", text)
        self.assertIn("ZN: chain=standard_chain rows=60; bars=ready rows=120", text)
        self.assertIn("missing_full_chain: ZF", text)
        self.assertIn("missing_bars: ZN", text)

    def test_refresh_cli_accepts_require_ready_flag(self) -> None:
        args = build_parser().parse_args(
            [
                "refresh-carry-html",
                "--require-ready",
                "--zc-chain-specs",
                "ZC=202609",
                "--min-chain-rows",
                "75",
                "--min-bars-rows",
                "120",
                "--max-chain-age-hours",
                "12",
                "--max-bars-age-hours",
                "48",
            ]
        )

        self.assertTrue(args.require_ready)
        self.assertEqual(args.zc_chain_specs, "ZC=202609")
        self.assertEqual(args.min_chain_rows, 75)
        self.assertEqual(args.min_bars_rows, 120)
        self.assertEqual(args.max_chain_age_hours, 12)
        self.assertEqual(args.max_bars_age_hours, 48)

    def test_ready_gate_formats_missing_chain_and_bars(self) -> None:
        report = {
            "readiness": {
                "ready_for_full_zf_zn_view": False,
                "missing_full_chain": ["ZF", "ZN"],
                "missing_bars": ["ZN"],
            }
        }

        with self.assertRaises(SystemExit) as context:
            _raise_if_carry_html_not_ready(report)

        self.assertIn("missing full chain: ZF, ZN", str(context.exception))
        self.assertIn("missing bars: ZN", str(context.exception))


class FutureBarsTests(unittest.TestCase):
    def test_parse_ib_intraday_bar_datetime_with_double_space(self) -> None:
        timestamp = _parse_bar_datetime("20260702  09:30:00")

        self.assertEqual(timestamp.year, 2026)
        self.assertEqual(timestamp.month, 7)
        self.assertEqual(timestamp.day, 2)
        self.assertEqual(timestamp.hour, 9)
        self.assertEqual(timestamp.minute, 30)
        self.assertIsNotNone(timestamp.tzinfo)

    def test_parse_ib_daily_bar_date_attaches_timezone(self) -> None:
        timestamp = _parse_bar_datetime("20260702")

        self.assertEqual(timestamp.date().isoformat(), "2026-07-02")
        self.assertIsNotNone(timestamp.tzinfo)

    def test_parse_aware_iso_bar_datetime_preserves_timezone(self) -> None:
        timestamp = _parse_bar_datetime("2026-07-02T14:30:00+00:00")

        self.assertEqual(timestamp.tz_convert("UTC").hour, 14)

    def test_future_local_symbol_from_root_and_month(self) -> None:
        self.assertEqual(future_local_symbol("ZF", "202609"), "ZFU6")
        self.assertEqual(future_local_symbol("ZN", "202612"), "ZNZ6")

    def test_local_symbol_contract_fields(self) -> None:
        contract = local_symbol_future_contract("ZN", "202609")

        self.assertEqual(contract.symbol, "ZN")
        self.assertEqual(contract.secType, "FUT")
        self.assertEqual(contract.exchange, "CBOT")
        self.assertEqual(contract.localSymbol, "ZNU6")
        self.assertEqual(contract.lastTradeDateOrContractMonth, "202609")


if __name__ == "__main__":
    unittest.main()
