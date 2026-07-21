from __future__ import annotations

import asyncio
import re
from datetime import datetime
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from ..models import CollectionResult, ResearchItem


WELCOME_TITLE = "Welcome to My Research Subscriptions"
SUBSCRIPTIONS_FRAME_TITLE = "My Research Subscriptions"


async def prepare_jpm_page(page: Any) -> list[str]:
    actions: list[str] = []
    surfaces = [page, *[frame for frame in page.frames if frame != page.main_frame]]
    for surface in surfaces:
        welcome_title = surface.get_by_text(WELCOME_TITLE, exact=True).first
        try:
            await welcome_title.wait_for(state="visible", timeout=2_500)
        except PlaywrightTimeoutError:
            continue

        close_button = surface.get_by_role("button", name="Close", exact=True).last
        try:
            await close_button.wait_for(state="visible", timeout=3_000)
            await close_button.click()
        except PlaywrightTimeoutError:
            # Some JPM releases expose only the top-right icon as an accessible Close button.
            icon_button = surface.locator(
                "button[aria-label='Close'], [role='button'][aria-label='Close']"
            ).last
            await icon_button.click(timeout=3_000)

        try:
            await welcome_title.wait_for(state="hidden", timeout=5_000)
        except PlaywrightTimeoutError:
            pass
        actions.append("closed_jpm_welcome_modal")
        break

    return actions


async def get_subscriptions_frame(page: Any) -> Any:
    iframe = page.locator(f'iframe[title="{SUBSCRIPTIONS_FRAME_TITLE}"]').first
    await iframe.wait_for(state="attached", timeout=30_000)
    frame = iframe.content_frame
    if frame is None:
        raise RuntimeError("JPM subscriptions iframe 尚未就绪。")
    await frame.locator('[data-test-id="subscription-panel-edit"]').first.wait_for(
        state="visible", timeout=30_000
    )
    return frame


async def list_subscriptions(frame: Any) -> list[str]:
    labels = frame.locator(
        '[data-test-id="subscription-panel-edit"] > label'
    )
    return [text.strip() for text in await labels.all_inner_texts() if text.strip()]


async def select_subscription(frame: Any, subscription: str) -> None:
    items = frame.locator('[data-test-id="subscription-panel-edit"]')
    for index in range(await items.count()):
        item = items.nth(index)
        label = (await item.locator("label").inner_text()).strip()
        if label == subscription:
            await item.click()
            for _ in range(120):
                titled = frame.locator("[title]")
                for title_index in range(await titled.count()):
                    candidate = titled.nth(title_index)
                    if (
                        await candidate.get_attribute("title") == subscription
                        and await candidate.is_visible()
                    ):
                        loading = frame.locator('[aria-label="loading"]')
                        try:
                            await loading.last.wait_for(state="hidden", timeout=30_000)
                        except PlaywrightTimeoutError:
                            pass
                        return
                await asyncio.sleep(0.25)
            raise RuntimeError(
                f"点击后未等到 JPM subscription 明细加载：{subscription}"
            )
    raise ValueError(f"未找到 JPM subscription：{subscription}")


def _parse_published_date(raw: str) -> str:
    for date_format in ("%d %b %y", "%d %b %Y"):
        try:
            return datetime.strptime(raw, date_format).date().isoformat()
        except ValueError:
            continue
    raise ValueError(f"无法解析 JPM 日期：{raw}")


