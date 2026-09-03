# -*- coding: utf-8 -*-
"""去重层（Phase 3 Dedupe）：FactCards → Events（同事件多源并一条，evidence_urls 聚合）。

对外入口：
- classify_dimension(title, summary)  → 规则维度判定（fp 稳定前置，Phase 5 rubric 接管前先占位）
- version_anchor(title)                → 发布事件的"版本锚"（v12.0→'120'）
- merge_to_events(cards, ...)          → 去重合并 → (Events, DedupeSummary)
- read_competitor_aliases(mock_dir)    → 读竞品别名（合并时剔除产品名干扰）
"""
from dedupe.classify import classify_dimension
from dedupe.merge import (
    DedupeSummary,
    merge_to_events,
    read_competitor_aliases,
    version_anchor,
)

__all__ = [
    "classify_dimension",
    "version_anchor",
    "merge_to_events",
    "DedupeSummary",
    "read_competitor_aliases",
]
