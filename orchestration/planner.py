# -*- coding: utf-8 -*-
"""Planner 占位类（Phase 4 实现）。

职责：把"盯哪些竞品 × 看哪些维度 × 哪些信源"翻译成采集任务清单，支持
增量（since=上次时间，只抓新内容）与全量两种模式（README2 §5.2）。
"""


class Planner:
    """采集计划器。Phase 4（LangGraph 首个节点）实现：竞品×维度×信源 → 任务清单。"""

    # 数据契约（Phase 1 定稿）：Competitor / SourceConfig 见 docs/schema.md。
    # 增量采集是 token 优化的一环，与 Memory/Diff 呼应。
    pass
