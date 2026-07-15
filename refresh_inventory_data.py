from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

from target_treasury_monitor_clean.inventory_planner_server import refresh_progress_from_output
from target_treasury_monitor_clean.ib_client_lock import IbClientLockBusy, acquire_ib_client_lock
from target_treasury_monitor_clean.settings import DEFAULT_IB_ACCOUNT


STATUS_REPLACE_ATTEMPTS = 20
STATUS_REPLACE_RETRY_SECONDS = 0.05
STATUS_LOG_TAIL_LINES = 160
STATUS_UPDATE_MIN_INTERVAL = 0.5


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
        "--positions-timeout",
        str(args.positions_timeout),
        "--future-price-wait-seconds",
        str(args.future_price_wait_seconds),
        # The wrapper holds this client-id lock for the entire refresh, including
        # its status-file updates. The child CLI must not try to acquire it again.
        "--no-client-lock",
    ]
    if args.refresh_mode == "fast":
        command.append("--fast-refresh")
    if args.market_data_max_dte:
        command.extend(["--market-data-max-dte", str(args.market_data_max_dte)])
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
    if args.strict_positions:
        command.append("--strict-positions")
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


def write_refresh_status(args: argparse.Namespace, payload: dict[str, object]) -> bool:
    """Publish refresh state without letting a transient Windows file lock kill a refresh.

    The planner page polls this file while the worker updates it.  On Windows a
    reader can briefly prevent ``os.replace`` from replacing the file, unlike
    on POSIX.  The status is advisory, so retrying and retaining the last
    complete payload is safer than terminating a successful data refresh.
    """
    path = refresh_status_path(args)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(encoded)
        temporary_path = Path(handle.name)
    try:
        for attempt in range(STATUS_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary_path, path)
                return True
            except PermissionError:
                if attempt + 1 >= STATUS_REPLACE_ATTEMPTS:
                    return False
                time.sleep(STATUS_REPLACE_RETRY_SECONDS)
    finally:
        temporary_path.unlink(missing_ok=True)

    return False


def _refresh_status_payload(
    *,
    started: str,
    lines: list[str],
    progress: int,
    stage: str,
    running: bool,
    finished: str = "",
    returncode: int | None = None,
) -> dict[str, object]:
    output_tail = lines[-STATUS_LOG_TAIL_LINES:]
    return {
        "ok": None if running else returncode == 0,
        "running": running,
        "started": started,
        "finished": finished,
        "returncode": returncode,
        "progress": progress,
        "stage": stage,
        "lines": lines[-80:],
        # The browser only needs useful recent diagnostics.  Keeping the full
        # pretty-printed validation report made every progress write larger
        # and increased the Windows file-lock collision window.
        "stdout": "\n".join(output_tail),
        "stderr": "",
    }


def run_refresh_once(args: argparse.Namespace) -> None:
    command = build_refresh_command(args)
    print_refresh_request_summary(args, command)
    if args.dry_run:
        return
    try:
        with acquire_ib_client_lock(
            args.ib_host,
            args.ib_port,
            args.client_id,
            purpose="refresh-inventory-data",
        ):
            _run_refresh_once_locked(args, command)
    except IbClientLockBusy as exc:
        print(str(exc), flush=True)
        raise SystemExit(str(exc)) from exc


def run_scheduled_refresh(args: argparse.Namespace) -> None:
    while True:
        started = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"\n[{started}] refresh started", flush=True)
        try:
            run_refresh_once(args)
        except SystemExit:
            if args.repeat_minutes <= 0:
                raise
            print("refresh failed; planner server stays online and the next scheduled retry will run normally", flush=True)
        except Exception as exc:
            if args.repeat_minutes <= 0:
                raise
            print(
                f"refresh crashed ({type(exc).__name__}: {exc}); "
                "planner server stays online and the next scheduled retry will run normally",
                flush=True,
            )
        finished = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{finished}] refresh finished", flush=True)
        if args.repeat_minutes <= 0:
            return
        sleep_seconds = max(args.repeat_minutes * 60, 1)
        print(f"sleeping {sleep_seconds:.0f}s; press Ctrl+C to stop", flush=True)
        time.sleep(sleep_seconds)


