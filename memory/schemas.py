# -*- coding: utf-8 -*-
"""核心数据对象（对齐 README2 §3 的 ER 图）：证据 FactCard → 事实 Event → 记忆 Snapshot。

分层语义与各字段的填充归属：
- FactCard（证据）：Researchers 归一化产出的"一条可溯源原文"，由 collectors 装配（Phase 2 起真正产出，
  模型现在定全）。铁律：source_url 非空——情报无出处不成立。
- Event（事实）：同一真实事件多源报道经 Dedupe 合并成一条，evidence_urls 聚合佐证它的全部 FactCard
  URL（Phase 3 产出）；severity 由 Analyst 填（Phase 5）；fp 由指纹/diff 层填（Phase 3）。
- Snapshot（记忆）：某竞品某周期结束时的留档 = 该期 Event 指纹集合，diff 引擎（Phase 3）靠它对比下期。

三类对象全部 pydantic → 存储/门卫/评测才有稳定类型契约。
"""
from __future__ import annotations

import hashlib
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, model_validator

from memory.fingerprint import normalize_title


def sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


class Dimension(str, Enum):
    """观察维度（README2 §1.1 的看什么）。"""

    feature = "feature"          # 功能 / 产品
    price = "price"              # 价格 / 商务
    market = "market"            # 市场 / 渠道
    org = "org"                  # 组织 / 战略
    compliance = "compliance"    # 合规 / 舆情


class EventKind(str, Enum):
    """变更事件类型（diff 引擎 Phase 3 产出）。"""

    add = "add"        # 本期新增
    change = "change"  # 既有事件内容有变
    remove = "remove"  # 上期有、本期消失


class ThreatLevel(str, Enum):
    """威胁分级（Analyst Phase 5 填，现在只定枚举）。"""

    high = "high"
    medium = "medium"
    low = "low"


class FactCard(BaseModel):
    """证据：一条可溯源到 URL 的归一化原文。source_url 是溯源锚点。"""

    competitor_id: str
    source_url: str = Field(..., min_length=1)
    title: str = ""
    summary: str = ""
    published_at: datetime            # 原文发布时间
    fetched_at: datetime              # 抓取/入库时间
    digest: str | None = None         # 内容指纹 sha1(source_url|normalize_title)；语义去重前身
    id: str | None = None             # sha1(competitor_id|source_url|published_at) —— 稳定、可幂等

    @model_validator(mode="after")
    def _fill_derived(self) -> "FactCard":
        if self.digest is None:
            self.digest = sha1_hex(f"{self.source_url}|{normalize_title(self.title)}")
        if self.id is None:
            self.id = sha1_hex(f"{self.competitor_id}|{self.source_url}|{self.published_at.isoformat()}")
        return self


class Event(BaseModel):
    """事实：同一真实事件多源报道合并后的单条记录。Phase 3 Dedupe 产出。"""

    id: str
    competitor_id: str
    dimension: Dimension
    kind: EventKind
    title: str = Field(..., min_length=1)
    summary: str = ""
    evidence_urls: list[str] = Field(..., min_length=1)  # ≥1 个真实来源，先审后发的 grounding 依据
    severity: ThreatLevel | None = None       # Analyst Phase 5 填
    first_seen: date
    last_seen: date
    fp: str | None = None                     # 指纹层 Phase 3 填，跨期 diff 锚点
    created_at: datetime = Field(default_factory=datetime.now)


class Snapshot(BaseModel):
    """记忆：某竞品某周期结束的留档 = 该期 Event 指纹集合。diff 引擎（Phase 3）的比对对象。

    Phase 3 扩展：
    - `event_digests`（fp → 事件内容摘要）——只看 fp 集合只能判 add/remove/skip，判不了 change；
      同 fp 两期都在但 digest 变了 = 变更事件。Phase 1 的旧快照无此字段（默认 {}，向后兼容）。
    - `event_fps` 与 `event_digests` 的键应一一对应（`memory/diff.py::build_snapshot` 保证）。
    """

    period: str                          # "YYYY-Www"（ISO 周），如 "2026-W34"
    competitor_id: str
    event_fps: list[str] = Field(default_factory=list)
    event_digests: dict[str, str] = Field(default_factory=dict)  # fp → 内容摘要（diff change 判定）
    created_at: datetime = Field(default_factory=datetime.now)
