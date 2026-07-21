from __future__ import annotations

import importlib.util
import json
import urllib.error
import urllib.request
from typing import Any

from .config import Settings


def _cdp_status(url: str) -> dict[str, Any]:
    endpoint = url.rstrip("/") + "/json/version"
    try:
        with urllib.request.urlopen(endpoint, timeout=1.5) as response:
            payload = json.load(response)
        return {
            "reachable": True,
            "browser": payload.get("Browser"),
            "websocket": bool(payload.get("webSocketDebuggerUrl")),
        }
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        return {"reachable": False, "reason": str(exc)}


def collect_diagnostics(settings: Settings) -> dict[str, Any]:
    return {
        "playwright_installed": importlib.util.find_spec("playwright") is not None,
        "chrome_path": str(settings.chrome_path) if settings.chrome_path else None,
        "persistent_profile": str(settings.profile_dir),
        "artifacts_dir": str(settings.artifacts_dir),
        "data_dir": str(settings.data_dir),
        "cdp_url": settings.cdp_url,
        "cdp": _cdp_status(settings.cdp_url),
    }