def _run_refresh_once_locked(args: argparse.Namespace, command: list[str]) -> None:
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
    last_status_write = time.monotonic()
    last_reported_state = (8, "启动刷新进程")
    try:
        for line in process.stdout:
            text = line.rstrip()
            print(text, flush=True)
            lines.append(text)
            progress, stage = refresh_progress_from_output(lines)
            state = (progress, stage)
            now = time.monotonic()
            if state != last_reported_state or now - last_status_write >= STATUS_UPDATE_MIN_INTERVAL:
                write_refresh_status(
                    args,
                    _refresh_status_payload(
                        started=started,
                        lines=lines,
                        progress=progress,
                        stage=stage,
                        running=True,
                    ),
                )
                last_status_write = now
                last_reported_state = state
    finally:
        process.stdout.close()
    returncode = process.wait()
    progress, stage = refresh_progress_from_output(lines, returncode)
    finished = time.strftime("%Y-%m-%d %H:%M:%S")
    write_refresh_status(
        args,
        _refresh_status_payload(
            started=started,
            lines=lines,
            progress=progress,
            stage=stage,
            running=False,
            finished=finished,
            returncode=returncode,
        ),
    )
    if returncode != 0:
        raise SystemExit(f"refresh command failed with exit code {returncode}; see output above")


def print_refresh_request_summary(args: argparse.Namespace, command: list[str]) -> None:
    mode_note = (
        "fast: request near/current-position option quotes, preserve cached far-chain rows and bars"
        if args.refresh_mode == "fast"
        else "full: request the broader configured option chain and refresh bars unless skipped"
    )
    print("refresh request:", flush=True)
    print(f"  mode: {args.refresh_mode} ({mode_note})", flush=True)
    print(
        f"  ib: {args.ib_host}:{args.ib_port}, client_id={args.client_id}, "
        f"market_data={args.market_data_type}, account={args.account or '<none>'}",
        flush=True,
    )
    print(f"  chain_specs: {args.chain_specs}", flush=True)
    print(f"  zc_chain_specs: {args.zc_chain_specs or '<none>'}", flush=True)
    print(
        "  quote timing: "
        f"batch_size={args.batch_size}, wait={args.wait_seconds}s, stable={args.stable_seconds}s, "
        f"request_interval={args.request_interval}s, inter_batch_pause={args.inter_batch_pause_seconds}s, "
        f"timeout={args.timeout}s, future_price_wait={args.future_price_wait_seconds}s",
        flush=True,
    )
    print(
        f"  positions: timeout={args.positions_timeout}s, "
        f"source={'csv ' + args.positions_csv if args.positions_csv else 'IB account snapshot'}",
        flush=True,
    )
    print(
        f"  filters/cache: market_data_max_dte={args.market_data_max_dte or 'auto'}, "
        f"contract_cache={'off' if args.no_contract_cache else 'on'}, "
        f"rebuild_cache={bool(args.rebuild_contract_cache)}, strict_chain={bool(args.strict_chain)}",
        flush=True,
    )
    print(
        f"  bars: {'skip' if args.skip_bars else args.duration + ' / ' + args.bar_size + ' / ' + args.what_to_show}",
        flush=True,
    )
    print(f"  working_dir: {args.working_dir}", flush=True)
    print(f"  html_data_dir: {args.html_data_dir}", flush=True)
    print("running command:", flush=True)
    print("  " + " ".join(command), flush=True)


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
    parser.add_argument("--refresh-mode", choices=("fast", "full"), default="fast", help="fast preserves cached far-chain/bars rows; full refreshes the broader chain.")
    parser.add_argument("--full-refresh", dest="refresh_mode", action="store_const", const="full", help="Shortcut for --refresh-mode full.")
    parser.add_argument("--positions-timeout", type=float, default=30.0)
    parser.add_argument("--strict-positions", action="store_true")
    parser.add_argument("--future-price-wait-seconds", type=float, default=6.0, help="Seconds to wait when refreshing underlying futures prices for each product.")
    parser.add_argument("--min-expiration", default="")
    parser.add_argument("--max-expiration", default="")
    parser.add_argument("--working-dir", type=Path, default=Path("data/planner/debug"))
    parser.add_argument("--html-data-dir", type=Path, default=Path("data/planner"))
    parser.add_argument("--batch-size", type=int, default=150)
    parser.add_argument("--wait-seconds", type=float, default=8.0)
    parser.add_argument("--stable-seconds", type=float, default=1.5)
    parser.add_argument("--request-interval", type=float, default=0.025)
    parser.add_argument("--inter-batch-pause-seconds", type=float, default=1.0)
    parser.add_argument("--market-data-max-dte", type=int, default=0, help="Only request option market data up to this DTE. 0 means auto/default.")
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
        run_scheduled_refresh(args)
    finally:
        if server is not None and server.poll() is None:
            server.terminate()


if __name__ == "__main__":
    main()
