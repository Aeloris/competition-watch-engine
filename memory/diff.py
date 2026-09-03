# -*- coding: utf-8 -*-
"""跨周期 diff（README2 §4.4 / §5.5，项目灵魂）：上期快照 vs 本期事件 → 新增/变更/消失。

比对语义（逐 fp）：
- fp 本期有、上期无                    → **add 新增**
- fp 两期都有，内容摘要/证据变了        → **change 变更**（返回本期内容，kind=change）
- fp 两期都有，内容摘要/证据没变        → **unchanged 跳过**（零重复计算，省 token 的落点）
- 消失 = **只来自显式撤回**（removed_fps 参数），绝不自动推断：
  上期事件本期没被报道 ≠ 它从世界上消失。自动过期/沉寂策略属 Phase 4 记忆层。

诚实边界写在 docstring：本引擎只做确定性 fp/digest 比对，不含语义判断；措辞变体
（同事件换标题再报道）在本阶段按新增处理，跨期语义同一性属 Phase 4（语义记忆 + Qdrant）。
"""
from __future__ import annotations

import hashlib
from typing import Iterable

from pydantic import BaseModel, Field

from memory.schemas import Event, EventKind, Snapshot


def _collapse_ws(s: str) -> str:
    """摘要参与摘要比对前先压空白，避免排版差异造成假 change。"""
    return " ".join(s.split())


def event_content_digest(ev: Event) -> str:
    """事件"内容指纹"：证据 url 并集（排序）+ 压白后的摘要。fp 管身份、digest 管内容是否变了。"""
    urls = "\x1f".join(sorted(ev.evidence_urls))
    payload = f"{urls}\x1e{_collapse_ws(ev.summary)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def build_snapshot(
    events: list[Event],
    competitor_id: str,
    period: str,
) -> Snapshot:
    """把一期的 Event 集固化成快照：event_fps + event_digests（fp→内容摘要），供下期 diff。"""
    fps = sorted({e.fp for e in events if e.fp})
    digests = {e.fp: event_content_digest(e) for e in events if e.fp}
    return Snapshot(
        period=period,
        competitor_id=competitor_id,
        event_fps=fps,
        event_digests=digests,
    )


def mark_removed(prev: Snapshot, fps: Iterable[str]) -> list[str]:
    """显式撤回：返回确在上期快照里、本次要标记消失的 fp（升序）。

    fp 若根本不在上期快照里 → ValueError（fail fast：调用方想撤一个不存在的东西是 bug）。
    这就是"消失只来自显式撤回"的唯一入口——绝不自动推断。
    """
    known = set(prev.event_fps)
    wanted = [f for f in fps if f]
    missing = [f for f in wanted if f not in known]
    if missing:
        raise ValueError(f"mark_removed: fp 不在上期快照里，无法标记消失: {sorted(missing)}")
    return sorted(set(wanted))


class DiffSummary(BaseModel):
    """trace 计数：diff 出 K（§5.10 去重剩 M → diff 出 K 的第三节）。"""

    adds: int = 0
    changes: int = 0
    removes: int = 0
    unchanged: int = 0

    @property
    def total(self) -> int:
        """本期要下发给下游的变更数 = 新增 + 变更（消失不进周报正文）。"""
        return self.adds + self.changes


class ChangeSet(BaseModel):
    """diff 产出：三类变更 + 跳过名单。adds/changes 是带本期内容的 Event（kind 已标）。"""

    adds: list[Event] = Field(default_factory=list)
    changes: list[Event] = Field(default_factory=list)
    removed_fps: list[str] = Field(default_factory=list)
    unchanged_fps: list[str] = Field(default_factory=list)

    def summary(self) -> DiffSummary:
        return DiffSummary(
            adds=len(self.adds),
            changes=len(self.changes),
            removes=len(self.removed_fps),
            unchanged=len(self.unchanged_fps),
        )


def diff_event_sets(
    prev: Snapshot,
    curr_events: list[Event],
    *,
    removed_fps: Iterable[str] | None = None,
) -> ChangeSet:
    """上期快照 vs 本期 Event 集 → ChangeSet。curr 按 fp 升序处理 → 输出确定。

    - adds：fp 不在上期；changes：fp 两期都在但内容摘要变；unchanged：内容没变（零重复计算）；
    - removed_fps：显式撤回的 fp（默认空 = 不自动消失）。传了会先经 mark_removed 校验。
    """
    prev_fps = set(prev.event_fps)
    adds: list[Event] = []
    changes: list[Event] = []
    unchanged: list[str] = []
    for ev in sorted(curr_events, key=lambda e: e.fp or ""):
        fp = ev.fp
        if fp is None:
            continue  # 无 fp 的事件（理论不发生）不入 diff
        digest = event_content_digest(ev)
        if fp not in prev_fps:
            adds.append(ev.model_copy(update={"kind": EventKind.add}))
        elif prev.event_digests.get(fp) == digest:
            unchanged.append(fp)
        else:
            changes.append(ev.model_copy(update={"kind": EventKind.change}))
    removed = mark_removed(prev, removed_fps or []) if removed_fps is not None else []
    return ChangeSet(adds=adds, changes=changes, removed_fps=removed, unchanged_fps=unchanged)
