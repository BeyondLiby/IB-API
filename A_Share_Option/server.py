from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Lock
import time
from urllib.parse import parse_qs, urlparse
import webbrowser

try:
    from .live_excel_reader import read_live_option_chain
    from .xlsx_reader import XlsxReadError, read_option_chain
except ImportError:  # pragma: no cover - supports direct script execution
    from live_excel_reader import read_live_option_chain
    from xlsx_reader import XlsxReadError, read_option_chain


DEFAULT_EXCEL_PATH = Path("C:/Users/Beyond/Desktop/A股期权信息.xlsx")
DEFAULT_SHEET = "创业板"
LIVE_EXCEL_LOCK = Lock()


def _json_response(handler: SimpleHTTPRequestHandler, status: int, payload: dict[str, object] | list[object]) -> None:
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_positions(path: Path) -> list[dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("positions"), list):
        return [item for item in payload["positions"] if isinstance(item, dict)]
    return []


def _write_positions(path: Path, positions: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(positions, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_chain(excel_path: Path, sheet: str, mode: str) -> dict[str, object]:
    mode = (mode or "auto").lower()
    if mode == "saved":
        return read_option_chain(excel_path, sheet)
    # Excel COM permits one reliable reader at a time. The HTTP server is threaded,
    # so guard against overlapping dashboard refreshes and browser tabs.
    with LIVE_EXCEL_LOCK:
        if mode == "live":
            return read_live_option_chain(excel_path, sheet)
        try:
            return read_live_option_chain(excel_path, sheet)
        except XlsxReadError as exc:
            payload = read_option_chain(excel_path, sheet)
            payload["sourceMode"] = "saved-fallback"
            payload["liveError"] = str(exc)
            return payload


def make_handler(directory: Path, excel_path: Path, default_sheet: str, positions_path: Path, read_mode: str):
    class AShareOptionHandler(SimpleHTTPRequestHandler):
        def send_json(self, status: int, payload: dict[str, object] | list[object]) -> None:
            _json_response(self, status, payload)

        def do_GET(self) -> None:  # noqa: N802 - stdlib method name
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.path = "/dashboard.html"
                return super().do_GET()
            if parsed.path == "/api/chain":
                query = parse_qs(parsed.query)
                sheet = query.get("sheet", [default_sheet])[0] or default_sheet
                mode = query.get("mode", [read_mode])[0] or read_mode
                try:
                    payload = _read_chain(excel_path, sheet, mode)
                except XlsxReadError as exc:
                    self.send_json(500, {
                        "ok": False,
                        "error": str(exc),
                        "source": str(excel_path),
                        "sheet": sheet,
                        "sourceMode": mode,
                        "readAt": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    return
                self.send_json(200, payload)
                return
            if parsed.path == "/api/positions":
                self.send_json(200, {
                    "ok": True,
                    "path": str(positions_path),
                    "positions": _read_positions(positions_path),
                })
                return
            if parsed.path == "/api/config":
                self.send_json(200, {
                    "ok": True,
                    "excelPath": str(excel_path),
                    "defaultSheet": default_sheet,
                    "readMode": read_mode,
                    "positionsPath": str(positions_path),
                })
                return
            return super().do_GET()

        def do_POST(self) -> None:  # noqa: N802 - stdlib method name
            parsed = urlparse(self.path)
            if parsed.path != "/api/positions":
                self.send_error(404, "not found")
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length > 1_000_000:
                self.send_json(413, {"ok": False, "error": "payload too large"})
                return
            try:
                payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except json.JSONDecodeError as exc:
                self.send_json(400, {"ok": False, "error": f"invalid JSON: {exc}"})
                return
            positions = payload.get("positions") if isinstance(payload, dict) else payload
            if not isinstance(positions, list):
                self.send_json(400, {"ok": False, "error": "expected a positions list"})
                return
            clean = [item for item in positions if isinstance(item, dict)]
            _write_positions(positions_path, clean)
            self.send_json(200, {"ok": True, "positions": clean, "path": str(positions_path)})

    return partial(AShareOptionHandler, directory=str(directory))


def serve(
    directory: Path,
    excel_path: Path = DEFAULT_EXCEL_PATH,
    sheet: str = DEFAULT_SHEET,
    host: str = "127.0.0.1",
    port: int = 8777,
    open_browser: bool = False,
    read_mode: str = "auto",
) -> None:
    directory = directory.resolve()
    positions_path = directory / "positions.json"
    server = ThreadingHTTPServer((host, port), make_handler(directory, excel_path, sheet, positions_path, read_mode))
    bound_host, bound_port = server.server_address[:2]
    url = f"http://{bound_host}:{bound_port}/dashboard.html"
    print(f"serving: {directory}", flush=True)
    print(f"excel: {excel_path}", flush=True)
    print(f"read-mode: {read_mode}", flush=True)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve the A-share option dashboard.")
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--excel", type=Path, default=DEFAULT_EXCEL_PATH)
    parser.add_argument("--sheet", default=DEFAULT_SHEET)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--read-mode", choices=["auto", "live", "saved"], default="auto")
    parser.add_argument("--open", action="store_true", help="Open the dashboard in the default browser.")
    args = parser.parse_args()
    serve(args.directory, args.excel, args.sheet, args.host, args.port, args.open, args.read_mode)


if __name__ == "__main__":
    main()
