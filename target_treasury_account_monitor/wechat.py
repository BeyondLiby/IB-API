from __future__ import annotations

import json
import math
from typing import Any
from urllib import request

import pandas as pd

try:
    from .config import MonitorSettings
    from .greeks import greek_totals
    from .utils import fmt_money, fmt_number, summary_value
except ImportError:
    from config import MonitorSettings
    from greeks import greek_totals
    from utils import fmt_money, fmt_number, summary_value


def build_wechat_text(settings: MonitorSettings, summary: pd.DataFrame, frame: pd.DataFrame) -> str:
    """生成企业微信机器人使用的账户快照文本。"""
    totals = greek_totals(frame)
    system = totals.iloc[0] if not totals.empty else {}
    return "\n".join(
        [
            f"Target treasury monitor: {settings.account}",
            f"Time: {pd.Timestamp.now(tz='Asia/Shanghai').strftime('%Y-%m-%d %H:%M:%S')}",
            f"NetLiq: {fmt_money(summary_value(summary, 'NetLiquidation'))}",
            f"ExcessLiq: {fmt_money(summary_value(summary, 'ExcessLiquidity'))}",
            f"AvailableFunds: {fmt_money(summary_value(summary, 'AvailableFunds'))}",
            f"Treasury positions: {len(frame)}",
            f"System delta(multiplier): {fmt_number(system.get('deltaMultiplier', math.nan), 2)}",
            f"System gamma(multiplier): {fmt_number(system.get('gammaMultiplier', math.nan), 2)}",
            f"System theta(multiplier): {fmt_number(system.get('thetaMultiplier', math.nan), 2)}",
            f"System vega(multiplier): {fmt_number(system.get('vegaMultiplier', math.nan), 2)}",
        ]
    )


def post_wechat_text(webhook_url: str, text: str) -> tuple[bool, str]:
    """通过企业微信机器人 webhook 发送文本消息。"""
    payload = json.dumps({"msgtype": "text", "text": {"content": text}}, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=8) as resp:
            return 200 <= resp.status < 300, resp.read().decode("utf-8", errors="replace")
    except Exception as exc:
        return False, str(exc)


def push_wechat_snapshot(settings: MonitorSettings, summary: pd.DataFrame, frame: pd.DataFrame) -> dict[str, Any]:
    """生成并发送当前账户快照。"""
    text = build_wechat_text(settings, summary, frame)
    ok, detail = post_wechat_text(settings.wechat_webhook_url, text)
    return {"ok": ok, "detail": detail[-300:]}
