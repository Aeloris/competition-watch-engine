# -*- coding: utf-8 -*-
"""Planner（Phase 4 实现）：把"跑哪个竞品 × 哪个周期"翻译成采集计划。

职责（README2 §5.2）：输入竞品与周期，输出采集清单 + 增量锚。
- **增量**：since = 该 ISO 周周一起日 —— 只抓"这周起的新内容"，与 diff 记忆呼应（省 token 一环）；
- **确定性**：run_id / fetched_at 可注入，不注入则给稳定默认（周期结束后的周一 09:00，对齐调度 cron）；
- **全量模式**：真实信源下 since=None 即全量；mock 回放里 per-period 用 since=周期起日 + 期过滤，
  全量语义留注释不实现（本阶段只跑周期档）。

周期换算 helper（period_start / period_end_exclusive）是"YYYY-Www ↔ 日期"的唯一入口，
研究者/写档共用，保证同一字符串永远换算出同一日期。
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, time, timedelta

_PERIOD_RE = re.compile(r"^(\d{4})-W(\d{2})$")


def period_start(period: str) -> date:
    """周期 "YYYY-Www" 的周一起日（ISO 周）。非法字符串 → ValueError（fail fast）。"""
    m = _PERIOD_RE.match(period)
    if not m:
        raise ValueError(f"period 必须是 'YYYY-Www'（ISO 周），收到: {period!r}")
    return date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)


def period_end_exclusive(period: str) -> date:
    """周期结束的次日（下周一）——"本周已过完"的天然边界。"""
    return period_start(period) + timedelta(days=7)


def default_fetched_at(period: str) -> datetime:
    """稳定默认抓取时间 = 周期结束后的周一 09:00（对齐 config.schedule.cron '0 9 * * mon'）。"""
    return datetime.combine(period_end_exclusive(period), time(hour=9))


class Planner:
    """采集计划器：竞品 × 周期 → plan（since 增量锚 + run_id + 抓取时间 + 启用的信源）。"""

    def __init__(self, settings) -> None:
        self._settings = settings

    def plan(
        self,
        competitor_id: str,
        period: str,
        *,
        fetched_at: datetime | None = None,
        run_id: str | None = None,
    ) -> dict:
        """生成一次周期运行的采集计划（确定：同输入同输出，除 run_id 缺省随机）。"""
        fetched_at = fetched_at or default_fetched_at(period)
        return {
            "run_id": run_id or uuid.uuid4().hex[:12],
            "competitor_id": competitor_id,
            "period": period,
            "since": period_start(period).isoformat(),          # 增量锚：本周一起日
            "fetched_at": fetched_at.isoformat(),
            "sources_enabled": list(self._settings.sources.enabled),
        }
