# -*- coding: utf-8 -*-
"""Mock 语料装载（回放底座，Phase 1）：把 fixtures/sources 的假数据按 ISO 周切成本期 FactCard 存档。

确定性的价值：**给定同一份语料永远切出同样的期与条数** → Phase 3 diff 与 Phase 8 回放评测都建立在
这份"可回放"存档之上。只读 fixtures，不写库（写库由上层 MemoryStore 负责）。
真实信源在 Phase 2 的 Researchers 接入后，本模块让位给 collector 适配器（这里只服务 mock 回放）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path

from memory.schemas import FactCard
from sources import MockSource

# 对齐 config.sources.enabled 的默认信源类型（fixtures 语料覆盖这三类）
_ENABLED_KINDS = ("website", "news", "rss")


def _period_of(dt: datetime) -> str:
    """按原文发布时间切 ISO 周期：2026-08-17 → "2026-W34"。"""
    iso = dt.isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def list_competitors(mock_dir: str | Path) -> list[str]:
    """fixtures/sources 下有哪些竞品（按 id 升序）；目录缺失返回 []。"""
    d = Path(mock_dir)
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.json") if p.is_file())


def build_period_cards(
    mock_dir: str | Path, competitor_id: str, fetched_at: datetime
) -> dict[str, list[FactCard]]:
    """读某竞品全部信源（MockSource）→ 归一成 FactCard → 按 published_at 的 ISO 周分组。

    返回 {period: [FactCard]}，期内按 (published_at, source_url) 升序 → 两次装载结果逐字段一致（回放确定性）。
    id/digest 由 FactCard 的 after-validator 派生，调用方只需给原文字段 + fetched_at。
    """

    async def _gather() -> dict[str, list[FactCard]]:
        src = MockSource(mock_dir)
        grouped: dict[str, list[FactCard]] = {}
        for kind in _ENABLED_KINDS:
            for item in await src.fetch(competitor_id, kind):
                card = FactCard(
                    competitor_id=competitor_id,
                    source_url=item.url,
                    title=item.title,
                    summary=item.summary,
                    published_at=item.published_at,
                    fetched_at=fetched_at,
                )
                grouped.setdefault(_period_of(item.published_at), []).append(card)
        return {
            period: sorted(cards, key=lambda c: (c.published_at.isoformat(), c.source_url))
            for period, cards in grouped.items()
        }

    return asyncio.run(_gather())
