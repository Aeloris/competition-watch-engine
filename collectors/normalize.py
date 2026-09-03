# -*- coding: utf-8 -*-
"""归一化唯一入口：RawItem（信源原文）→ FactCard（证据）。

fetched_at 必须由调用方注入（默认值在 Worker 侧取 now）—— 固定它才能保证离线确定性/回放。
id/digest 由 FactCard 的 after-validator 派生，这里只搬运字段。
"""
from __future__ import annotations

from datetime import datetime

from memory.schemas import FactCard
from sources.base import RawItem


def rawitem_to_factcard(item: RawItem, competitor_id: str, fetched_at: datetime) -> FactCard:
    return FactCard(
        competitor_id=competitor_id,
        source_url=item.url,
        title=item.title,
        summary=item.summary,
        published_at=item.published_at,
        fetched_at=fetched_at,
    )
