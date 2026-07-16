from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pandas as pd

from target_treasury_monitor_clean import cli as clean_cli
from target_treasury_monitor_clean.cli import _product_filter_widths
from refresh_inventory_data import run_scheduled_refresh
from refresh_inventory_data import chain_eastern_dates_by_product
from refresh_inventory_data import select_effective_refresh_mode
from refresh_inventory_data import wait_for_planner_server
from refresh_inventory_data import write_refresh_status
from target_treasury_monitor_clean.chain_batch import _contract_cache_path
from target_treasury_monitor_clean.chain_batch import _find_existing_contract_cache
from target_treasury_monitor_clean.chain_batch import _load_cached_chain
from target_treasury_monitor_clean.chain_batch import _select_market_data_contracts
from target_treasury_monitor_clean.ib_client_lock import IbClientLockBusy
from target_treasury_monitor_clean.ib_client_lock import acquire_ib_client_lock
from target_treasury_monitor_clean.ib_client_lock import ib_client_lock_metadata_path
from target_treasury_monitor_clean.ib_client_lock import ib_client_lock_path
from target_treasury_monitor_clean.inventory_planner_server import inventory_planner_handler
from target_treasury_monitor_clean.inventory_planner_server import inventory_planner_manifest
from target_treasury_monitor_clean.inventory_planner_server import normalize_refresh_contract_months
from target_treasury_monitor_clean.inventory_planner_server import read_latest_refresh_status
from target_treasury_monitor_clean.inventory_planner_server import refresh_progress_from_output
from target_treasury_monitor_clean.inventory_planner_server import refresh_status_http_code
from target_treasury_monitor_clean.settings import IBSettings
from target_treasury_monitor_clean.settings import StaticChainSettings


class DummyProcess:
    def poll(self) -> None:
        return None


class QuietStaticHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return


