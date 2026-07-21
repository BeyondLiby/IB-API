from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date, datetime, timezone
from typing import Sequence
from urllib.parse import quote

from .browser import BrowserSession, BrowserSetupError
from .config import PORTAL_URLS, Settings
from .diagnostics import collect_diagnostics
from .downloads import download_jpm_pdf, jpm_default_pdf_path
from .portals import prepare_portal_page
from .portals.jpm import (
    collect_subscription,
    filter_date_range,
    get_subscriptions_frame,
    list_subscriptions,
    select_subscription,
    subscription_items_per_month,
)
from .snapshot import save_page_snapshot
from .storage import ResearchStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-portal",
        description="使用已授权登录会话采集 JPMorgan/GS research portal。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="检查 Chrome、Playwright 与 CDP 连接状态")

    for command, help_text in (
        ("login", "打开浏览器，手工登录并保存会话"),
        ("snapshot", "打开已登录页面并保存本地诊断快照"),
    ):
        child = subparsers.add_parser(command, help=help_text)
        child.add_argument("portal", choices=sorted(PORTAL_URLS))
        child.add_argument(
            "--mode", choices=("persistent", "cdp"), default="persistent"
        )

    discover = subparsers.add_parser(
        "discover-jpm", help="只读点击一个 JPM subscription 并保存明细快照"
    )
    discover.add_argument("--subscription", default="China Equity Strategy")
    discover.add_argument(
        "--mode", choices=("persistent", "cdp"), default="persistent"
    )

    crawl_jpm = subparsers.add_parser(
        "crawl-jpm", help="逐项采集 JPM subscriptions 的可见研究历史"
    )
    crawl_jpm.add_argument(
        "--subscription",
        action="append",
        help="指定 subscription；可重复传入，省略则采集全部",
    )
    scope = crawl_jpm.add_mutually_exclusive_group(required=True)
    scope.add_argument("--date", help="只保留指定日期，格式 YYYY-MM-DD")
    scope.add_argument("--from-date", help="保留该日期及之后，格式 YYYY-MM-DD")
    scope.add_argument(
        "--all-history", action="store_true", help="滚动到全部可见历史末尾"
    )
    crawl_jpm.add_argument("--to-date", help="保留该日期及之前，格式 YYYY-MM-DD")
    crawl_jpm.add_argument("--max-scrolls", type=int, default=100)
    crawl_jpm.add_argument(
        "--mode", choices=("persistent", "cdp"), default="persistent"
    )

    probe_jpm = subparsers.add_parser(
        "probe-jpm-document", help="探测一篇 JPM 文档的真实响应类型"
    )
    probe_jpm.add_argument("external_id", help="例如 GPS-5369353-0")
    probe_jpm.add_argument(
        "--mode", choices=("persistent", "cdp"), default="persistent"
    )

    download_jpm = subparsers.add_parser(
        "download-jpm", help="从本地索引下载已授权的 JPM PDF"
    )
    download_jpm.add_argument(
        "--subscription",
        action="append",
        help="仅下载指定 subscription；可重复传入",
    )
    download_scope = download_jpm.add_mutually_exclusive_group(required=True)
    download_scope.add_argument("--date", help="只下载指定日期，格式 YYYY-MM-DD")
    download_scope.add_argument(
        "--from-date", help="下载该日期及之后，格式 YYYY-MM-DD"
    )
    download_scope.add_argument(
        "--all-history", action="store_true", help="下载本地索引中的全部 JPM 文档"
    )
    download_jpm.add_argument("--to-date", help="下载该日期及之前，格式 YYYY-MM-DD")
    download_jpm.add_argument("--max-files", type=int)
    download_jpm.add_argument("--overwrite", action="store_true")
    download_jpm.add_argument(
        "--mode", choices=("persistent", "cdp"), default="persistent"
    )

    observe_jpm = subparsers.add_parser(
        "observe-jpm-schedule",
        help="逐项读取 JPM subscription 页面显示的 Items/Month",
    )
    observe_jpm.add_argument(
        "--mode", choices=("persistent", "cdp"), default="persistent"
    )

    return parser


async def _login(settings: Settings, portal: str, mode: str) -> int:
    async with BrowserSession(settings, mode=mode) as browser:  # type: ignore[arg-type]
        page = await browser.open(PORTAL_URLS[portal])
        print(f"已打开 {portal.upper()}: {page.url}")
        print("请在浏览器中完成登录并确认目标页面可见。")
        await asyncio.to_thread(input, "完成后回到此窗口按 Enter 保存并退出：")
        actions = await prepare_portal_page(page, portal)
        if actions:
            print(f"已自动处理页面前置状态：{', '.join(actions)}")
        print(f"会话已保存在：{settings.profile_dir}")
    return 0


