# -*- coding: utf-8 -*-
"""Phase 2 Researchers：build_collectors 覆盖 enabled、并发采集计数/溯源/确定性、
单源失败隔离（BoomAdapter 注入）、since 增量、collect_all 双竞品、信号量限流峰值不超限。
"""
import asyncio
from datetime import datetime

from config.settings import get_settings
from collectors import (
    CollectSummary,
    CollectorWorker,
    build_collectors,
    collect_all,
    collect_competitor,
)
from collectors.orchestrator import _gather_bounded
from memory import FactCard
from sources import SourceAdapter

SETTINGS = get_settings()
FETCHED = datetime(2026, 8, 31, 9, 0, 0)
SINCE = datetime(2026, 8, 24, 0, 0)  # 2026-W35 起点


def _run(coro):
    return asyncio.run(coro)


def _sum(competitor: str) -> CollectSummary:
    return _run(
        collect_competitor(SETTINGS, build_collectors(SETTINGS), competitor, fetched_at=FETCHED)
    )


# ---------- 装配与基础采集 ----------

def test_build_collectors_covers_enabled_kinds():
    workers = build_collectors(SETTINGS)
    assert set(workers) == {"website", "news", "rss"}
    assert all(isinstance(w, CollectorWorker) for w in workers.values())


def test_collect_counts_per_source():
    summary = _sum("crm_alpha")
    assert summary.failures == []
    assert len(summary.cards) == 7
    assert {k: s.fetched for k, s in summary.by_source.items()} == {
        "website": 2,
        "news": 2,
        "rss": 3,
    }
    # by_source 计数与 cards 实际一致
    assert {k: s.cards for k, s in summary.by_source.items()} == {
        "website": 2,
        "news": 2,
        "rss": 3,
    }


def test_cards_carry_source_url_and_injected_fetched_at():
    summary = _sum("bi_beta")
    assert all(c.source_url.startswith("http") for c in summary.cards)
    assert all(c.fetched_at == FETCHED for c in summary.cards)  # fetched_at 可注入 → 确定性


def test_cards_have_derived_id_and_digest():
    summary = _sum("crm_alpha")
    assert all(isinstance(c.id, str) and len(c.id) == 40 for c in summary.cards)
    assert all(isinstance(c.digest, str) and len(c.digest) == 40 for c in summary.cards)


def test_cards_sorted_by_published_at():
    summary = _sum("crm_alpha")
    times = [c.published_at for c in summary.cards]
    assert times == sorted(times)


def test_collect_is_replayable_deterministic():
    assert _sum("crm_alpha") == _sum("crm_alpha")


# ---------- 失败隔离（项目承诺：单源挂了 ≠ 全流程崩） ----------

class _BoomAdapter(SourceAdapter):
    """测试专用：fetch 必抛 → 证明单源失败被隔离、不进 cards、不中断其它源。"""

    name = "boom"

    async def fetch(self, competitor_id: str, kind: str, *, since: datetime | None = None):
        raise ConnectionError("simulated source outage")


def test_single_source_failure_is_isolated():
    workers = build_collectors(SETTINGS)
    workers["rss"] = CollectorWorker(_BoomAdapter(), "rss", max_retries=1)  # 只让 rss 挂
    summary = _run(
        collect_competitor(SETTINGS, workers, "crm_alpha", fetched_at=FETCHED)
    )  # 不抛异常
    # 健康源照常出卡；失败源准确记账
    assert {k: s.failed for k, s in summary.by_source.items()} == {
        "website": 0,
        "news": 0,
        "rss": 1,
    }
    assert len(summary.cards) == 4  # website 2 + news 2（rss 被隔离）
    assert len(summary.failures) == 1
    assert summary.failures[0].startswith("rss/crm_alpha")
    assert "ConnectionError" in summary.failures[0]


# ---------- 增量采集 ----------

def test_since_filters_to_latest_week():
    workers = build_collectors(SETTINGS)
    summary = _run(
        collect_competitor(SETTINGS, workers, "crm_alpha", since=SINCE, fetched_at=FETCHED)
    )
    assert summary.cards and all(c.published_at >= SINCE for c in summary.cards)
    assert len(summary.cards) == 5  # 只采到 W35
    assert summary.by_source["rss"].cards == 2  # rss 08-25 / 08-29


# ---------- collect_all ----------

def test_collect_all_returns_both_competitors():
    summaries = _run(collect_all(SETTINGS, fetched_at=FETCHED))
    assert set(summaries) == {"bi_beta", "crm_alpha"}
    assert sum(len(s.cards) for s in summaries.values()) == 14  # 7 + 7
    assert all(s.failures == [] for s in summaries.values())


# ---------- 信号量限流：峰值并发不超限 ----------

def test_semaphore_caps_concurrency_at_limit():
    # 驱动 collect_competitor 内部使用的 _gather_bounded：6 个慢任务在 limit=2 下峰值不得超过 2，
    # 且确实发生过并发（证明信号量包住的是真实并行，而非串行假装）。
    async def _probe():
        peak = 0
        active = 0
        lock = asyncio.Lock()

        async def one():
            nonlocal peak, active
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.03)
            async with lock:
                active -= 1

        await _gather_bounded([one() for _ in range(6)], limit=2)
        return peak

    peak = asyncio.run(_probe())
    assert peak <= 2
    assert peak >= 2
