from __future__ import annotations

from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlparse
import uuid
import webbrowser

from .inventory_market_stream import InventoryMarketStream
from .settings import IBSettings


REFRESH_PRODUCTS = ("ZF", "ZN", "ZC")


def normalize_refresh_contract_months(value: object) -> dict[str, str]:
    """Validate optional product futures-month choices from the local planner."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("contractMonths must be an object keyed by ZF, ZN and ZC")
    unknown = set(str(key).upper() for key in value) - set(REFRESH_PRODUCTS)
    if unknown:
        raise ValueError(f"unknown contract-month product: {sorted(unknown)[0]}")

    normalized: dict[str, str] = {}
    for product in REFRESH_PRODUCTS:
        raw = value.get(product)
        if raw is None:
            raw = value.get(product.lower())
        if raw is None or raw == "" or raw == []:
            raise ValueError(f"missing {product} contract month")
        parts = raw if isinstance(raw, list) else str(raw or "").split(",")
        months: list[str] = []
        for part in parts:
            month = str(part).strip().replace("-", "")
            if len(month) != 6 or not month.isdigit() or not 1 <= int(month[-2:]) <= 12:
                raise ValueError(f"invalid {product} contract month: {part}")
            if month not in months:
                months.append(month)
        if not months:
            raise ValueError(f"missing {product} contract month")
        normalized[product] = ",".join(months)
    return normalized


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
        "marginWhatIf": {
            "enabled": False,
            "mode": "read_only",
            "requiresOrderChannel": True,
            "reason": (
                "IB 原生 What-If 使用订单协议；当前 Dashboard 保持 Read-Only API，"
                "因此不会发起在线测算。"
            ),
        },
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
    lock_busy = "ib client refresh is already running" in joined
    checks = [
        (8, "启动刷新进程", "refresh started"),
        (12, "执行刷新命令", "running:"),
        (14, "检查美东数据日期", "refresh_decision:"),
        (20, "连接IB并读取持仓", "refresh positions/account snapshot"),
        (30, "持仓已读取", "positions:"),
        (36, "刷新期权链", "refresh option chains"),
        (46, "刷新ZF期权链", "refresh chain: zf"),
        (49, "刷新ZF期货价格", "zf future price refresh started"),
        (50, "刷新ZF期货价格", "zf future prices sidecar:"),
        (51, "刷新ZF期权行情", "zf option quote refresh started"),
        (52, "刷新ZF期权行情", "zf option quotes:"),
        (58, "刷新ZN期权链", "refresh chain: zn"),
        (61, "刷新ZN期货价格", "zn future price refresh started"),
        (62, "刷新ZN期货价格", "zn future prices sidecar:"),
        (63, "刷新ZN期权行情", "zn option quote refresh started"),
        (64, "刷新ZN期权行情", "zn option quotes:"),
        (68, "刷新ZC期权链", "refresh chain: zc"),
        (71, "刷新ZC期货价格", "zc future price refresh started"),
        (72, "刷新ZC期货价格", "zc future prices sidecar:"),
        (73, "刷新ZC期权行情", "zc option quote refresh started"),
        (74, "刷新ZC期权行情", "zc option quotes:"),
        (78, "持仓快刷行情已完成", "fast refresh: preserving candidate chain"),
        (78, "刷新期货K线", "refresh futures bars"),
        (90, "发布CSV", "published:"),
        (96, "校验输出文件", "ready_for_full"),
        (100, "刷新完成", "refresh finished"),
    ]
    for value, label, needle in checks:
        if needle in joined:
            progress = value
            stage = label
    if lock_busy:
        progress = max(progress, 18)
        stage = "IB client-id 已被其他刷新占用"
    if returncode is not None:
        return (100, "刷新完成") if returncode == 0 else (progress, stage if lock_busy else "刷新失败")
    return progress, stage


def refresh_phase_timings_from_output(
    lines: list[str],
    total_seconds: float = 0.0,
) -> dict[str, float]:
    """Aggregate machine-readable phase timers emitted by the refresh command."""
    raw = {"positions": 0.0, "futures": 0.0, "options": 0.0, "bars": 0.0, "publish": 0.0, "total": 0.0}
    base_seen: set[str] = set()
    details: dict[str, dict[str, float]] = {name: {} for name in raw}
    pattern = re.compile(
        r"^phase timing:\s*(positions|futures|options|bars|publish|total)"
        r"(?:\.([A-Za-z0-9_-]+))?=([0-9]+(?:\.[0-9]+)?)\s*$",
        re.I,
    )
    for line in lines:
        match = pattern.match(str(line).strip())
        if not match:
            continue
        name = match.group(1).lower()
        detail = (match.group(2) or "").upper()
        value = max(float(match.group(3)), 0.0)
        if detail:
            # Per-product markers are individual durations. Using a keyed map
            # also prevents a repeated status line from being counted twice.
            details[name][detail] = value
        else:
            # Legacy refreshes emitted a cumulative value after every product;
            # the latest base value is therefore authoritative.
            raw[name] = value
            base_seen.add(name)

    for name, values in details.items():
        if name not in base_seen and values:
            raw[name] = sum(values.values())

    total = max(raw["total"], float(total_seconds or 0.0), 0.0)
    option_seconds = raw["options"]
    futures_seconds = raw["futures"] + raw["bars"]
    other_seconds = max(total - raw["positions"] - option_seconds - futures_seconds, 0.0)
    return {
        "positionsSeconds": round(raw["positions"], 3),
        "optionsSeconds": round(option_seconds, 3),
        "futuresSeconds": round(futures_seconds, 3),
        "otherSeconds": round(other_seconds, 3),
        "totalSeconds": round(total, 3),
    }


def refresh_mode_details_from_output(
    lines: list[str],
    requested_mode: str,
) -> tuple[str, str]:
    effective_mode = requested_mode
    refresh_decision = ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("effective_mode:"):
            effective_mode = stripped.split(":", 1)[1].strip().split(" ", 1)[0]
        elif stripped.startswith("refresh_decision:"):
            refresh_decision = stripped.split(":", 1)[1].strip()
    return effective_mode, refresh_decision


def refresh_status_http_code(payload: dict[str, object]) -> int:
    if payload.get("returncode") in {None, 0}:
        return 200
    output = "\n".join(str(payload.get(key) or "") for key in ("stdout", "stderr", "error")).lower()
    if "ib client refresh is already running" in output:
        return 409
    return 500


def read_latest_refresh_status(directory: Path) -> dict[str, object]:
    path = directory / "data" / "planner" / "refresh_status.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        modified_at = path.stat().st_mtime
    except OSError:
        return {"ok": None, "running": False, "error": "no refresh status found"}
    except json.JSONDecodeError:
        try:
            modified_at = path.stat().st_mtime
        except OSError:
            return {"ok": None, "running": False, "error": "no refresh status found"}
        if time.time() - modified_at < 5:
            return {
                "ok": None,
                "running": True,
                "progress": 4,
                "stage": "刷新状态写入中",
                "error": "refresh status is being updated",
            }
        return {"ok": None, "running": False, "error": "invalid refresh status"}
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


def inventory_planner_handler(
    directory: Path,
    market_stream: InventoryMarketStream | None = None,
):
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
            if parsed.path == "/api/live-positions":
                if market_stream is None:
                    self.send_json(503, {
                        "ok": False,
                        "connected": False,
                        "dataMode": "offline",
                        "error": "persistent IB market stream is not enabled",
                    })
                    return
                self.send_json(200, market_stream.snapshot())
                return
            if parsed.path == "/api/refresh-inventory-data/status":
                job_id = parse_qs(parsed.query).get("job", [""])[0]
                if job_id in {"", "latest"}:
                    payload = read_latest_refresh_status(directory)
                    self.send_json(refresh_status_http_code(payload), payload)
                    return
                with jobs_lock:
                    job = dict(jobs.get(job_id, {}))
                if not job:
                    self.send_json(404, {"ok": False, "error": f"refresh job not found: {job_id}"})
                    return
                self.send_json(refresh_status_http_code(job), job)
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
            path = self.path.split("?", 1)[0]
            if path == "/api/margin-whatif":
                # IB's native What-If flag still travels through placeOrder at
                # the API protocol layer.  Keep the everyday planner strictly
                # read-only and reject this path before reading input or
                # opening any IB session.
                self.send_json(403, {
                    "ok": False,
                    "mode": "read_only",
                    "requiresOrderChannel": True,
                    "error": (
                        "IB 原生 What-If 已停用：它使用订单协议，而当前 Dashboard "
                        "保持 Read-Only API。"
                    ),
                })
                return
            if path != "/api/refresh-inventory-data":
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
            if mode not in {"fast", "full", "scheduled"}:
                self.send_json(400, {"ok": False, "error": f"unknown refresh mode: {mode}"})
                return
            try:
                contract_months = normalize_refresh_contract_months(payload.get("contractMonths"))
            except ValueError as exc:
                self.send_json(400, {"ok": False, "error": str(exc)})
                return
            if market_stream is not None and contract_months:
                market_stream.set_future_months(contract_months)

            latest = read_latest_refresh_status(directory)
            if latest.get("running"):
                latest = dict(latest)
                latest.setdefault("jobId", "latest")
                latest.setdefault("mode", latest.get("mode") or "unknown")
                self.send_json(202, latest)
                return

            started = time.strftime("%Y-%m-%d %H:%M:%S")
            command = [sys.executable, str(script), "--refresh-mode", mode]
            if contract_months:
                command.extend([
                    "--chain-specs",
                    f"ZF={contract_months['ZF']};ZN={contract_months['ZN']}",
                    "--zc-chain-specs",
                    f"ZC={contract_months['ZC']}",
                ])

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
                    "durationSeconds": 0.0,
                    "phaseTimings": refresh_phase_timings_from_output([], 0.0),
                    "stdout": "",
                    "stderr": "",
                    "lines": [],
                    "mode": mode,
                    "requestedMode": mode,
                    "effectiveMode": mode,
                    "refreshDecision": "",
                    "contractMonths": contract_months,
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
            with jobs_lock:
                requested_mode = str(jobs[job_id].get("requestedMode") or "")
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
                        effective_mode, refresh_decision = refresh_mode_details_from_output(
                            lines,
                            requested_mode,
                        )
                        with jobs_lock:
                            job = jobs[job_id]
                            job["stdout"] = "\n".join(lines)
                            job["lines"] = lines[-80:]
                            job["progress"] = progress
                            job["stage"] = stage
                            job["durationSeconds"] = round(time.monotonic() - started_at, 3)
                            job["phaseTimings"] = refresh_phase_timings_from_output(lines, job["durationSeconds"])
                            job["effectiveMode"] = effective_mode
                            job["refreshDecision"] = refresh_decision
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
            duration_seconds = round(time.monotonic() - started_at, 3) if "started_at" in locals() else 0.0
            effective_mode, refresh_decision = refresh_mode_details_from_output(lines, requested_mode)
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
                    "durationSeconds": duration_seconds,
                    "phaseTimings": refresh_phase_timings_from_output(lines, duration_seconds),
                    "effectiveMode": effective_mode,
                    "refreshDecision": refresh_decision,
                    "stdout": "\n".join(lines),
                    "stderr": stderr,
                    "lines": lines[-80:],
                    "manifest": inventory_planner_manifest(directory),
                })

    return partial(InventoryPlannerHandler, directory=str(directory))


def serve_inventory_planner(
    directory: Path,
    host: str = "127.0.0.1",
    port: int = 8766,
    open_browser: bool = False,
    stream_settings: IBSettings | None = None,
    stream_future_months: dict[str, object] | None = None,
) -> None:
    directory = directory.resolve()
    html_path = directory / "sell_side_inventory_planner.html"
    if not html_path.exists():
        raise SystemExit(f"sell_side_inventory_planner.html not found under {directory}")

    stream_settings = stream_settings or IBSettings(
        client_id=7318,
        market_data_type=3,
        readonly=True,
    )
    market_stream = InventoryMarketStream(
        stream_settings,
        future_months=stream_future_months,
    )
    market_stream.start()
    try:
        server = ThreadingHTTPServer(
            (host, port),
            inventory_planner_handler(
                directory,
                market_stream=market_stream,
            ),
        )
    except OSError as exc:
        market_stream.stop()
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
        market_stream.stop()
