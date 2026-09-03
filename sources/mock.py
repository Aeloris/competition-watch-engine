# -*- coding: utf-8 -*-
"""MockSource：读 fixtures/sources/{competitor_id}.json 的假数据，模拟一个信源。

fixtures/sources 每个竞品一份 JSON：{"id","name","aliases","items":[{kind,url,published_at,title,summary}]}。
items 覆盖 website/news/rss 三类且时间戳跨两周 —— 作为 Phase 1–3 去重/diff/评测的离线回放底座。
真实 HTTP/RSS/搜索 adapter 一律不实现（Phase 2 起另开）。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from sources.base import RawItem, SourceAdapter


class MockSource(SourceAdapter):
    name = "mock"

    def __init__(self, mock_dir: str | Path) -> None:
        self._mock_dir = Path(mock_dir)

    def _load(self, competitor_id: str) -> dict:
        p = self._mock_dir / f"{competitor_id}.json"
        if not p.exists():
            return {"items": []}
        return json.loads(p.read_text(encoding="utf-8"))

    async def fetch(
        self, competitor_id: str, kind: str, *, since: datetime | None = None
    ) -> list[RawItem]:
        data = self._load(competitor_id)
        items = [RawItem(**it) for it in data.get("items", []) if it.get("kind") == kind]
        if since is not None:
            items = [it for it in items if it.published_at >= since]
        return sorted(items, key=lambda it: it.published_at)
