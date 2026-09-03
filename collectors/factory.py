# -*- coding: utf-8 -*-
"""build_collectors：按 config.sources.enabled 把信源类型映射成 Worker。

mock 下三类源共用一个 MockSource（读同一 fixtures 按 kind 过滤），为每个 kind 单独包一层 Worker
是为了让"并发单元 = 每源一个 Worker、单源失败隔离"的语义在实例层面成立；
将来真实 HTTP/RSS/搜索 adapter 接入时，只需替换这里给 Worker 的 adapter（加"来源"不加业务代码）。
"""
from __future__ import annotations

from config.settings import Settings
from collectors.worker import CollectorWorker
from sources import MockSource


def build_collectors(settings: Settings) -> dict[str, CollectorWorker]:
    """返回 {kind: CollectorWorker}，kind 来自 config.sources.enabled。"""
    adapter = MockSource(settings.repo_root / settings.sources.mock_dir)
    workers: dict[str, CollectorWorker] = {}
    for kind in settings.sources.enabled:
        workers[kind] = CollectorWorker(adapter, kind, max_retries=settings.sources.max_retries)
    return workers
