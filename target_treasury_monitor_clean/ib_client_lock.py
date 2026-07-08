from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import sys
import tempfile
import time
from typing import Iterator, TextIO

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class IbClientLockBusy(RuntimeError):
    """Raised when another local process is already using the same IB client id."""


@dataclass(frozen=True)
class IbClientLockInfo:
    host: str
    port: int
    client_id: int
    path: Path
    metadata: dict[str, object]


def _safe_component(value: object) -> str:
    text = str(value).strip() or "default"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def ib_client_lock_path(host: str, port: int, client_id: int) -> Path:
    lock_dir = Path(tempfile.gettempdir()) / "ib_api_client_locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"ib_client_{_safe_component(host)}_{int(port)}_{int(client_id)}.lock"


def _try_lock(handle: TextIO) -> bool:
    if os.name == "nt":
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: TextIO) -> None:
    if os.name == "nt":
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_metadata(path: Path) -> dict[str, object]:
    try:
        text = path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_metadata(handle: TextIO, host: str, port: int, client_id: int, purpose: str) -> dict[str, object]:
    metadata: dict[str, object] = {
        "pid": os.getpid(),
        "host": host,
        "port": int(port),
        "client_id": int(client_id),
        "purpose": purpose,
        "hostname": socket.gethostname(),
        "cwd": os.getcwd(),
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        "argv": sys.argv,
    }
    handle.seek(0)
    handle.truncate()
    handle.write(json.dumps(metadata, ensure_ascii=False, indent=2))
    handle.flush()
    os.fsync(handle.fileno())
    return metadata


def format_lock_busy_message(info: IbClientLockInfo) -> str:
    metadata = info.metadata
    pid = metadata.get("pid", "unknown")
    started_at = metadata.get("started_at", "unknown time")
    purpose = metadata.get("purpose", "IB refresh")
    return (
        "IB client refresh is already running for "
        f"{info.host}:{info.port} with client-id {info.client_id}. "
        f"Current owner: pid={pid}, purpose={purpose}, started_at={started_at}. "
        "Wait for the current refresh to finish, or retry with a different --client-id. "
        f"Lock file: {info.path}"
    )


@contextmanager
def acquire_ib_client_lock(
    host: str,
    port: int,
    client_id: int,
    *,
    purpose: str = "IB refresh",
) -> Iterator[IbClientLockInfo]:
    path = ib_client_lock_path(host, port, client_id)
    with path.open("a+", encoding="utf-8") as handle:
        if not _try_lock(handle):
            metadata = _read_metadata(path)
            info = IbClientLockInfo(host=host, port=int(port), client_id=int(client_id), path=path, metadata=metadata)
            raise IbClientLockBusy(format_lock_busy_message(info))

        metadata = _write_metadata(handle, host, port, client_id, purpose)
        info = IbClientLockInfo(host=host, port=int(port), client_id=int(client_id), path=path, metadata=metadata)
        try:
            yield info
        finally:
            handle.seek(0)
            handle.truncate()
            handle.flush()
            _unlock(handle)
