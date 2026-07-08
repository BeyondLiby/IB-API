from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from target_treasury_monitor_clean.inventory_planner_server import refresh_progress_from_output
from target_treasury_monitor_clean.settings import DEFAULT_IB_ACCOUNT


def split_extra_args(values: list[str] | None) -> list[str]:
    if not values:
        return []
    out: list[str] = []
    for value in values:
        out.extend(part for part in value.split() if part)
    return out


def build_refresh_command(args: argparse.Namespace) -> list[str]:
    command = [
        args.python,
        "-m",
        "target_treasury_monitor_clean.cli",
        "refresh-carry-html",
        "--host",
        args.ib_host,
        "--port",
        str(args.ib_port),
        "--client-id",
        str(args.client_id),
        "--market-data-type",
        args.market_data_type,
        "--chain-specs",
        args.chain_specs,
        "--working-dir",
        str(args.working_dir),
        "--html-data-dir",
        str(args.html_data_dir),
        "--batch-size",
        str(args.batch_size),
        "--wait-seconds",
        str(args.wait_seconds),
        "--stable-seconds",
        str(args.stable_seconds),
        "--request-interval",
        str(args.request_interval),
        "--inter-batch-pause-seconds",
        str(args.inter_batch_pause_seconds),
        "--timeout",
        str(args.timeout),
        "--bar-size",
        args.bar_size,
        "--duration",
        args.duration,
        "--what-to-show",
        args.what_to_show,
    ]
    if args.account:
        command.extend(["--account", args.account])
    if args.positions_csv:
        command.extend(["--positions-csv", args.positions_csv])
    if args.zc_chain_specs:
        command.extend(["--zc-chain-specs", args.zc_chain_specs])
    if args.bars_contracts:
        command.extend(["--bars-contracts", args.bars_contracts])
    if args.min_expiration:
        command.extend(["--min-expiration", args.min_expiration])
    if args.max_expiration:
        command.extend(["--max-expiration", args.max_expiration])
    if args.infer_spreads:
        command.append("--infer-spreads")
    if args.no_market_data_filter:
        command.append("--no-market-data-filter")
    if args.no_contract_cache:
        command.append("--no-contract-cache")
    if args.rebuild_contract_cache:
        command.append("--rebuild-contract-cache")
    if args.strict_chain:
        command.append("--strict-chain")
    if args.skip_bars:
        command.append("--skip-bars")
    if args.strict_bars:
        command.append("--strict-bars")
    if args.prefer_local_symbol_bars:
        command.append("--prefer-local-symbol-bars")
    if args.require_ready:
        command.append("--require-ready")
    command.extend(split_extra_args(args.extra_refresh_arg))
    return command


def build_server_command(args: argparse.Namespace) -> list[str]:
    command = [
        args.python,
        str(Path(__file__).resolve().parent / "open_inventory_planner.py"),
        "--directory",
        str(Path(__file__).resolve().parent),
        "--host",
        args.planner_host,
        "--port",
        str(args.planner_port),
    ]
    if not args.open_browser:
        command.append("--no-open")
    return command


def planner_base_url(args: argparse.Namespace) -> str:
    host = "127.0.0.1" if args.planner_host in {"", "0.0.0.0"} else args.planner_host
    return f"http://{host}:{args.planner_port}"


def wait_for_planner_server(args: argparse.Namespace, server: subprocess.Popen[str], timeout: float = 6.0) -> None:
    base_url = planner_base_url(args)
    manifest_url = f"{base_url}/inventory-planner-defaults.json"
    deadline = time.monotonic() + timeout
    last_error = "not checked"

    while time.monotonic() < deadline:
        try:
            with urlopen(manifest_url, timeout=0.75) as response:
                body = response.read(4096)
                if response.status == 200 and b'"products"' in body and b'"defaults"' in body:
                    print(f"planner API ready: {base_url}/sell_side_inventory_planner.html", flush=True)
                    return
                last_error = f"unexpected response HTTP {response.status}"
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if server.poll() is not None:
                break
        except (OSError, TimeoutError, URLError) as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            if server.poll() is not None:
                break
        time.sleep(0.2)

    raise SystemExit(
        "planner server did not expose the inventory API at "
        f"{manifest_url} ({last_error}). The port may be occupied by a plain "
        "static HTTP server; stop that server or pass a different --planner-port."
    )


def refresh_status_path(args: argparse.Namespace) -> Path:
    return Path(args.html_data_dir) / "refresh_status.json"


