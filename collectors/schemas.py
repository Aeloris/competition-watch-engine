# -*- coding: utf-8 -*-
"""采集层结果模型：每源 WorkerOutcome + 每竞品 CollectSummary（含 failures 旁路）。

计数链（README2 §5.10）第一节在此成形：fetched（读到几条原文）→ cards（归一化成功几条）→ failed（几源失败）。
failures 是"诚实标注"，不是异常——单源挂了照常出其它源，由上层决定如何标注报告。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from memory.schemas import FactCard


class SourceStats(BaseModel):
    """单个信源在本轮采集的计数。"""

    kind: str
    fetched: int = 0   # 该源读到几条原文（RawItem）
    cards: int = 0     # 归一化成几条 FactCard（成功数）
    failed: int = 0    # 该源是否失败（0/1；本轮粒度到源）


class WorkerOutcome(BaseModel):
    """一个 Worker（竞品×信源）的运行结果。失败也返回对象、绝不抛给全局。"""

    kind: str
    competitor_id: str
    ok: bool
    cards: list[FactCard] = Field(default_factory=list)
    error: str | None = None
    fetched: int = 0


class CollectSummary(BaseModel):
    """一个竞品全部信源采集的汇总（跨源并集 + 逐源计数 + 失败清单）。"""

    competitor_id: str
    by_source: dict[str, SourceStats] = Field(default_factory=dict)
    cards: list[FactCard] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)  # 人类可读："kind/竞品: 原因"
