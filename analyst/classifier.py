# -*- coding: utf-8 -*-
"""Analyst（Phase 5 落地）：对 diff 出的变更事件做**威胁分级**，输出带理由、可解释。

职责边界（诚实）：
  - **只分级、不改维度**：Event.dimension 已由 Phase 3 用确定性规则冻结并混进 fp 定锚——
    Analyst 此刻改维度 = 同一件事被 diff 成"消失+新增"假情报。所以这里只读 dimension，
    用 rubric（analyst/rubric.py）给 high/medium/low + 理由链。
  - **不给 Event 重新归类**：Phase 3 classify 已按"规则先命中"做了维度兜底，P5 不再动它。
  - **LLM 补语义是未来一层**（真 provider 联调后在 rubric 之后兜异词同威胁），本期不假装做。

用法：
    from analyst import Analyst
    assessment = Analyst().assess(event)   # event: memory.schemas.Event
    # -> ThreatAssessment{level, rule_id, reasons[]}
"""
from __future__ import annotations

from analyst.rubric import ThreatAssessment, ThreatRubric
from memory.schemas import Event


class Analyst:
    """威胁分级器（Phase 5 实现：rubric 驱动，规则先命中）。"""

    def __init__(self, rubric: ThreatRubric | None = None) -> None:
        self._rubric = rubric if rubric is not None else ThreatRubric()

    @property
    def rubric(self) -> ThreatRubric:
        return self._rubric

    def assess(self, event: Event) -> ThreatAssessment:
        """对单条变更事件分级（纯函数、确定性：同事件永远同结果）。"""
        return self._rubric.apply(event)

    def grade(self, events: list[Event]) -> list[dict]:
        """批量分级 → JSON-safe 的待写条目（供 writer 直接消费）。

        每条 = {fp, kind, dimension, severity, reasons, title, summary, evidence_urls, first_seen}，
        全部是 str/list/dict —— 可直接进共享 State / 周报，不夹 pydantic 对象。
        """
        items: list[dict] = []
        for ev in events:
            a = self.assess(ev)
            items.append(
                {
                    "fp": ev.fp,
                    "kind": ev.kind.value,
                    "dimension": ev.dimension.value,
                    "severity": a.level.value,  # "high"|"medium"|"low"
                    "reasons": a.reasons,
                    "title": ev.title,
                    "summary": ev.summary,
                    "evidence_urls": ev.evidence_urls,
                    "first_seen": ev.first_seen.isoformat() if ev.first_seen else None,
                }
            )
        return items
