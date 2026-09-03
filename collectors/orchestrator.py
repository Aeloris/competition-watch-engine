# -*- coding: utf-8 -*-
"""采集编排：把 Workers 并发散出去、汇成 CollectSummary（fan-out / fan-in + 部分失败）。

- collect_competitor：单竞品跨 enabled 信源**并发**采集，asyncio.Semaphore(concurrency_limit) 限流
  （控 QPS/成本，README2 §5.3）；单源失败经 WorkerOutcome.ok=False 汇聚进 failures，编排层不抛；
- collect_all：遍历竞品（竞品间串行即可，跨竞品并发是 Phase 4 LangGraph 编排的活），
  竞品缺省从 fixtures 扫（memory.corpus.list_competitors）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Awaitable, Iterable

from config.settings import Settings
from collectors.factory import build_collectors
from collectors.schemas import CollectSummary, SourceStats
from collectors.worker import CollectorWorker
from memory import list_competitors


async def _gather_bounded(coros: Iterable[Awaitable], limit: int) -> list:
    """asyncio.Semaphore 限流的 gather：同时最多 limit 个协程在跑。"""
    sem = asyncio.Semaphore(limit)

    async def _guarded(coro) -> object:
        async with sem:
            return await coro

    return await asyncio.gather(*(_guarded(c) for c in coros))


async def collect_competitor(
    settings: Settings,
    workers: dict[str, CollectorWorker],
    competitor_id: str,
    *,
    fetched_at: datetime | None = None,
    since: datetime | None = None,
) -> CollectSummary:
    """单竞品跨全部 enabled 信源并发采集，汇总 + 失败清单。不落库。"""
    outcomes = await _gather_bounded(
        (w.run(competitor_id, since=since, fetched_at=fetched_at) for w in workers.values()),
        limit=settings.sources.concurrency_limit,
    )
    by_source: dict[str, SourceStats] = {}
    failures: list[str] = []
    cards: list = []
    for o in outcomes:  # gather 保序 → by_source 顺序与 workers/enabled 一致
        by_source[o.kind] = SourceStats(
            kind=o.kind,
            fetched=o.fetched,
            cards=len(o.cards),
            failed=0 if o.ok else 1,
        )
        if o.ok:
            cards.extend(o.cards)
        else:
            failures.append(f"{o.kind}/{competitor_id}: {o.error}")
    # 跨源并集按 (原文时间, url) 升序 → 确定性（与 memory.corpus 同口径）
    cards.sort(key=lambda c: (c.published_at.isoformat(), c.source_url))
    return CollectSummary(competitor_id=competitor_id, by_source=by_source, cards=cards, failures=failures)


async def collect_all(
    settings: Settings,
    competitors: list[str] | None = None,
    *,
    fetched_at: datetime | None = None,
    since: datetime | None = None,
) -> dict[str, CollectSummary]:
    """全部竞品各采一轮。竞品缺省扫 fixtures（MockSource.mock_dir）。"""
    workers = build_collectors(settings)
    if competitors is None:
        competitors = list_competitors(settings.repo_root / settings.sources.mock_dir)
    summaries: dict[str, CollectSummary] = {}
    for competitor_id in competitors:
        summaries[competitor_id] = await collect_competitor(
            settings, workers, competitor_id, fetched_at=fetched_at, since=since
        )
    return summaries
