from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid
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
    directory = directory.resolve()
    planner_dir = directory / "data" / "planner"
    debug_dir = planner_dir / "debug"
    products: dict[str, dict[str, str]] = {}

    default_positions = latest_matching_file(planner_dir, "carry_dashboard_positions.csv")
    default_chain = latest_matching_file(planner_dir, "carry_dashboard_chain.csv")
    bars = latest_matching_file(planner_dir, "carry_dashboard_bars.csv")
    product_future_prices: dict[str, Path] = {}
    if debug_dir.exists():
        for path in debug_dir.glob("*_FOP_Static_*_future_prices.csv"):
            product = path.name.split("_", 1)[0].upper()
            current = product_future_prices.get(product)
            if current is None or (path.stat().st_mtime, path.name) > (current.stat().st_mtime, current.name):
                product_future_prices[product] = path
    data_files: list[Path | None] = [default_positions, default_chain, bars, *product_future_prices.values()]
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
            if product in product_future_prices:
                entry["futurePrices"] = relative_web_path(product_future_prices[product], directory)
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


def refresh_progress_from_output(lines: list[str], returncode: int | None = None) -> tuple[int, str]:
    joined = "\n".join(lines).lower()
    progress = 4
    stage = "等待刷新开始"
    checks = [
        (8, "启动刷新进程", "refresh started"),
        (12, "执行刷新命令", "running:"),
        (20, "连接IB并读取持仓", "refresh positions/account snapshot"),
        (30, "持仓已读取", "positions:"),
        (36, "刷新期权链", "refresh option chains"),
        (46, "刷新ZF期权链", "refresh chain: zf"),
        (50, "刷新ZF期货价格", "zf future prices sidecar:"),
        (58, "刷新ZN期权链", "refresh chain: zn"),
        (62, "刷新ZN期货价格", "zn future prices sidecar:"),
        (68, "刷新ZC期权链", "refresh chain: zc"),
        (72, "刷新ZC期货价格", "zc future prices sidecar:"),
        (78, "刷新期货K线", "refresh futures bars"),
        (90, "发布CSV", "published:"),
        (96, "校验输出文件", "ready_for_full"),
        (100, "刷新完成", "refresh finished"),
    ]
    for value, label, needle in checks:
        if needle in joined:
            progress = value
            stage = label
    if returncode is not None:
        return (100, "刷新完成") if returncode == 0 else (max(progress, 18), "刷新失败")
    return progress, stage


def read_latest_refresh_status(directory: Path) -> dict[str, object]:
    path = directory / "data" / "planner" / "refresh_status.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        modified_at = path.stat().st_mtime
    except (OSError, json.JSONDecodeError):
        return {"ok": None, "running": False, "error": "no refresh status found"}
    if isinstance(payload, dict):
        if payload.get("running") and time.time() - modified_at > 1800:
            payload = dict(payload)
            payload.update({
                "ok": False,
                "running": False,
                "error": "refresh status is stale; the refresh process may have stopped unexpectedly",
            })
        return payload
    return {"ok": None, "running": False, "error": "invalid refresh status"}


def inventory_planner_handler(directory: Path):
    jobs: dict[str, dict[str, object]] = {}
    jobs_lock = threading.Lock()

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
            parsed = urlparse(self.path)
            if parsed.path == "/inventory-planner-defaults.json":
                self.send_json(200, inventory_planner_manifest(directory))
                return
            if parsed.path == "/api/refresh-inventory-data/status":
                job_id = parse_qs(parsed.query).get("job", [""])[0]
                if job_id in {"", "latest"}:
                    payload = read_latest_refresh_status(directory)
                    status = 200 if payload.get("returncode") in {None, 0} else 500
                    self.send_json(status, payload)
                    return
                with jobs_lock:
                    job = dict(jobs.get(job_id, {}))
                if not job:
                    self.send_json(404, {"ok": False, "error": f"refresh job not found: {job_id}"})
                    return
                status = 200 if job.get("returncode") in {None, 0} else 500
                self.send_json(status, job)
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

            payload: dict[str, object] = {}
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 0:
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
            except (OSError, json.JSONDecodeError, ValueError):
                payload = {}
            mode = str(payload.get("mode") or "fast").strip().lower()
            if mode not in {"fast", "full"}:
                self.send_json(400, {"ok": False, "error": f"unknown refresh mode: {mode}"})
                return

            started = time.strftime("%Y-%m-%d %H:%M:%S")
            command = [sys.executable, str(script), "--refresh-mode", mode]

            with jobs_lock:
                running = next((job for job in jobs.values() if job.get("running")), None)
                if running is not None:
                    self.send_json(202, running)
                    return

                job_id = uuid.uuid4().hex
                job = {
                    "ok": None,
                    "jobId": job_id,
                    "running": True,
                    "started": started,
                    "finished": "",
                    "returncode": None,
                    "progress": 4,
                    "stage": "提交刷新任务",
                    "stdout": "",
                    "stderr": "",
                    "lines": [],
                    "mode": mode,
                    "manifest": inventory_planner_manifest(directory),
                }
                jobs[job_id] = job

            thread = threading.Thread(target=self.run_refresh_job, args=(job_id, command), daemon=True)
            thread.start()
            self.send_json(202, job)

        def run_refresh_job(self, job_id: str, command: list[str]) -> None:
            lines: list[str] = []
            stderr = ""
            returncode: int | None = None
            try:
                process = subprocess.Popen(
                    command,
                    cwd=directory,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    bufsize=1,
                )
                started_at = time.monotonic()
                assert process.stdout is not None
                try:
                    for line in process.stdout:
                        lines.append(line.rstrip())
                        progress, stage = refresh_progress_from_output(lines)
                        with jobs_lock:
                            job = jobs[job_id]
                            job["stdout"] = "\n".join(lines)
                            job["lines"] = lines[-80:]
                            job["progress"] = progress
                            job["stage"] = stage
                        if time.monotonic() - started_at > 900:
                            process.kill()
                            stderr = "refresh timed out after 900s"
                            break
                finally:
                    process.stdout.close()
                returncode = process.wait(timeout=2)
            except Exception as exc:
                returncode = 1
                stderr = f"{type(exc).__name__}: {exc}"
                lines.append(stderr)

            progress, stage = refresh_progress_from_output(lines, returncode)
            finished = time.strftime("%Y-%m-%d %H:%M:%S")
            with jobs_lock:
                job = jobs[job_id]
                job.update({
                    "ok": returncode == 0,
                    "running": False,
                    "finished": finished,
                    "returncode": returncode,
                    "progress": progress,
                    "stage": stage,
                    "stdout": "\n".join(lines),
                    "stderr": stderr,
                    "lines": lines[-80:],
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
