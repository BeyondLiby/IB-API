from __future__ import annotations

from typing import Any

from .jpm import prepare_jpm_page


async def prepare_portal_page(page: Any, portal: str) -> list[str]:
    """Clear known portal overlays before discovery or collection."""
    if portal == "jpm":
        return await prepare_jpm_page(page)
    return []

