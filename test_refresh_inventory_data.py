from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import tempfile
import time
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from refresh_inventory_data import wait_for_planner_server
from target_treasury_monitor_clean.ib_client_lock import IbClientLockBusy
from target_treasury_monitor_clean.ib_client_lock import acquire_ib_client_lock
from target_treasury_monitor_clean.inventory_planner_server import inventory_planner_handler
from target_treasury_monitor_clean.inventory_planner_server import inventory_planner_manifest


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
            script.write_text("import sys\nprint('fake refresh')\nsys.exit(1)\n", encoding="utf-8")
            handler = inventory_planner_handler(directory)
            server = self.start_server(handler)
            url = f"http://127.0.0.1:{server.server_address[1]}/api/refresh-inventory-data"

            response = urlopen(Request(url, method="POST"), timeout=5)
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

    def test_wait_for_planner_server_rejects_plain_static_server(self) -> None:
        server = self.start_server(QuietStaticHandler)

        with self.assertRaises(SystemExit) as raised:
            wait_for_planner_server(self.args_for(server.server_address[1]), DummyProcess(), timeout=0.4)

        self.assertIn("plain static HTTP server", str(raised.exception))


class IbClientLockTests(unittest.TestCase):
    def test_same_ib_client_id_is_locked_once(self) -> None:
        client_id = 880001

        with acquire_ib_client_lock("127.0.0.1", 4001, client_id, purpose="test-refresh"):
            with self.assertRaises(IbClientLockBusy) as raised:
                with acquire_ib_client_lock("127.0.0.1", 4001, client_id, purpose="test-refresh"):
                    self.fail("second lock acquisition should not be possible")

        self.assertIn("client-id 880001", str(raised.exception))

    def test_different_ib_client_ids_have_separate_locks(self) -> None:
        with acquire_ib_client_lock("127.0.0.1", 4001, 880002, purpose="test-refresh"):
            with acquire_ib_client_lock("127.0.0.1", 4001, 880003, purpose="test-refresh"):
                pass


if __name__ == "__main__":
    unittest.main()