async def _visible_articles(frame: Any, subscription: str) -> list[ResearchItem]:
    raw_items = await frame.locator("div.force-word-break").evaluate_all(
        """
        cards => cards.map(card => {
          const link = card.querySelector('a[href*="/research/ArticleServlet?doc="]');
          if (!link) return null;
          const url = new URL(link.getAttribute('href'), document.baseURI).href;
          const summaryLink = card.querySelector('p a[href*="/research/ArticleServlet?doc="]');
          const authors = Array.from(
            card.querySelectorAll('ul[aria-label*="Analysts"] li')
          ).map(node => node.textContent.trim()).filter(Boolean);
          const match = card.innerText.match(
            /(\\d{1,2}\\s+[A-Za-z]{3}\\s+\\d{2,4})\\s*\\|\\s*Pages?\\s*(\\d+)/
          );
          return {
            external_id: new URL(url).searchParams.get('doc'),
            title: link.textContent.trim(),
            summary: summaryLink ? summaryLink.textContent.trim() : '',
            published_date_raw: match ? match[1] : '',
            pages: match ? Number(match[2]) : null,
            authors,
            url,
          };
        }).filter(Boolean)
        """
    )
    items = []
    for item in raw_items:
        external_id = item.get("external_id")
        published_raw = item.get("published_date_raw")
        if not external_id or not published_raw:
            continue
        items.append(
            ResearchItem(
                portal="jpm",
                external_id=external_id,
                subscription=subscription,
                title=item["title"],
                summary=item["summary"],
                published_date=_parse_published_date(published_raw),
                url=item["url"],
                pages=item["pages"],
                authors=tuple(item["authors"]),
            )
        )
    return items


async def collect_subscription(
    page: Any,
    frame: Any,
    subscription: str,
    stop_at_date: str | None,
    max_scrolls: int,
) -> CollectionResult:
    preview_link = frame.locator(
        'a[href*="/research/ArticleServlet?doc="]'
    ).first
    await preview_link.wait_for(state="visible", timeout=30_000)
    scroller = frame.locator("div.simplebar-content-wrapper").filter(
        has=frame.locator('a[href*="/research/ArticleServlet?doc="]')
    ).last
    await scroller.wait_for(state="visible", timeout=10_000)

    collected: dict[str, ResearchItem] = {}
    stable_rounds = 0
    previous_height = -1
    stop_reason = "max_scrolls"
    performed_scrolls = 0

    for scroll_index in range(max_scrolls + 1):
        for item in await _visible_articles(frame, subscription):
            collected[item.external_id] = item

        dates = [item.published_date for item in collected.values()]
        if stop_at_date and dates and min(dates) <= stop_at_date:
            stop_reason = "date_reached"
            break
        if scroll_index == max_scrolls:
            break

        metrics = await scroller.evaluate(
            """
            element => {
              const before = element.scrollTop;
              element.scrollTop = element.scrollHeight;
              return {
                before,
                after: element.scrollTop,
                height: element.scrollHeight,
                client_height: element.clientHeight,
              };
            }
            """
        )
        performed_scrolls += 1
        before_count = len(collected)
        await asyncio.sleep(1.5)
        for item in await _visible_articles(frame, subscription):
            collected[item.external_id] = item

        at_bottom = metrics["after"] + metrics["client_height"] >= metrics["height"] - 2
        if (
            len(collected) == before_count
            and at_bottom
            and metrics["height"] == previous_height
        ):
            stable_rounds += 1
        else:
            stable_rounds = 0
        previous_height = metrics["height"]

        if stable_rounds >= 2:
            stop_reason = "visible_history_exhausted"
            break

    ordered = tuple(
        sorted(
            collected.values(),
            key=lambda item: (item.published_date, item.external_id),
            reverse=True,
        )
    )
    return CollectionResult(
        items=ordered, scrolls=performed_scrolls, stop_reason=stop_reason
    )


async def subscription_items_per_month(frame: Any) -> int | None:
    preview = frame.get_by_text("Content Preview", exact=True).first
    try:
        await preview.wait_for(state="visible", timeout=15_000)
        header_text = await preview.locator("xpath=..").inner_text()
    except PlaywrightTimeoutError:
        return None
    match = re.search(r"(\d+)\s*Items?\s*/\s*Month", header_text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def filter_date_range(
    items: tuple[ResearchItem, ...],
    from_date: str | None,
    to_date: str | None,
) -> list[ResearchItem]:
    return [
        item
        for item in items
        if (from_date is None or item.published_date >= from_date)
        and (to_date is None or item.published_date <= to_date)
    ]
