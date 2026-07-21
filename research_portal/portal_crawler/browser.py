from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .config import Settings


BrowserMode = Literal["persistent", "cdp"]


class BrowserSetupError(RuntimeError):
    pass


@dataclass
class BrowserSession:
    settings: Settings
    mode: BrowserMode = "persistent"

    def __post_init__(self) -> None:
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None

    async def __aenter__(self) -> "BrowserSession":
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise BrowserSetupError(
                "Playwright 尚未安装，请先运行 .\\setup.ps1。"
            ) from exc

        self.settings.ensure_local_dirs()
        self._playwright = await async_playwright().start()

        try:
            if self.mode == "persistent":
                if not self.settings.chrome_path:
                    raise BrowserSetupError(
                        "未找到 Google Chrome；可用 RESEARCH_PORTAL_CHROME 指定 chrome.exe。"
                    )
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(self.settings.profile_dir),
                    executable_path=str(self.settings.chrome_path),
                    headless=False,
                    no_viewport=True,
                    accept_downloads=True,
                )
            else:
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    self.settings.cdp_url
                )
                if not self._browser.contexts:
                    raise BrowserSetupError("CDP 浏览器没有可用的 browser context。")
                self._context = self._browser.contexts[0]
        except Exception:
            await self._playwright.stop()
            self._playwright = None
            raise

        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        # Never close an attached CDP browser owned by the user.
        if self.mode == "persistent" and self._context is not None:
            await self._context.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def page_for(self, url: str) -> Any:
        if self._context is None:
            raise RuntimeError("BrowserSession 尚未启动。")

        hostname = url.split("/", 3)[2]
        for page in self._context.pages:
            if hostname in page.url:
                return page

        return await self._context.new_page()

    @property
    def context(self) -> Any:
        if self._context is None:
            raise RuntimeError("BrowserSession 尚未启动。")
        return self._context

    async def open(self, url: str) -> Any:
        page = await self.page_for(url)
        await page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        return page
