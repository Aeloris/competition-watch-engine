# -*- coding: utf-8 -*-
"""周报数据模型（Phase 5 Writer 的"schema 合规"落点）：pydantic 强制每条分级合规。

README2 §5.7 的"输出严格走 pydantic"落地：本模型在 Writer 组装时做**最后一道结构校验**——
每条 ReportItem 必须有非空 severity（high/medium/low）、reasons≥1、evidence_urls≥1；
threat_radar 计数、headline_count 由模型在校验期**重算并核对**，不一致即报错（fail-fast）。
Writer 只组装、绝不补充常识 → 校验保证发出去的就是"已验证事件 + 已解释分级"的周报。

诚实口径：本模型管**结构/口径合规**；"原文是否真支撑结论/是否矛盾"是 Phase 6 Reviewer
的语义 grounding，不在这里假装。
"""
from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from memory.schemas import Dimension, EventKind, ThreatLevel


class ReportItem(BaseModel):
    """周报里的一条"本期变更动态"：内容是已验证 Event 的原样搬运，分级是可解释的威胁判定。"""

    fp: str | None = None
    kind: EventKind = Field(..., description="add/change/remove（本期来源类型）")
    dimension: Dimension
    severity: ThreatLevel = Field(..., description="Phase 5 Analyst 必填，禁 None")
    reasons: list[str] = Field(..., min_length=1, description="威胁判定理由链，禁空")
    title: str = Field(..., min_length=1)
    summary: str = ""
    evidence_urls: list[str] = Field(..., min_length=1, description="≥1 个真实出处，先审后发的 grounding 依据")
    first_seen: str | None = None  # ISO 日期


class ThreatRadar(BaseModel):
    """威胁雷达 = 本期变更按 high/medium/low 的计数（由 items 派生，不手工填）。"""

    high: int = 0
    medium: int = 0
    low: int = 0


class WeeklyReport(BaseModel):
    """一份合规的周报初稿。Writer.build 产出；校验不过即 raise，绝不发出结构不整的周报。"""

    run_id: str | None = None
    competitor_id: str = Field(..., min_length=1)
    period: str = Field(..., min_length=1, description='"YYYY-Www"')
    generated_at: str | None = None  # ISO
    headline_count: int = 0          # 由 items 长度重算并核对
    sections: dict[str, int] = Field(default_factory=dict)  # {adds,changes,removes}（来自 diff_summary）
    items: list[ReportItem] = Field(default_factory=list)
    threat_radar: ThreatRadar = Field(default_factory=ThreatRadar)  # 由 items 重算
    sources: list[str] = Field(default_factory=list, description="本期全部条目的出处 URL 并集（可溯源清单）")

    @model_validator(mode="after")
    def _enforce_structural_consistency(self) -> "WeeklyReport":
        # headline = 条数（每条 = 一条本期变更）
        if self.headline_count != len(self.items):
            raise ValueError(f"headline_count({self.headline_count}) != items 条数({len(self.items)})——周报口径不一致")
        # kind 计数与 diff sections 核对（remove 不进入周报 items，见 orchestration）
        adds = sum(1 for it in self.items if it.kind is EventKind.add)
        changes = sum(1 for it in self.items if it.kind is EventKind.change)
        if self.sections.get("adds", 0) != adds or self.sections.get("changes", 0) != changes:
            raise ValueError(f"items 的 kind 分布(add={adds},change={changes})与 diff sections {self.sections} 不一致")
        # 雷达计数重算（不信任调用方手填）
        radar = ThreatRadar(
            high=sum(1 for it in self.items if it.severity is ThreatLevel.high),
            medium=sum(1 for it in self.items if it.severity is ThreatLevel.medium),
            low=sum(1 for it in self.items if it.severity is ThreatLevel.low),
        )
        self.threat_radar = radar
        # 出处并集：可溯源清单（排序保证确定性）
        seen: set[str] = set()
        for it in self.items:
            seen.update(it.evidence_urls)
        self.sources = sorted(seen)
        return self
