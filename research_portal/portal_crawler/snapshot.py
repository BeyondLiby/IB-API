from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


async def save_page_snapshot(page: Any, portal: str, artifacts_dir: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = artifacts_dir / portal / timestamp
    target.mkdir(parents=True, exist_ok=False)

    await page.screenshot(path=str(target / "page.png"), full_page=True)
    (target / "page.html").write_text(await page.content(), encoding="utf-8")
    frame_dir = target / "frames"
    frame_dir.mkdir()
    frames = []
    for index, frame in enumerate(page.frames):
        frame_name = f"{index:02d}"
        frame_metadata = {
            "index": index,
            "name": frame.name,
            "url": frame.url,
        }
        try:
            (frame_dir / f"{frame_name}.html").write_text(
                await frame.content(), encoding="utf-8"
            )
            (frame_dir / f"{frame_name}.txt").write_text(
                await frame.locator("body").inner_text(timeout=5_000), encoding="utf-8"
            )
            try:
                aria = await frame.locator("body").aria_snapshot(timeout=5_000)
                (frame_dir / f"{frame_name}.aria.yml").write_text(
                    aria, encoding="utf-8"
                )
            except Exception as exc:  # Diagnostic output should remain best-effort.
                frame_metadata["aria_error"] = str(exc)
        except Exception as exc:  # Cross-origin or transient frames can disappear.
            frame_metadata["capture_error"] = str(exc)
        frames.append(frame_metadata)

    metadata = {
        "portal": portal,
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "url": page.url,
        "title": await page.title(),
        "frames": frames,
    }
    (target / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return target