class PlannerServerReadinessTests(unittest.TestCase):
    def start_server(self, handler: type[SimpleHTTPRequestHandler]):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return server

    def args_for(self, port: int) -> SimpleNamespace:
        return SimpleNamespace(planner_host="127.0.0.1", planner_port=port)

    def test_wait_for_planner_server_accepts_inventory_api(self) -> None:
        handler = inventory_planner_handler(Path.cwd())
        server = self.start_server(handler)

        wait_for_planner_server(self.args_for(server.server_address[1]), DummyProcess(), timeout=1.0)

    def test_manifest_includes_data_updated_at(self) -> None:
        manifest = inventory_planner_manifest(Path.cwd())

        self.assertIn("dataUpdatedAt", manifest)
        self.assertRegex(str(manifest["dataUpdatedAt"]), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$|^$")

    def test_manifest_exposes_product_future_prices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            planner = directory / "data" / "planner"
            debug = planner / "debug"
            debug.mkdir(parents=True)
            (planner / "carry_dashboard_positions.csv").write_text("symbol,position\nZC,-1\n", encoding="utf-8")
            (planner / "carry_dashboard_chain.csv").write_text("symbol,strike,right\nZC,425,P\n", encoding="utf-8")
            (debug / "ZC_FOP_Static_202609_from_20260707_to_all_future_prices.csv").write_text(
                "root,month,price\nZC,202609,438.5\n",
                encoding="utf-8",
            )

            manifest = inventory_planner_manifest(directory)

        self.assertEqual(
            manifest["products"]["ZC"]["futurePrices"],
            "data/planner/debug/ZC_FOP_Static_202609_from_20260707_to_all_future_prices.csv",
        )

    def test_inventory_refresh_post_starts_status_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            script = directory / "refresh_inventory_data.py"
            script.write_text("import sys\nprint('fake refresh', ' '.join(sys.argv[1:]))\nsys.exit(1)\n", encoding="utf-8")
            handler = inventory_planner_handler(directory)
            server = self.start_server(handler)
            url = f"http://127.0.0.1:{server.server_address[1]}/api/refresh-inventory-data"

            body = json.dumps({"mode": "full"}).encode("utf-8")
            response = urlopen(Request(url, data=body, method="POST", headers={"Content-Type": "application/json"}), timeout=5)
            self.assertEqual(response.status, 202)
            payload = json.loads(response.read().decode("utf-8"))
            self.assertTrue(payload["running"])
            self.assertIn("jobId", payload)

            status_url = f"{url}/status?job={payload['jobId']}"
            for _ in range(20):
                try:
                    status_response = urlopen(status_url, timeout=5)
                    payload = json.loads(status_response.read().decode("utf-8"))
                except HTTPError as exc:
                    payload = json.loads(exc.read().decode("utf-8"))
                if payload["running"] is False:
                    break
                time.sleep(0.1)

            self.assertFalse(payload["running"])
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["returncode"], 1)
            self.assertIn("fake refresh", payload["stdout"])
            self.assertIn("--refresh-mode full", payload["stdout"])
            self.assertGreaterEqual(payload["durationSeconds"], 0)
            self.assertEqual(payload["requestedMode"], "full")
            self.assertEqual(payload["effectiveMode"], "full")

    def test_inventory_refresh_post_reuses_running_latest_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            planner = directory / "data" / "planner"
            planner.mkdir(parents=True)
            (directory / "refresh_inventory_data.py").write_text(
                "raise SystemExit('should not start a second refresh')\n",
                encoding="utf-8",
            )
            (planner / "refresh_status.json").write_text(
                json.dumps({
                    "ok": None,
                    "running": True,
                    "started": "2026-07-10 10:31:00",
                    "returncode": None,
                    "progress": 42,
                    "stage": "已有刷新正在运行",
                }),
                encoding="utf-8",
            )
            handler = inventory_planner_handler(directory)
            server = self.start_server(handler)
            url = f"http://127.0.0.1:{server.server_address[1]}/api/refresh-inventory-data"

            response = urlopen(Request(url, data=b'{"mode":"fast"}', method="POST", headers={"Content-Type": "application/json"}), timeout=5)
            payload = json.loads(response.read().decode("utf-8"))

            self.assertEqual(response.status, 202)
            self.assertTrue(payload["running"])
            self.assertEqual(payload["jobId"], "latest")
            self.assertEqual(payload["progress"], 42)

    def test_inventory_refresh_post_forwards_selected_contract_months(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            script = directory / "refresh_inventory_data.py"
            script.write_text("import sys\nprint('ARGS', ' '.join(sys.argv[1:]))\n", encoding="utf-8")
            handler = inventory_planner_handler(directory)
            server = self.start_server(handler)
            url = f"http://127.0.0.1:{server.server_address[1]}/api/refresh-inventory-data"
            body = json.dumps({
                "mode": "fast",
                "contractMonths": {"ZF": "202609", "ZN": "202609", "ZC": "202609"},
            }).encode("utf-8")

            response = urlopen(Request(url, data=body, method="POST", headers={"Content-Type": "application/json"}), timeout=5)
            payload = json.loads(response.read().decode("utf-8"))
            status_url = f"{url}/status?job={payload['jobId']}"
            for _ in range(20):
                status_response = urlopen(status_url, timeout=5)
                payload = json.loads(status_response.read().decode("utf-8"))
                if payload["running"] is False:
                    break
                time.sleep(0.1)

            self.assertTrue(payload["ok"])
            self.assertIn("--chain-specs ZF=202609;ZN=202609", payload["stdout"])
            self.assertIn("--zc-chain-specs ZC=202609", payload["stdout"])
            self.assertEqual(payload["contractMonths"], {"ZF": "202609", "ZN": "202609", "ZC": "202609"})

    def test_contract_month_payload_rejects_invalid_or_incomplete_values(self) -> None:
        self.assertEqual(
            normalize_refresh_contract_months({"ZF": "2026-09", "ZN": "202609", "ZC": "202609"}),
            {"ZF": "202609", "ZN": "202609", "ZC": "202609"},
        )
        with self.assertRaisesRegex(ValueError, "missing ZC"):
            normalize_refresh_contract_months({"ZF": "202609", "ZN": "202609"})
        with self.assertRaisesRegex(ValueError, "invalid ZC"):
            normalize_refresh_contract_months({"ZF": "202609", "ZN": "202609", "ZC": "202613"})

    def test_recent_invalid_refresh_status_is_treated_as_in_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            planner = directory / "data" / "planner"
            planner.mkdir(parents=True)
            (planner / "refresh_status.json").write_text('{"running":', encoding="utf-8")

            payload = read_latest_refresh_status(directory)

        self.assertTrue(payload["running"])
        self.assertEqual(payload["stage"], "刷新状态写入中")

    def test_refresh_status_write_replaces_a_complete_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner = Path(tmp) / "planner"
            args = SimpleNamespace(html_data_dir=planner)
            write_refresh_status(args, {"ok": None, "running": True, "progress": 12})

            payload = json.loads((planner / "refresh_status.json").read_text(encoding="utf-8"))

        self.assertTrue(payload["running"])
        self.assertEqual(payload["progress"], 12)

    def test_refresh_status_write_retries_a_transient_windows_file_lock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner = Path(tmp) / "planner"
            args = SimpleNamespace(html_data_dir=planner)
            original_replace = os.replace
            attempts = 0

            def replace_with_two_lock_failures(source, target):
                nonlocal attempts
                attempts += 1
                if attempts < 3:
                    raise PermissionError("status file is temporarily open")
                original_replace(source, target)

            with (
                patch("refresh_inventory_data.os.replace", side_effect=replace_with_two_lock_failures),
                patch("refresh_inventory_data.time.sleep"),
            ):
                written = write_refresh_status(args, {"ok": None, "running": True, "progress": 12})

            payload = json.loads((planner / "refresh_status.json").read_text(encoding="utf-8"))

        self.assertTrue(written)
        self.assertEqual(attempts, 3)
        self.assertEqual(payload["progress"], 12)

    def test_lock_busy_refresh_status_is_not_reported_as_backend_500(self) -> None:
        message = (
            "IB client refresh is already running for 127.0.0.1:4001 "
            "with client-id 7316."
        )

        progress, stage = refresh_progress_from_output([message], returncode=1)

        self.assertEqual(progress, 18)
        self.assertEqual(stage, "IB client-id 已被其他刷新占用")
        self.assertEqual(refresh_status_http_code({"returncode": 1, "stdout": message}), 409)

    def test_refresh_progress_distinguishes_future_price_from_option_quotes(self) -> None:
        progress, stage = refresh_progress_from_output([
            "ZC future prices sidecar: source=fresh",
            "ZC option quotes: selected=120, months=202609",
        ])

        self.assertEqual(progress, 74)
        self.assertEqual(stage, "刷新ZC期权行情")

    def test_wait_for_planner_server_rejects_plain_static_server(self) -> None:
        server = self.start_server(QuietStaticHandler)

        with self.assertRaises(SystemExit) as raised:
            wait_for_planner_server(self.args_for(server.server_address[1]), DummyProcess(), timeout=0.4)

        self.assertIn("plain static HTTP server", str(raised.exception))


class CachedPositionsTests(unittest.TestCase):
    def test_nonempty_cached_positions_skips_empty_html_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            planner = root / "planner"
            debug = planner / "debug"
            debug.mkdir(parents=True)
            (planner / "carry_dashboard_positions.csv").write_text("symbol,position\n", encoding="utf-8")
            (debug / "dashboard_treasury_positions.csv").write_text("symbol,position\nZF,-1\n", encoding="utf-8")
            args = SimpleNamespace(html_data_dir=planner, working_dir=debug)

            frame, path = clean_cli._nonempty_cached_positions(args)

        self.assertEqual(path, debug / "dashboard_treasury_positions.csv")
        self.assertEqual(frame.to_dict("records"), [{"symbol": "ZF", "position": -1}])

    def test_empty_snapshot_without_cache_is_not_publishable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            args = RefreshCarryHtmlFallbackTests().args_for(directory, Path())
            args.positions_csv = ""
            args.allow_empty_positions = False
            snapshot = SimpleNamespace(
                position_frame=pd.DataFrame(),
                account_summary=pd.DataFrame(),
                greek_summary=pd.DataFrame(),
            )
            with (
                patch.object(clean_cli, "ib_connection", return_value=nullcontext(object())) as connection,
                patch.object(clean_cli, "fetch_account_dashboard", return_value=snapshot),
                self.assertRaises(SystemExit) as raised,
            ):
                clean_cli._run_refresh_carry_html(
                    args,
                    IBSettings(host="127.0.0.1", port=4001, client_id=7316, account="U16251798"),
                )

        self.assertIn("refusing to publish an empty inventory", str(raised.exception))
        self.assertEqual(connection.call_args.kwargs["fetch_fields"], clean_cli.StartupFetch.POSITIONS)


class ScheduledRefreshTests(unittest.TestCase):
    def test_scheduled_mode_uses_fast_only_when_every_product_is_current_in_us_eastern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner = Path(tmp)
            chain = planner / "carry_dashboard_chain.csv"
            chain.write_text(
                "symbol,snapshotTimeUtc\n"
                "ZF,2026-07-16T16:00:00+00:00\n"
                "ZN,2026-07-16T16:01:00+00:00\n"
                "ZC,2026-07-16T16:02:00+00:00\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                refresh_mode="scheduled",
                html_data_dir=planner,
                chain_specs="ZF=202609;ZN=202609",
                zc_chain_specs="ZC=202609",
            )

            mode, decision = select_effective_refresh_mode(
                args,
                now=datetime(2026, 7, 16, 20, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(mode, "fast")
            self.assertIn("US/Eastern 2026-07-16", decision)
            self.assertEqual(
                chain_eastern_dates_by_product(planner),
                {"ZF": datetime(2026, 7, 16).date(), "ZN": datetime(2026, 7, 16).date(), "ZC": datetime(2026, 7, 16).date()},
            )

    def test_scheduled_mode_runs_full_when_one_product_is_stale_in_us_eastern(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner = Path(tmp)
            (planner / "carry_dashboard_chain.csv").write_text(
                "symbol,snapshotTimeUtc\n"
                "ZF,2026-07-16T16:00:00+00:00\n"
                "ZN,2026-07-16T16:01:00+00:00\n"
                "ZC,2026-07-15T16:02:00+00:00\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                refresh_mode="scheduled",
                html_data_dir=planner,
                chain_specs="ZF=202609;ZN=202609",
                zc_chain_specs="ZC=202609",
            )

            mode, decision = select_effective_refresh_mode(
                args,
                now=datetime(2026, 7, 16, 20, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(mode, "full")
            self.assertIn("ZC=2026-07-15", decision)

    def test_us_eastern_date_gate_does_not_use_local_asia_date(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            planner = Path(tmp)
            (planner / "carry_dashboard_chain.csv").write_text(
                "symbol,snapshotTimeUtc\n"
                "ZF,2026-07-15T20:00:00+00:00\n"
                "ZN,2026-07-15T20:00:00+00:00\n"
                "ZC,2026-07-15T20:00:00+00:00\n",
                encoding="utf-8",
            )
            args = SimpleNamespace(
                refresh_mode="scheduled",
                html_data_dir=planner,
                chain_specs="ZF=202609;ZN=202609",
                zc_chain_specs="ZC=202609",
            )

            mode, _decision = select_effective_refresh_mode(
                args,
                now=datetime(2026, 7, 16, 1, 0, tzinfo=timezone.utc),
            )

            self.assertEqual(mode, "fast")

    def test_repeating_refresh_keeps_running_after_one_failure(self) -> None:
        args = SimpleNamespace(repeat_minutes=3)
        with (
            patch("refresh_inventory_data.run_refresh_once", side_effect=SystemExit("IB unavailable")) as run_once,
            patch("refresh_inventory_data.time.sleep", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_scheduled_refresh(args)

        self.assertEqual(run_once.call_count, 1)

    def test_repeating_refresh_keeps_server_parent_alive_after_unexpected_exception(self) -> None:
        args = SimpleNamespace(repeat_minutes=3)
        with (
            patch("refresh_inventory_data.run_refresh_once", side_effect=RuntimeError("status file locked")) as run_once,
            patch("refresh_inventory_data.time.sleep", side_effect=KeyboardInterrupt),
            self.assertRaises(KeyboardInterrupt),
        ):
            run_scheduled_refresh(args)

        self.assertEqual(run_once.call_count, 1)


class IbClientLockTests(unittest.TestCase):
    def test_same_ib_client_id_is_locked_once(self) -> None:
        client_id = 880001

        with acquire_ib_client_lock("127.0.0.1", 4001, client_id, purpose="test-refresh"):
            with self.assertRaises(IbClientLockBusy) as raised:
                with acquire_ib_client_lock("127.0.0.1", 4001, client_id, purpose="test-refresh"):
                    self.fail("second lock acquisition should not be possible")

        self.assertIn("client-id 880001", str(raised.exception))

    def test_busy_lock_reports_owner_metadata(self) -> None:
        client_id = 880004

        with acquire_ib_client_lock("127.0.0.1", 4001, client_id, purpose="test-refresh-owner"):
            with self.assertRaises(IbClientLockBusy) as raised:
                with acquire_ib_client_lock("127.0.0.1", 4001, client_id, purpose="test-refresh-second"):
                    self.fail("second lock acquisition should not be possible")

        message = str(raised.exception)
        self.assertIn(f"pid={os.getpid()}", message)
        self.assertIn("purpose=test-refresh-owner", message)
        self.assertIn("started_at=", message)

    def test_lock_metadata_is_marked_released(self) -> None:
        client_id = 880005
        path = ib_client_lock_path("127.0.0.1", 4001, client_id)
        metadata_path = ib_client_lock_metadata_path(path)

        with acquire_ib_client_lock("127.0.0.1", 4001, client_id, purpose="test-release"):
            self.assertTrue(metadata_path.exists())

        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        self.assertFalse(metadata["active"])
        self.assertIn("released_at", metadata)

    def test_different_ib_client_ids_have_separate_locks(self) -> None:
        with acquire_ib_client_lock("127.0.0.1", 4001, 880002, purpose="test-refresh"):
            with acquire_ib_client_lock("127.0.0.1", 4001, 880003, purpose="test-refresh"):
                pass


class FastRefreshSelectionTests(unittest.TestCase):
    def test_fast_filter_widths_are_product_aware(self) -> None:
        self.assertEqual(_product_filter_widths("ZF", 1.0, 3.0, fast_refresh=True), (1.0, 3.0))
        self.assertEqual(_product_filter_widths("ZN", 1.0, 3.0, fast_refresh=True), (1.5, 3.0))
        self.assertEqual(_product_filter_widths("ZC", 1.0, 3.0, fast_refresh=True), (20.0, 40.0))

    def test_force_con_ids_are_kept_outside_fast_dte_window(self) -> None:
        contracts = [SimpleNamespace(conId=1), SimpleNamespace(conId=2)]
        metadata = pd.DataFrame(
            [
                {"conId": 1, "strike": 100, "right": "C", "expiration": "20990101", "underlyingMonth": "202609", "underlyingPrice": 100},
                {"conId": 2, "strike": 150, "right": "P", "expiration": "20990101", "underlyingMonth": "202609", "underlyingPrice": 100},
            ]
        )
        settings = StaticChainSettings(
            root="ZF",
            future_months="202609",
            market_data_max_dte=1,
            force_con_ids=(2,),
        )

        selected_contracts, selected = _select_market_data_contracts(
            contracts,
            metadata,
            pd.DataFrame([{"month": "202609", "price": 100}]),
            settings,
        )

        self.assertEqual([contract.conId for contract in selected_contracts], [2])
        self.assertEqual(selected["conId"].astype(int).tolist(), [2])

    def test_zc_decimal_strikes_are_filtered_in_cents(self) -> None:
        contracts = [SimpleNamespace(conId=1), SimpleNamespace(conId=2)]
        metadata = pd.DataFrame(
            [
                {"conId": 1, "strike": 4.25, "right": "P", "expiration": "20990101", "underlyingMonth": "202609"},
                {"conId": 2, "strike": 5.50, "right": "P", "expiration": "20990101", "underlyingMonth": "202609"},
            ]
        )
        settings = StaticChainSettings(
            root="ZC",
            future_months="202609",
            far_strike_width=30,
        )

        selected_contracts, selected = _select_market_data_contracts(
            contracts,
            metadata,
            pd.DataFrame([{"month": "202609", "price": 4.42}]),
            settings,
        )

        self.assertEqual([contract.conId for contract in selected_contracts], [1])
        self.assertEqual(selected["strike"].astype(float).tolist(), [425.0])

    def test_stable_contract_cache_reuses_newest_older_date_key(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            settings = StaticChainSettings(root="ZF", future_months="202609,202612", output_dir=output_dir)
            old_cache = output_dir / "ZF_FOP_Static_202609_202612_from_20260707_to_all_contracts.csv"
            old_cache.write_text("conId\n1\n", encoding="utf-8")
            preferred = _contract_cache_path(settings)

            self.assertEqual(preferred.name, "ZF_FOP_Static_202609_202612_from_auto_to_all_contracts.csv")
            self.assertEqual(_find_existing_contract_cache(settings, preferred), old_cache)

    def test_contract_cache_ignores_filtered_selection_and_poisoned_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            settings = StaticChainSettings(root="ZN", future_months="202609,202612", output_dir=output_dir)
            full = output_dir / "ZN_FOP_Static_202609_202612_from_20260707_to_all_contracts.csv"
            selected = output_dir / "ZN_FOP_Static_202609_202612_from_20260707_to_all_selected_contracts.csv"
            preferred = _contract_cache_path(settings)
            full.write_text("conId\n1\n2\n", encoding="utf-8")
            selected.write_text("conId\n2\n", encoding="utf-8")
            preferred.write_text("conId\n2\n", encoding="utf-8")
            full.with_name(full.name.replace("_contracts.csv", "_chain_summary.csv")).write_text(
                "expiration,count\n20260717,2\n",
                encoding="utf-8",
            )

            self.assertEqual(_find_existing_contract_cache(settings, preferred), full)

    def test_single_month_refresh_reuses_and_filters_two_month_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            settings = StaticChainSettings(
                root="ZF",
                future_months="202609",
                output_dir=output_dir,
                request_market_data=False,
            )
            cache = output_dir / "ZF_FOP_Static_202609_202612_from_20260707_to_all_contracts.csv"
            metadata = pd.DataFrame([
                {
                    "underlyingMonth": "202609", "conId": 1, "secType": "FOP", "symbol": "ZF",
                    "localSymbol": "A", "tradingClass": "A", "lastTradeDateOrContractMonth": "20260717",
                    "strike": 107.0, "right": "C", "multiplier": "1000", "exchange": "CBOT", "currency": "USD",
                },
                {
                    "underlyingMonth": "202612", "conId": 2, "secType": "FOP", "symbol": "ZF",
                    "localSymbol": "B", "tradingClass": "B", "lastTradeDateOrContractMonth": "20261016",
                    "strike": 107.0, "right": "C", "multiplier": "1000", "exchange": "CBOT", "currency": "USD",
                },
            ])
            metadata.to_csv(cache, index=False)
            cache.with_name(cache.name.replace("_contracts.csv", "_chain_summary.csv")).write_text(
                "underlyingMonth,candidateCount\n202609,1\n202612,1\n",
                encoding="utf-8",
            )
            preferred = _contract_cache_path(settings)

            self.assertEqual(_find_existing_contract_cache(settings, preferred), cache)
            with patch(
                "target_treasury_monitor_clean.chain_batch._fresh_or_cached_future_prices",
                side_effect=AssertionError("provided futures prices must be reused"),
            ):
                result = _load_cached_chain(
                    None,
                    settings,
                    cache,
                    future_prices=pd.DataFrame([{"month": "202609", "price": 107.0}]),
                    future_price_source="fresh",
                )

            self.assertEqual(result["metadata"]["underlyingMonth"].astype(str).tolist(), ["202609"])
            self.assertEqual([contract.conId for contract in result["contracts"]], [1])
            self.assertEqual(result["future_prices"]["month"].astype(str).tolist(), ["202609"])


class RefreshCarryHtmlFallbackTests(unittest.TestCase):
    def args_for(self, directory: Path, positions_csv: Path) -> SimpleNamespace:
        return SimpleNamespace(
            chain_specs="ZF=202609",
            zc_chain_specs="",
            working_dir=str(directory / "debug"),
            html_data_dir=str(directory),
            positions_csv=str(positions_csv),
            positions_timeout=0.1,
            strict_positions=False,
            quote_wait_seconds=0.1,
            infer_spreads=False,
            min_expiration="",
            max_expiration="",
            batch_size=150,
            wait_seconds=0.1,
            stable_seconds=0.1,
            request_interval=0.001,
            inter_batch_pause_seconds=0.01,
            empty_batch_retries=0,
            empty_batch_retry_pause_seconds=0.01,
            no_contract_cache=False,
            rebuild_contract_cache=False,
            no_market_data_filter=False,
            near_dte_days=7,
            near_strike_width=1.0,
            far_strike_width=3.0,
            market_data_max_dte=0,
            future_price_wait_seconds=0.1,
            strict_chain=False,
            skip_bars=False,
            strict_bars=False,
            bars_contracts="",
            bar_size="30 mins",
            duration="1 D",
            what_to_show="TRADES",
            timeout=0.1,
            prefer_local_symbol_bars=False,
            min_chain_rows=1,
            min_bars_rows=1,
            max_chain_age_hours=999999.0,
            max_bars_age_hours=999999.0,
            require_ready=False,
            fast_refresh=True,
        )

    def test_fast_refresh_reuses_cached_chain_when_ib_connection_is_down(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            positions = pd.DataFrame(
                [{"symbol": "ZF", "secType": "FOP", "position": -1, "conId": 101}]
            )
            chain = pd.DataFrame(
                [{"symbol": "ZF", "expiration": "20260710", "strike": 107.5, "right": "P", "bid": 0.01, "ask": 0.02}]
            )
            bars = pd.DataFrame(
                [{"symbol": "ZF", "date": "2026-07-10 09:30:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
            )
            directory.mkdir(parents=True, exist_ok=True)
            positions_path = directory / "carry_dashboard_positions.csv"
            positions.to_csv(positions_path, index=False)
            chain.to_csv(directory / "carry_dashboard_chain.csv", index=False)
            bars.to_csv(directory / "carry_dashboard_bars.csv", index=False)

            args = self.args_for(directory, positions_path)

            with patch.object(clean_cli, "ib_connection", side_effect=ConnectionRefusedError("gateway closed")):
                clean_cli._run_refresh_carry_html(
                    args,
                    IBSettings(host="127.0.0.1", port=4001, client_id=7316, account="U16251798"),
                )

            refreshed_chain = pd.read_csv(directory / "carry_dashboard_chain.csv")
            self.assertEqual(len(refreshed_chain), 1)
            self.assertEqual(refreshed_chain.iloc[0]["symbol"], "ZF")

    def test_fast_refresh_never_scans_candidate_option_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            positions = pd.DataFrame(
                [{"symbol": "ZF", "secType": "FOP", "position": -1, "conId": 101}]
            )
            chain = pd.DataFrame(
                [{"symbol": "ZF", "conId": 101, "expiration": "20260717", "strike": 107.5, "right": "P", "bid": 0.01, "ask": 0.02}]
            )
            bars = pd.DataFrame(
                [{"symbol": "ZF", "date": "2026-07-16 09:30:00", "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}]
            )
            directory.mkdir(parents=True, exist_ok=True)
            positions_path = directory / "carry_dashboard_positions.csv"
            positions.to_csv(positions_path, index=False)
            chain.to_csv(directory / "carry_dashboard_chain.csv", index=False)
            bars.to_csv(directory / "carry_dashboard_bars.csv", index=False)
            args = self.args_for(directory, positions_path)

            with (
                patch.object(clean_cli, "ib_connection", return_value=nullcontext(object())),
                patch.object(
                    clean_cli,
                    "refresh_future_prices_sidecar",
                    return_value=(pd.DataFrame(), "missing", "", directory / "future_prices.csv"),
                ),
                patch.object(
                    clean_cli,
                    "refresh_static_chain",
                    side_effect=AssertionError("fast refresh must not scan candidate chain"),
                ) as refresh_chain,
            ):
                clean_cli._run_refresh_carry_html(
                    args,
                    IBSettings(host="127.0.0.1", port=4001, client_id=7316, account="U16251798"),
                )

            self.assertEqual(refresh_chain.call_count, 0)
            refreshed_chain = pd.read_csv(directory / "carry_dashboard_chain.csv")
            self.assertEqual(refreshed_chain["conId"].astype(int).tolist(), [101])


if __name__ == "__main__":
    unittest.main()
