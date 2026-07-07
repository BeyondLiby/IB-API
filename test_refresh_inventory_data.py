from __future__ import annotations

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import threading
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from refresh_inventory_data import wait_for_planner_server
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

    def test_inventory_refresh_post_is_handled_by_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            script = directory / "refresh_inventory_data.py"
            script.write_text("import sys\nprint('fake refresh')\nsys.exit(1)\n", encoding="utf-8")
            handler = inventory_planner_handler(directory)
            server = self.start_server(handler)
            url = f"http://127.0.0.1:{server.server_address[1]}/api/refresh-inventory-data"

            with self.assertRaises(HTTPError) as raised:
                urlopen(Request(url, method="POST"), timeout=5)

            self.assertEqual(raised.exception.code, 500)
            payload = json.loads(raised.exception.read().decode("utf-8"))
            self.assertFalse(payload["ok"])
            self.assertEqual(payload["returncode"], 1)

    def test_wait_for_planner_server_rejects_plain_static_server(self) -> None:
        server = self.start_server(QuietStaticHandler)

        with self.assertRaises(SystemExit) as raised:
            wait_for_planner_server(self.args_for(server.server_address[1]), DummyProcess(), timeout=0.4)

        self.assertIn("plain static HTTP server", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
