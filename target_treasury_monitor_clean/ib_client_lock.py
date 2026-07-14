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


def ib_client_lock_metadata_path(lock_path: Path) -> Path:
    return lock_path.with_name(f"{lock_path.name}.json")


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
    metadata_path = ib_client_lock_metadata_path(path)
    try:
        text = metadata_path.read_text(encoding="utf-8").strip()
        return json.loads(text) if text else {}
    except (OSError, json.JSONDecodeError):
        pass
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
        "active": True,
    }
    metadata_path = ib_client_lock_metadata_path(Path(handle.name))
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def _mark_metadata_released(path: Path, metadata: dict[str, object]) -> None:
    if not metadata:
        return
    released = dict(metadata)
    released["active"] = False
    released["released_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    try:
        ib_client_lock_metadata_path(path).write_text(
            json.dumps(released, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError:
        return


def format_lock_busy_message(info: IbClientLockInfo) -> str:
    metadata = info.metadata
    pid = metadata.get("pid", "unknown")
    started_at = metadata.get("started_at", "unknown time")
    purpose = metadata.get("purpose", "IB refresh")
    owner = (
        f"pid={pid}, purpose={purpose}, started_at={started_at}"
        if metadata
        else "metadata unavailable; the lock may be held by an older process or a process that is just exiting"
    )
    return (
        "IB client refresh is already running for "
        f"{info.host}:{info.port} with client-id {info.client_id}. "
        f"Current owner: {owner}. "
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
        locked = False
        metadata: dict[str, object] = {}
        for attempt in range(4):
            if _try_lock(handle):
                locked = True
                break
            metadata = _read_metadata(path)
            if metadata or attempt == 3:
                break
            time.sleep(0.15)
        if not locked:
            info = IbClientLockInfo(host=host, port=int(port), client_id=int(client_id), path=path, metadata=metadata)
            raise IbClientLockBusy(format_lock_busy_message(info))

        metadata = _write_metadata(handle, host, port, client_id, purpose)
        info = IbClientLockInfo(host=host, port=int(port), client_id=int(client_id), path=path, metadata=metadata)
        try:
            yield info
        finally:
            _mark_metadata_released(path, metadata)
            _unlock(handle)
