from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

try:
    from .xlsx_reader import XlsxReadError, read_option_chain_from_matrix
except ImportError:  # pragma: no cover - supports direct script execution
    from xlsx_reader import XlsxReadError, read_option_chain_from_matrix


def read_live_option_chain(excel_path: str | Path, sheet_name: str = "创业板", timeout: float = 4.0) -> dict[str, object]:
    """Read an open Excel workbook through COM so unsaved RTD/formula updates are visible."""

    if sys.platform != "win32":
        raise XlsxReadError("Live Excel COM reading is only available on Windows")

    script = Path(__file__).with_name("live_excel_reader.ps1")
    if not script.exists():
        raise XlsxReadError(f"Live Excel reader script not found: {script}")

    command = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script),
        "-WorkbookPath",
        str(Path(excel_path)),
        "-SheetName",
        sheet_name,
    ]
    try:
        completed = subprocess.run(
            command,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise XlsxReadError(f"Live Excel read failed: {exc}") from exc

    if completed.returncode != 0:
        error = (completed.stderr or completed.stdout or "").strip()
        raise XlsxReadError(error or f"Live Excel read failed with exit code {completed.returncode}")

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise XlsxReadError(f"Live Excel returned invalid JSON: {exc}") from exc

    matrix = payload.get("matrix")
    if not isinstance(matrix, list):
        raise XlsxReadError("Live Excel returned no matrix")

    normalized = read_option_chain_from_matrix(
        matrix=matrix,
        source_path=payload.get("source") or excel_path,
        sheet_name=payload.get("sheet") or sheet_name,
        requested_sheet=payload.get("requestedSheet") or sheet_name,
        available_sheets=payload.get("availableSheets") or [sheet_name],
        source_mode="live",
    )
    normalized["updatedAt"] = payload.get("readAt") or normalized.get("updatedAt", "")
    return normalized

