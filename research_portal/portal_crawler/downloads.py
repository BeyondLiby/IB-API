from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote


@dataclass(frozen=True)
class DownloadResult:
    external_id: str
    path: Path
    byte_size: int
    sha256: str
    content_type: str
    skipped: bool


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _is_pdf(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open("rb") as source:
        return source.read(5) == b"%PDF-"


def jpm_report_type(title: str) -> str:
    report_type = re.split(r"[:：]", title, maxsplit=1)[0].strip()
    report_type = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", report_type)
    report_type = re.sub(r"\s+", " ", report_type).strip(" .")
    return (report_type or "Research")[:100].rstrip(" .")


def jpm_default_pdf_path(
    documents_dir: Path, published_date: str, title: str
) -> Path:
    report_type = jpm_report_type(title)
    return (
        documents_dir
        / published_date
        / "JPM-报告"
        / f"{published_date}_JPM_{report_type}.pdf"
    )


async def _pdf_viewer_frame(page: Any) -> Any:
    for _ in range(60):
        for frame in page.frames:
            if frame.url.startswith(
                "chrome-extension://mhjfbmdgcfjbbpaeojofohoefgiehjai/index.html"
            ):
                return frame
        await asyncio.sleep(0.25)
    raise RuntimeError("点击 PDF 后未检测到 Chrome PDF viewer。")


async def _pdf_download_button(frame: Any) -> Any:
    selectors = (
        "#download",
        "cr-icon-button#download",
        '[aria-label="下载"]',
        '[aria-label="Download"]',
    )
    for _ in range(40):
        for selector in selectors:
            locator = frame.locator(selector).first
            if await locator.count() and await locator.is_visible():
                return locator
        await asyncio.sleep(0.25)
    raise RuntimeError("Chrome PDF viewer 中未找到下载按钮。")


async def download_jpm_pdf(
    page: Any,
    external_id: str,
    target: Path,
    overwrite: bool = False,
) -> DownloadResult:
    target.parent.mkdir(parents=True, exist_ok=True)

    if not overwrite and _is_pdf(target):
        size, sha256 = _hash_file(target)
        return DownloadResult(
            external_id=external_id,
            path=target,
            byte_size=size,
            sha256=sha256,
            content_type="application/pdf",
            skipped=True,
        )

    reader_url = (
        "https://markets.jpmorgan.com/research/ArticleServlet?doc="
        f"{quote(external_id)}&referrerPortlet=analyst_page"
    )
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            await page.goto(
                reader_url, wait_until="domcontentloaded", timeout=60_000
            )
            pdf_link = page.locator('a[title="View PDF"]').first
            await pdf_link.wait_for(state="visible", timeout=15_000)
            await pdf_link.click()
            viewer_frame = await _pdf_viewer_frame(page)
            download_button = await _pdf_download_button(viewer_frame)

            partial = target.with_suffix(".pdf.part")
            async with page.expect_download(timeout=30_000) as download_info:
                await download_button.click()
            download = await download_info.value
            failure = await download.failure()
            if failure:
                raise RuntimeError(f"浏览器下载失败：{failure}")
            await download.save_as(str(partial))
            if not _is_pdf(partial):
                raise RuntimeError("浏览器保存结果不是有效 PDF。")
            size, sha256 = _hash_file(partial)
            partial.replace(target)
            return DownloadResult(
                external_id=external_id,
                path=target,
                byte_size=size,
                sha256=sha256,
                content_type="application/pdf",
                skipped=False,
            )
        except Exception as exc:
            last_error = exc
            if attempt < 1:
                await asyncio.sleep(2**attempt)

    raise RuntimeError(f"下载 {external_id} 失败：{last_error}")