def write_refresh_status(args: argparse.Namespace, payload: dict[str, object]) -> None:
    path = refresh_status_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_refresh_once(args: argparse.Namespace) -> None:
    command = build_refresh_command(args)
    print("running:", " ".join(command), flush=True)
    if args.dry_run:
        return
    lines: list[str] = []
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    write_refresh_status(args, {
        "ok": None,
        "running": True,
        "started": started,
        "finished": "",
        "returncode": None,
        "progress": 8,
        "stage": "启动刷新进程",
        "lines": [],
        "stdout": "",
        "stderr": "",
    })
    process = subprocess.Popen(
        command,
        cwd=Path(__file__).resolve().parent,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    assert process.stdout is not None
    try:
        for line in process.stdout:
            text = line.rstrip()
            print(text, flush=True)
            lines.append(text)
            progress, stage = refresh_progress_from_output(lines)
            write_refresh_status(args, {
                "ok": None,
                "running": True,
                "started": started,
                "finished": "",
                "returncode": None,
                "progress": progress,
                "stage": stage,
                "lines": lines[-80:],
                "stdout": "\n".join(lines),
                "stderr": "",
            })
    finally:
        process.stdout.close()
    returncode = process.wait()
    progress, stage = refresh_progress_from_output(lines, returncode)
    finished = time.strftime("%Y-%m-%d %H:%M:%S")
    write_refresh_status(args, {
        "ok": returncode == 0,
        "running": False,
        "started": started,
        "finished": finished,
        "returncode": returncode,
        "progress": progress,
        "stage": stage,
        "lines": lines[-80:],
        "stdout": "\n".join(lines),
        "stderr": "",
    })
    if returncode != 0:
        raise SystemExit(f"refresh command failed with exit code {returncode}; see output above")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Refresh IB positions, option chains, futures bars, and publish CSVs for the inventory planner HTML."
    )
    parser.add_argument("--python", default=sys.executable, help="Python executable that has the IB project dependencies installed.")
    parser.add_argument("--account", default=os.environ.get("IB_ACCOUNT", DEFAULT_IB_ACCOUNT), help="IB account id. Defaults to IB_ACCOUNT env var, then the project default account.")
    parser.add_argument("--ib-host", default="127.0.0.1")
    parser.add_argument("--ib-port", type=int, default=4001)
    parser.add_argument("--client-id", type=int, default=7316)
    parser.add_argument("--market-data-type", default="delayed", help="live, frozen, delayed, delayed_frozen, or 1/2/3/4")
    parser.add_argument("--chain-specs", default="ZF=202609,202612;ZN=202609,202612")
    parser.add_argument("--zc-chain-specs", default="ZC=202609,202612", help="Optional, for example ZC=202609,202612.")
    parser.add_argument("--bars-contracts", default="", help="Optional ROOT:YYYYMM list. Defaults to first month in each chain spec.")
    parser.add_argument("--positions-csv", default="", help="Reuse positions CSV instead of refreshing IB positions.")
    parser.add_argument("--min-expiration", default="")
    parser.add_argument("--max-expiration", default="")
    parser.add_argument("--working-dir", type=Path, default=Path("data/planner/debug"))
    parser.add_argument("--html-data-dir", type=Path, default=Path("data/planner"))
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    parser.add_argument("--stable-seconds", type=float, default=1.5)
    parser.add_argument("--request-interval", type=float, default=0.025)
    parser.add_argument("--inter-batch-pause-seconds", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--bar-size", default="30 mins")
    parser.add_argument("--duration", default="1 M")
    parser.add_argument("--what-to-show", default="TRADES")
    parser.add_argument("--infer-spreads", action="store_true")
    parser.add_argument("--no-market-data-filter", action="store_true")
    parser.add_argument("--no-contract-cache", action="store_true")
    parser.add_argument("--rebuild-contract-cache", action="store_true")
    parser.add_argument("--strict-chain", action="store_true")
    parser.add_argument("--skip-bars", action="store_true")
    parser.add_argument("--strict-bars", action="store_true")
    parser.add_argument("--prefer-local-symbol-bars", action="store_true")
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--repeat-minutes", type=float, default=0, help="Repeat refresh until Ctrl+C. 0 means run once.")
    parser.add_argument("--serve-planner", action="store_true", help="Start the local inventory planner server while refreshing.")
    parser.add_argument("--planner-host", default="127.0.0.1")
    parser.add_argument("--planner-port", type=int, default=8766)
    parser.add_argument("--open-browser", action="store_true", help="Open the planner browser tab when --serve-planner is used.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--extra-refresh-arg", action="append", help="Extra raw arguments passed to refresh-carry-html.")
    args = parser.parse_args()

    if not args.account and not args.positions_csv:
        raise SystemExit("--account is required unless --positions-csv is provided")

    server: subprocess.Popen[str] | None = None
    if args.serve_planner and not args.dry_run:
        server_command = build_server_command(args)
        print("starting planner server:", " ".join(server_command), flush=True)
        server = subprocess.Popen(server_command, cwd=Path(__file__).resolve().parent)
        wait_for_planner_server(args, server)

    try:
        while True:
            started = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"\n[{started}] refresh started", flush=True)
            run_refresh_once(args)
            finished = time.strftime("%Y-%m-%d %H:%M:%S")
            print(f"[{finished}] refresh finished", flush=True)
            if args.repeat_minutes <= 0:
                break
            sleep_seconds = max(args.repeat_minutes * 60, 1)
            print(f"sleeping {sleep_seconds:.0f}s; press Ctrl+C to stop", flush=True)
            time.sleep(sleep_seconds)
    finally:
        if server is not None and server.poll() is None:
            server.terminate()


if __name__ == "__main__":
    main()
