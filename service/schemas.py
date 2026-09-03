# -*- coding: utf-8 -*-
"""服务层数据契约（Phase 7）：一次调度 = 一个 Task；一个 Task = 本期 × 多竞品的若干 Run。

- RunMeta：单次 run_cycle（一竞品×一期）的元数据 + trace 计数链 + 门卫判定摘要；
- TaskMeta：一次被调度/手动的"本期巡检"（period × competitors[]），聚合多个 Run；
- AlertEvent：告警事件（高威胁 / 人工收件箱 / run 失败），经 Notifier 落台账。

全部 JSON-safe（pydantic v2 `model_dump(mode="json")` 直接可落盘，文件即状态，
与 Phase 4 `PeriodRunState`、Phase 1 `MemoryStore` 同一哲学）。
文档：docs/service.md。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RunStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


class TaskStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"  # 全部 run 成功
    partial = "partial"      # 部分 run 失败（run 级失败隔离，健康竞品照常出结果）
    failed = "failed"        # 全部 run 失败 / 无可用竞品


class RunMeta(BaseModel):
    """单次 run_cycle 的查询视图：跑哪个竞品哪一期、计数链、门卫判定、是否转人工。"""

    run_id: str
    competitor_id: str
    period: str
    status: RunStatus = RunStatus.pending
    verdict: str | None = None                 # PASS / human（done 时由 gate 回填）
    trace: dict = Field(default_factory=dict)  # {collected:N, merged:M, diff:K}（README2 §5.10 计数链）
    diff_summary: dict = Field(default_factory=dict)
    threat_radar: dict = Field(default_factory=dict)  # draft.threat_radar（high/medium/low）
    gate_problems: int = 0                     # 末次门卫判定的问题条数（= 拦 X 的口径）
    gate_trace_count: int = 0                  # 本次 run 内门卫判定次数（PASS 也算一次）
    inbox_count: int = 0                       # 转人工收件箱条数
    human_inbox: list[dict] = Field(default_factory=list)  # P6 落箱条目（Phase 8 面板读）
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def as_dict(self) -> dict:
        return self.model_dump(mode="json")


class TaskMeta(BaseModel):
    """一次"本期巡检"：同一 period 下对每个竞品各跑一个 Run（逐个隔离、失败不拖垮）。"""

    task_id: str
    period: str
    competitors_requested: list[str]
    status: TaskStatus = TaskStatus.running
    runs: list[RunMeta] = Field(default_factory=list)
    created_at: str
    finished_at: str | None = None
    summary: dict = Field(default_factory=dict)  # 聚合计数（done/failed/human/inbox），GET /tasks 直接展示
    error: str | None = None

    def as_dict(self) -> dict:
        return self.model_dump(mode="json")


class AlertEvent(BaseModel):
    """告警事件：MockNotifier 落台账的对象；Webhook 占位（README 写可对接企微/飞书/邮件）。"""

    alert_id: str
    event_type: str            # high_threat | human_inbox | run_failed
    competitor_id: str | None = None
    period: str | None = None
    run_id: str | None = None
    summary: str
    payload: dict = Field(default_factory=dict)
    ts: str
    delivered_by: str = "mock"  # mock | webhook_placeholder

    def as_dict(self) -> dict:
        return self.model_dump(mode="json")