async def _snapshot(settings: Settings, portal: str, mode: str) -> int:
    async with BrowserSession(settings, mode=mode) as browser:  # type: ignore[arg-type]
        page = await browser.open(PORTAL_URLS[portal])
        await page.wait_for_timeout(3_000)
        actions = await prepare_portal_page(page, portal)
        if actions:
            print(f"已自动处理页面前置状态：{', '.join(actions)}")
        target = await save_page_snapshot(page, portal, settings.artifacts_dir)
        print(f"页面快照已保存：{target}")
    return 0


async def _discover_jpm(settings: Settings, subscription: str, mode: str) -> int:
    async with BrowserSession(settings, mode=mode) as browser:  # type: ignore[arg-type]
        page = await browser.open(PORTAL_URLS["jpm"])
        await page.wait_for_timeout(3_000)
        await prepare_portal_page(page, "jpm")
        frame = await get_subscriptions_frame(page)
        subscriptions = await list_subscriptions(frame)

        network_events: list[dict[str, object]] = []

        def record_response(response: object) -> None:
            request = response.request  # type: ignore[attr-defined]
            if request.resource_type in {"xhr", "fetch"}:
                network_events.append(
                    {
                        "status": response.status,  # type: ignore[attr-defined]
                        "method": request.method,
                        "resource_type": request.resource_type,
                        "url": response.url,  # type: ignore[attr-defined]
                    }
                )

        page.on("response", record_response)
        await select_subscription(frame, subscription)
        await page.wait_for_timeout(6_000)
        target = await save_page_snapshot(page, "jpm", settings.artifacts_dir)
        discovery = {
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
            "selected_subscription": subscription,
            "subscriptions": subscriptions,
            "network_events": network_events,
        }
        (target / "jpm-discovery.json").write_text(
            json.dumps(discovery, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已识别 {len(subscriptions)} 个 subscriptions。")
        print(f"已选择：{subscription}")
        print(f"探测快照已保存：{target}")
    return 0


def _iso_date(value: str | None, option: str) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError(f"{option} 必须是 YYYY-MM-DD：{value}") from exc


async def _crawl_jpm(settings: Settings, args: argparse.Namespace) -> int:
    exact_date = _iso_date(args.date, "--date")
    from_date = exact_date or _iso_date(args.from_date, "--from-date")
    to_date = exact_date or _iso_date(args.to_date, "--to-date")
    if from_date and to_date and from_date > to_date:
        raise ValueError("--from-date 不能晚于 --to-date。")
    if args.max_scrolls < 1:
        raise ValueError("--max-scrolls 必须至少为 1。")

    parameters = {
        "subscriptions": args.subscription,
        "date": exact_date,
        "from_date": from_date,
        "to_date": to_date,
        "all_history": args.all_history,
        "max_scrolls": args.max_scrolls,
    }
    database_path = settings.data_dir / "research.sqlite"
    output_dir = settings.data_dir / "runs"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / datetime.now(timezone.utc).strftime(
        "jpm_%Y%m%dT%H%M%SZ.json"
    )

    with ResearchStore(database_path) as store:
        run_id = store.start_run("jpm", parameters)
        total_items: list[dict[str, object]] = []
        summaries: list[dict[str, object]] = []
        try:
            async with BrowserSession(settings, mode=args.mode) as browser:
                page = await browser.open(PORTAL_URLS["jpm"])
                await page.wait_for_timeout(3_000)
                await prepare_portal_page(page, "jpm")
                frame = await get_subscriptions_frame(page)
                available = await list_subscriptions(frame)
                selected = args.subscription or available
                unknown = [name for name in selected if name not in available]
                if unknown:
                    raise ValueError(
                        "以下 subscriptions 不存在：" + ", ".join(unknown)
                    )

                for subscription in selected:
                    print(f"正在采集：{subscription}", flush=True)
                    await select_subscription(frame, subscription)
                    result = await collect_subscription(
                        page=page,
                        frame=frame,
                        subscription=subscription,
                        stop_at_date=from_date,
                        max_scrolls=args.max_scrolls,
                    )
                    filtered = filter_date_range(result.items, from_date, to_date)
                    store.upsert_items(filtered)
                    total_items.extend(item.as_dict() for item in filtered)
                    summaries.append(
                        {
                            "subscription": subscription,
                            "loaded": len(result.items),
                            "stored": len(filtered),
                            "scrolls": result.scrolls,
                            "stop_reason": result.stop_reason,
                        }
                    )
                    print(
                        f"  加载 {len(result.items)}，保存 {len(filtered)}，"
                        f"停止原因 {result.stop_reason}",
                        flush=True,
                    )

            payload = {
                "portal": "jpm",
                "run_id": run_id,
                "parameters": parameters,
                "summaries": summaries,
                "items": total_items,
            }
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            store.finish_run(run_id, "completed", len(total_items))
        except Exception as exc:
            store.finish_run(run_id, "failed", len(total_items), str(exc))
            raise

    print(f"JPM 采集完成：{len(total_items)} 条 subscription-document 记录")
    print(f"SQLite：{database_path}")
    print(f"本次 JSON：{output_path}")
    return 0


async def _probe_jpm_document(
    settings: Settings, external_id: str, mode: str
) -> int:
    if not external_id or any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in external_id
    ):
        raise ValueError("external_id 只能包含字母、数字、连字符和下划线。")

    urls = {
        "reader": (
            "https://markets.jpmorgan.com/research/ArticleServlet?doc="
            f"{quote(external_id)}&referrerPortlet=analyst_page"
        ),
        "pdf": (
            "https://markets.jpmorgan.com/research/ArticleServlet?doc="
            f"{quote(external_id + '.pdf')}"
        ),
    }
    async with BrowserSession(settings, mode=mode) as browser:  # type: ignore[arg-type]
        page = await browser.open(PORTAL_URLS["jpm"])
        await page.wait_for_timeout(2_000)
        await prepare_portal_page(page, "jpm")
        target_dir = settings.artifacts_dir / "jpm" / "document-probes"
        target_dir.mkdir(parents=True, exist_ok=True)
        for label, url in urls.items():
            response = await browser.context.request.get(url, timeout=120_000)
            body = await response.body()
            content_type = response.headers.get("content-type", "").lower()
            if body.startswith(b"%PDF") or "application/pdf" in content_type:
                suffix = ".pdf"
            elif "html" in content_type or body.lstrip().startswith(b"<"):
                suffix = ".html"
            else:
                suffix = ".bin"
            target = target_dir / f"{external_id}_{label}{suffix}"
            target.write_bytes(body)
            print(f"[{label}] status={response.status}")
            print(f"[{label}] final_url={response.url}")
            print(f"[{label}] content_type={content_type}")
            print(f"[{label}] bytes={len(body)}")
            print(f"[{label}] saved={target}")

        render_page = await browser.context.new_page()
        network_events: list[dict[str, object]] = []
        capture_tasks: list[asyncio.Task[None]] = []

        async def capture_response(response: object) -> None:
            content_type = response.headers.get("content-type", "").lower()  # type: ignore[attr-defined]
            resource_type = response.request.resource_type  # type: ignore[attr-defined]
            response_url = response.url  # type: ignore[attr-defined]
            if (
                "pdf" not in content_type
                and "articleservlet" not in response_url.lower()
                and resource_type not in {"document", "xhr", "fetch"}
            ):
                return
            event: dict[str, object] = {
                "status": response.status,  # type: ignore[attr-defined]
                "resource_type": resource_type,
                "content_type": content_type,
                "url": response_url,
            }
            if "pdf" in content_type:
                try:
                    pdf_body = await response.body()  # type: ignore[attr-defined]
                    event["bytes"] = len(pdf_body)
                    if pdf_body.startswith(b"%PDF-"):
                        pdf_target = target_dir / f"{external_id}_captured.pdf"
                        pdf_target.write_bytes(pdf_body)
                        event["saved"] = str(pdf_target)
                except Exception as exc:
                    event["body_error"] = str(exc)
            network_events.append(event)

        def schedule_capture(response: object) -> None:
            capture_tasks.append(asyncio.create_task(capture_response(response)))

        render_page.on("response", schedule_capture)
        await render_page.goto(
            urls["reader"], wait_until="domcontentloaded", timeout=120_000
        )
        pdf_button = render_page.locator('a[title="View PDF"]').first
        await pdf_button.wait_for(state="visible", timeout=30_000)
        await pdf_button.click()
        await render_page.wait_for_timeout(12_000)
        if capture_tasks:
            await asyncio.gather(*capture_tasks, return_exceptions=True)
        render_target = await save_page_snapshot(
            render_page, "jpm", settings.artifacts_dir
        )
        (render_target / "pdf-render-network.json").write_text(
            json.dumps(network_events, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"[render] url={render_page.url}")
        print(f"[render] network={render_target / 'pdf-render-network.json'}")
        print(f"[render] snapshot={render_target}")
    return 0


async def _download_jpm(settings: Settings, args: argparse.Namespace) -> int:
    exact_date = _iso_date(args.date, "--date")
    from_date = exact_date or _iso_date(args.from_date, "--from-date")
    to_date = exact_date or _iso_date(args.to_date, "--to-date")
    if from_date and to_date and from_date > to_date:
        raise ValueError("--from-date 不能晚于 --to-date。")
    if args.max_files is not None and args.max_files < 1:
        raise ValueError("--max-files 必须至少为 1。")

    database_path = settings.data_dir / "research.sqlite"
    with ResearchStore(database_path) as store:
        documents = store.list_documents(
            portal="jpm",
            from_date=from_date,
            to_date=to_date,
            subscriptions=args.subscription,
        )
        if args.max_files:
            documents = documents[: args.max_files]
        if not documents:
            print("本地索引中没有符合条件的 JPM 文档。")
            return 0

        completed = 0
        skipped = 0
        failures: list[str] = []
        async with BrowserSession(settings, mode=args.mode) as browser:
            page = await browser.open(PORTAL_URLS["jpm"])
            await page.wait_for_timeout(2_000)
            await prepare_portal_page(page, "jpm")
            for index, document in enumerate(documents, start=1):
                external_id = str(document["external_id"])
                print(
                    f"[{index}/{len(documents)}] {document['published_date']} "
                    f"{external_id}",
                    flush=True,
                )
                try:
                    existing = store.get_file("jpm", external_id)
                    if existing:
                        target = settings.project_root / str(existing["file_path"])
                    else:
                        base_target = jpm_default_pdf_path(
                            documents_dir=settings.data_dir,
                            published_date=str(document["published_date"]),
                            title=str(document["title"]),
                        )
                        target = base_target
                        collision_index = 2
                        while True:
                            relative_candidate = str(
                                target.relative_to(settings.project_root)
                            )
                            owner = store.file_owner("jpm", relative_candidate)
                            if (
                                (owner is None and not target.exists())
                                or owner == external_id
                            ):
                                break
                            target = base_target.with_name(
                                f"{base_target.stem}_{collision_index}.pdf"
                            )
                            collision_index += 1
                    result = await download_jpm_pdf(
                        page=page,
                        external_id=external_id,
                        target=target,
                        overwrite=args.overwrite,
                    )
                    relative_path = result.path.relative_to(settings.project_root)
                    store.record_file(
                        portal="jpm",
                        external_id=external_id,
                        file_path=str(relative_path),
                        byte_size=result.byte_size,
                        sha256=result.sha256,
                        content_type=result.content_type,
                    )
                    completed += 1
                    skipped += int(result.skipped)
                    await asyncio.sleep(0.5)
                except Exception as exc:
                    store.record_download_failure("jpm", external_id, str(exc))
                    failures.append(f"{external_id}: {exc}")
                    print(f"  失败：{exc}", file=sys.stderr, flush=True)

    print(
        f"JPM PDF：成功 {completed}，其中已存在 {skipped}，失败 {len(failures)}"
    )
    print(f"目录：{settings.data_dir}")
    if failures:
        print("失败列表：", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    return 0


async def _observe_jpm_schedule(settings: Settings, mode: str) -> int:
    observations: list[dict[str, object]] = []
    async with BrowserSession(settings, mode=mode) as browser:  # type: ignore[arg-type]
        page = await browser.open(PORTAL_URLS["jpm"])
        await page.wait_for_timeout(3_000)
        await prepare_portal_page(page, "jpm")
        frame = await get_subscriptions_frame(page)
        subscriptions = await list_subscriptions(frame)
        for index, subscription in enumerate(subscriptions, start=1):
            await select_subscription(frame, subscription)
            items_per_month = await subscription_items_per_month(frame)
            observations.append(
                {
                    "subscription": subscription,
                    "items_per_month": items_per_month,
                }
            )
            print(
                f"[{index}/{len(subscriptions)}] {subscription}: "
                f"{items_per_month if items_per_month is not None else 'N/A'} Items/Month",
                flush=True,
            )

    payload = {
        "portal": "jpm",
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "subscriptions": observations,
    }
    output = settings.data_dir / "jpm_schedule_observations.json"
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"观察结果：{output}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = Settings.load()

    if args.command == "doctor":
        print(json.dumps(collect_diagnostics(settings), ensure_ascii=False, indent=2))
        return 0

    try:
        if args.command == "login":
            return asyncio.run(_login(settings, args.portal, args.mode))
        if args.command == "snapshot":
            return asyncio.run(_snapshot(settings, args.portal, args.mode))
        if args.command == "discover-jpm":
            return asyncio.run(
                _discover_jpm(settings, args.subscription, args.mode)
            )
        if args.command == "crawl-jpm":
            return asyncio.run(_crawl_jpm(settings, args))
        if args.command == "probe-jpm-document":
            return asyncio.run(
                _probe_jpm_document(settings, args.external_id, args.mode)
            )
        if args.command == "download-jpm":
            return asyncio.run(_download_jpm(settings, args))
        if args.command == "observe-jpm-schedule":
            return asyncio.run(_observe_jpm_schedule(settings, args.mode))
    except ValueError as exc:
        print(f"参数或页面数据错误：{exc}", file=sys.stderr)
        return 2
    except BrowserSetupError as exc:
        print(f"浏览器环境错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消。")
        return 130

    return 1
