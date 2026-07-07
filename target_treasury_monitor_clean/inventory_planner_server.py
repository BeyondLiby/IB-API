from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import time
import webbrowser


def relative_web_path(path: Path, directory: Path) -> str:
    return path.resolve().relative_to(directory).as_posix()


def latest_matching_file(directory: Path, pattern: str) -> Path | None:
    matches = [path for path in directory.glob(pattern) if path.is_file()]
    if not matches:
        return None
    return max(matches, key=lambda path: (path.stat().st_mtime, path.name))


def latest_mtime(paths: list[Path | None]) -> float | None:
    existing = [path for path in paths if path is not None and path.exists()]
    if not existing:
        return None
    return max(path.stat().st_mtime for path in existing)


def local_timestamp(timestamp: float | None) -> str:
    if timestamp is None:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def inventory_planner_manifest(directory: Path) -> dict[str, object]:
    planner_dir = directory / "data" / "planner"
    products: dict[str, dict[str, str]] = {}

    default_positions = latest_matching_file(planner_dir, "carry_dashboard_positions.csv")
    default_chain = latest_matching_file(planner_dir, "carry_dashboard_chain.csv")
    bars = latest_matching_file(planner_dir, "carry_dashboard_bars.csv")
    data_files: list[Path | None] = [default_positions, default_chain, bars]
    defaults: dict[str, str] = {}
    if default_positions is not None:
        defaults["positions"] = relative_web_path(default_positions, directory)
    if default_chain is not None:
        defaults["chain"] = relative_web_path(default_chain, directory)
    if bars is not None:
        defaults["bars"] = relative_web_path(bars, directory)

    if default_chain is not None:
        for product in products_from_chain(default_chain):
            entry = {"chain": relative_web_path(default_chain, directory)}
            if default_positions is not None:
                entry["positions"] = relative_web_path(default_positions, directory)
            if bars is not None:
                entry["bars"] = relative_web_path(bars, directory)
            products[product] = entry

    return {
        "products": products,
        "defaults": defaults,
        "dataUpdatedAt": local_timestamp(latest_mtime(data_files)),
    }


def products_from_chain(path: Path) -> list[str]:
    try:
        import csv

        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            products = {
                (row.get("symbol") or row.get("underlying") or row.get("root") or row.get("product") or "").strip().upper()
                for row in reader
            }
    except OSError:
        return []
    return sorted(product for product in products if product)


def inventory_planner_handler(directory: Path):
    class InventoryPlannerHandler(SimpleHTTPRequestHandler):
        def send_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
            if self.path.split("?", 1)[0] == "/inventory-planner-defaults.json":
                self.send_json(200, inventory_planner_manifest(directory))
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
            if self.path.split("?", 1)[0] != "/api/refresh-inventory-data":
                self.send_error(404, "not found")
                return

            script = directory / "refresh_inventory_data.py"
            if not script.exists():
                self.send_json(500, {"ok": False, "error": f"{script.name} not found"})
                return

            started = time.strftime("%Y-%m-%d %H:%M:%S")
            command = [sys.executable, str(script)]
            try:
                result = subprocess.run(
                    command,
                    cwd=directory,
                    text=True,
                    capture_output=True,
                    timeout=900,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                self.send_json(504, {
                    "ok": False,
                    "started": started,
                    "error": "refresh timed out after 900s",
                    "stdout": exc.stdout or "",
                    "stderr": exc.stderr or "",
                })
                return

            finished = time.strftime("%Y-%m-%d %H:%M:%S")
            self.send_json(200 if result.returncode == 0 else 500, {
                "ok": result.returncode == 0,
                "started": started,
                "finished": finished,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "manifest": inventory_planner_manifest(directory),
            })

    return partial(InventoryPlannerHandler, directory=str(directory))


def serve_inventory_planner(directory: Path, host: str = "127.0.0.1", port: int = 8766, open_browser: bool = False) -> None:
    directory = directory.resolve()
    html_path = directory / "sell_side_inventory_planner.html"
    if not html_path.exists():
        raise SystemExit(f"sell_side_inventory_planner.html not found under {directory}")

    try:
        server = ThreadingHTTPServer((host, port), inventory_planner_handler(directory))
    except OSError as exc:
        raise SystemExit(f"cannot bind {host}:{port} ({exc}); try another port") from exc

    host, bound_port = server.server_address[:2]
    url = f"http://{host}:{bound_port}/sell_side_inventory_planner.html"
    print(f"serving: {directory}", flush=True)
    print(f"open: {url}", flush=True)
    print("press Ctrl+C to stop", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped", flush=True)
    finally:
        server.server_close()
