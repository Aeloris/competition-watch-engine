# -*- coding: utf-8 -*-
"""Reviewer 问题清单的类型与代码（Phase 6）。

代码分两类，**路由策略在 orchestration 节点、不在本模块**：
- 结构性缺口（STRUCTURAL）：格式病（无出处/无标题/缺维度/未分级/空理由/缺 fp），
  Writer 重跑（或上游补全）能好 → REWRITE 阶梯（额度内打回，用尽 → human）。
- 信任类问题（TRUST）：编造来源 / 假事件 / 理由对不上 / 条目互斥 / 旧闻重发 ——
  **不能靠"改写"修好**（让 Writer 改写一个没出处的结论 = 逼它编造），命中即直转人工收件箱。

ReviewProblem 是打回 trace 的结构化载体：code（机器可判）+ item_index（1 基条目序号）+
fp（身份，回溯锚）+ detail（人话，写进 gate.reasons / gate_trace / human_inbox）。
"""
from __future__ import annotations

from dataclasses import dataclass

# ---- 结构性缺口（格式病 → REWRITE 阶梯）----
NO_EVIDENCE = "NO_EVIDENCE"              # evidence_urls 空 → 无出处
EMPTY_TITLE = "EMPTY_TITLE"              # 标题为空
MISSING_DIMENSION = "MISSING_DIMENSION"  # 缺观察维度
MISSING_SEVERITY = "MISSING_SEVERITY"    # 未分级（缺 severity）
EMPTY_REASONS = "EMPTY_REASONS"          # 无威胁判定理由
MISSING_FP = "MISSING_FP"                # 缺身份 fp → 无法回溯到本期 diff 事件

# ---- 信任类问题（直转人工收件箱，绝不自动放行）----
UNSOURCED_URL = "UNSOURCED_URL"          # 出处 URL 不在本期采集卡内 → 疑似编造来源
UNGROUNDED_FP = "UNGROUNDED_FP"          # fp 不在本期 diff 变更集 → 非本期真变更
UNGROUNDED_CLAIM = "UNGROUNDED_CLAIM"    # 理由声称命中的信号词不在条目正文 → 张冠李戴
CONTRADICTION = "CONTRADICTION"          # 同竞品本期两条：反义极性 + 共享主体锚 → 互斥
DUPLICATE = "DUPLICATE"                  # fp 属上期 unchanged → 把旧闻又发出来（与上期重复）

STRUCTURAL_CODES = frozenset({
    NO_EVIDENCE, EMPTY_TITLE, MISSING_DIMENSION,
    MISSING_SEVERITY, EMPTY_REASONS, MISSING_FP,
})
TRUST_CODES = frozenset({
    UNSOURCED_URL, UNGROUNDED_FP, UNGROUNDED_CLAIM, CONTRADICTION, DUPLICATE,
})


@dataclass(frozen=True)
class ReviewProblem:
    """一条门卫问题：code + 1 基条目序号 + 身份 fp + 人话 detail。"""

    code: str
    item_index: int           # 周报条目 1 基序号（可空语义：0 表示不落单条，如跨条目矛盾）
    fp: str | None            # 条目的身份指纹（回溯锚；缺则 None）
    detail: str               # 人话描述（同 gate.reasons 文案，进 trace / 收件箱）

    def as_dict(self) -> dict:
        return {"code": self.code, "item_index": self.item_index,
                "fp": self.fp, "detail": self.detail}
