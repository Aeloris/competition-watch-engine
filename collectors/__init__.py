# -*- coding: utf-8 -*-
"""采集层（Phase 2 Researchers）：并发采 3 类信源 → 归一化 FactCard，单源失败隔离。

对外入口：
- build_collectors(settings)      → {kind: CollectorWorker}（每源一个 Worker）
- collect_competitor(...)         → 单竞品跨源并发汇总 CollectSummary
- collect_all(...)                → 全部竞品逐竞品汇总
"""
from collectors.factory import build_collectors
from collectors.orchestrator import collect_all, collect_competitor
from collectors.schemas import CollectSummary, SourceStats, WorkerOutcome
from collectors.worker import CollectorWorker

__all__ = [
    "CollectorWorker",
    "SourceStats",
    "WorkerOutcome",
    "CollectSummary",
    "build_collectors",
    "collect_competitor",
    "collect_all",
]
