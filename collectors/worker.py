# -*- coding: utf-8 -*-
"""CollectorWorker：一个 Worker 包一个 SourceAdapter + kind，负责"竞品×该信源"的单次采集。

职责与设计（README2 §5.3）：
- **归一化**：adapter 返回 RawItem → 归一化成 FactCard（统一带 url + 原文时间），上层只见 FactCard；
- **失败隔离**：run() 自身 try/except，任何异常都转成 WorkerOutcome(ok=False, error=…)，绝不外抛 → 单源挂了不影响全局；
- **fetched_at 可注入**：不传则取当前时间，测试必须显式传固定值保证离线确定性；
- **重试退避接口**：max_retries 参数已就位（真 HTTP/RSS adapter 接入后启用；mock 永不失败故不触发）。
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from collectors.normalize import rawitem_to_factcard
from collectors.schemas import WorkerOutcome
from sources.base import SourceAdapter


class CollectorWorker:
    """单个信源的采集 Worker：把 adapter 的 RawItem 流归一化成 FactCard 并做单源失败隔离。"""

    def __init__(
        self,
        adapter: SourceAdapter,
        kind: str,
        *,
        max_retries: int = 1,
    ) -> None:
        self._adapter = adapter
        self.kind = kind
        self._max_retries = max(1, max_retries)

    async def run(
        self,
        competitor_id: str,
        *,
        since: datetime | None = None,
        fetched_at: datetime | None = None,
    ) -> WorkerOutcome:
        """采一次该源；成功 → ok=True+cards，失败 → ok=False+error（隔离，不外抛）。"""
        last_error: BaseException | None = None
        for attempt in range(self._max_retries):
            try:
                items = await self._adapter.fetch(competitor_id, self.kind, since=since)
            except Exception as exc:  # 单源失败：重试或记错，绝不中断其它源
                last_error = exc
                if attempt + 1 < self._max_retries:
                    await asyncio.sleep(0.05 * (attempt + 1))  # 简单线性退避（真网络接入后换成正式退避）
                continue
            ts = fetched_at if fetched_at is not None else datetime.now()
            cards = [rawitem_to_factcard(it, competitor_id, ts) for it in items]
            return WorkerOutcome(
                kind=self.kind,
                competitor_id=competitor_id,
                ok=True,
                cards=cards,
                fetched=len(items),
            )
        return WorkerOutcome(
            kind=self.kind,
            competitor_id=competitor_id,
            ok=False,
            error=f"{type(last_error).__name__}: {last_error}",
        )
