# -*- coding: utf-8 -*-
"""编排节点（Phase 4 落地版）：一条周期流水线的七个 LangGraph 节点函数。

节点契约 = LangGraph 约定：`(state: dict) -> dict`（返回的部分更新按 key 覆盖进共享 State）。
每个节点只读自己需要的 key、只写自己的 key —— 这就是"节点边界"的工程含义。

P4 的诚实分层（README2 §7.4 阶段 4 = 打通编排，5/6 = 分析师/门卫做实）：
- **plan / researchers / dedupe_diff 是真节点**：分别复用 orchestration.Planner、
  collectors（并发采 + 失败隔离 + fetched_at 确定性）与 dedupe + memory.diff（去重 + 跨期 diff + 写快照）；
- **analyst / writer / reviewer 是"结构真、逻辑薄"的节点**：
    analyst   → 投影"本周值得写"的变更清单（severity=None 占位，P5 rubric 才分级）；
    writer    → 排成周报初稿结构（P5 才做威胁雷达/对比表）；
    reviewer  → 结构性门卫（每条必带出处 + 必填齐全；语义 grounding / 矛盾检测是 P6）。
  这样图（Phase 4 主菜）跑得通、测得死，又没越界假装 P5/P6 已做。

数据流里的 pydantic 只在节点内部出现；写进共享 State 的永远是 JSON-safe 的 dict/list，
保证"共享 State 可序列化"字面成立（最终可直接落盘 / checkpoint）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from collectors import build_collectors, collect_competitor
from dedupe import merge_to_events, read_competitor_aliases
from memory import (
    ChangeSet,
    Event,
    FactCard,
    MemoryStore,
    Snapshot,
    build_snapshot,
    diff_event_sets,
)
from memory.store import get_memory_store
from orchestration.planner import (
    Planner,
    period_end_exclusive,
    period_start,
)


@dataclass
class NodeContext:
    """节点闭包依赖：settings + 每源 Worker + 记忆存储 + 竞品别名 + 打回限次。"""

    settings: object
    workers: dict
    store: MemoryStore
    aliases: dict[str, list[str]]
    review_max_rewrites: int

    @classmethod
    def build(cls, settings, *, store: MemoryStore | None = None) -> "NodeContext":
        store = store or get_memory_store(settings)
        workers = build_collectors(settings)
        mock_dir = settings.repo_root / settings.sources.mock_dir
        aliases = read_competitor_aliases(mock_dir)
        return cls(
            settings=settings,
            workers=workers,
            store=store,
            aliases=aliases,
            review_max_rewrites=settings.pipeline.review_max_rewrites,
        )


# ---------------------------------------------------------------- planner
def planner_node(state: dict, ctx: NodeContext) -> dict:
    """生成采集计划：since=周期起日（增量锚）+ run_id + fetched_at + 启用的信源。"""
    fet = datetime.fromisoformat(state["fetched_at"]) if state.get("fetched_at") else None
    plan = Planner(ctx.settings).plan(
        state["competitor_id"],
        state["period"],
        fetched_at=fet,
        run_id=state.get("run_id"),
    )
    return {"plan": plan}


# ---------------------------------------------------------------- researchers
def researchers_node(state: dict, ctx: NodeContext) -> dict:
    """并发采该竞品全部启用的信源（collectors 已做信号量限流 + 单源失败隔离，节点不 raise）。"""
    p = state["plan"]
    since_dt = datetime.fromisoformat(p["since"])
    fetched_at_dt = datetime.fromisoformat(p["fetched_at"])
    summary = asyncio.run(  # 节点同步；collect_competitor 内部 asyncio 并发（每源 Worker）
        collect_competitor(
            ctx.settings,
            ctx.workers,
            p["competitor_id"],
            since=since_dt,
            fetched_at=fetched_at_dt,
        )
    )
    return {
        "collect_summary": {
            "by_source": {kind: s.model_dump() for kind, s in summary.by_source.items()},
            "cards": len(summary.cards),
            "failures": list(summary.failures),
        },
        "cards": [c.model_dump(mode="json") for c in summary.cards],
    }


# ---------------------------------------------------------------- dedupe + diff
def dedupe_diff_node(state: dict, ctx: NodeContext) -> dict:
    """去重合并（cards→events）→ 与上期快照 diff → 变更事件，并把本期快照写档（幂等覆盖）。"""
    comp = state["competitor_id"]
    period = state["period"]
    cards = [FactCard.model_validate(c) for c in state.get("cards", [])]
    # 增量抓取可能带回未来内容：严格只留"落在本周期"的卡（起日 ≤ 发布日 < 下周一）
    start, end = period_start(period), period_end_exclusive(period)
    target = [c for c in cards if start <= c.published_at.date() < end]
    target.sort(key=lambda c: (c.published_at.isoformat(), c.source_url))

    events, dsum = merge_to_events(target, period=period, aliases=ctx.aliases)

    prev = ctx.store.latest_snapshot(comp)
    prev_snap = prev if prev is not None else Snapshot(period="", competitor_id=comp)
    cs: ChangeSet = diff_event_sets(prev_snap, events)   # 不传 removed_fps = 绝不自动推断消失

    # 记忆写档：本期固化（fps + digests），下期 diff 的比对底；同 (竞品,期) 幂等覆盖
    ctx.store.save_snapshot(build_snapshot(events, comp, period))

    return {
        "cards": [c.model_dump(mode="json") for c in target],  # state.cards 收敛为"目标期卡"
        "events": [e.model_dump(mode="json") for e in events],
        "changes": [e.model_dump(mode="json") for e in (cs.adds + cs.changes)],
        "diff_summary": cs.summary().model_dump(),
        "removed_fps": list(cs.removed_fps),
        "unchanged_fps": list(cs.unchanged_fps),
        "trace": {
            "collected": len(target),   # N：本期喂给去重的卡
            "merged": dsum.events_out,  # M：去重剩多少事件
            "diff": cs.summary().total, # K：diff 出多少变更（新增+变更）
        },
    }


# ---------------------------------------------------------------- analyst（薄）
def analyst_node(state: dict, ctx: NodeContext) -> dict:
    """投影"本周值得写"的变更清单：drop unchanged，severity 置 None（P5 rubric 填）。

    不做威胁分级/归因 —— P4 的 analyst 只负责"选哪些进周报 + 打占位"，把 P5 的地盘留干净。
    """
    write_items: list[dict] = []
    for e in state.get("changes", []):
        write_items.append(
            {
                "fp": e.get("fp"),
                "kind": e.get("kind"),
                "dimension": e.get("dimension"),
                "severity": None,  # P5 Analyst rubric 填；P4 诚实占位
                "title": e.get("title"),
                "summary": e.get("summary"),
                "evidence_urls": e.get("evidence_urls"),
            }
        )
    return {"write_items": write_items}


# ---------------------------------------------------------------- writer（薄）
def writer_node(state: dict, ctx: NodeContext) -> dict:
    """把待写清单排成周报初稿结构：headline + sections + items（P5 才做威胁雷达/对比表）。"""
    diff = state.get("diff_summary", {})
    draft = {
        "run_id": state.get("run_id"),
        "competitor_id": state["competitor_id"],
        "period": state["period"],
        "generated_at": state.get("fetched_at"),
        "headline_count": len(state.get("write_items", [])),  # 本周变更条数 = diff.total(K)
        "sections": {
            "adds": diff.get("adds", 0),
            "changes": diff.get("changes", 0),
            "removes": diff.get("removes", 0),
        },
        "items": state.get("write_items", []),  # 每条带 evidence_urls → 可溯源
    }
    return {"draft": draft}


# ---------------------------------------------------------------- reviewer（结构性门卫，薄）
def reviewer_node(state: dict, ctx: NodeContext) -> dict:
    """结构性门卫：周报每条必须有出处、必填齐全。

    有缺口 → verdict=REWRITE（还有改写额度）或 human（额度用尽 → 低置信转人工）；
    无缺口 → PASS。语义 grounding（原文是否真支撑结论）与矛盾检测属 P6 —— 这里只做结构完整性。
    """
    draft = state.get("draft", {})
    problems: list[str] = []
    for i, item in enumerate(draft.get("items", []), start=1):
        label = f"#{i} {str(item.get('title'))[:24] or '(无标题)'}"
        if not item.get("evidence_urls"):
            problems.append(f"{label}: 无出处(evidence_urls 空)——先审后发要求每条可溯源")
        if not str(item.get("title") or "").strip():
            problems.append(f"#{i}: 标题为空")
        if not item.get("dimension"):
            problems.append(f"#{i} {label}: 缺观察维度")

    attempts = int(state.get("rewrites", 0))
    if not problems:
        verdict, reasons, rewrites = "PASS", [], attempts
    elif attempts < ctx.review_max_rewrites:
        verdict, rewrites = "REWRITE", attempts + 1
        reasons = problems
    else:
        verdict, rewrites = "human", attempts  # 额度用尽 → 低置信转人工收件箱（不自动放行）
        reasons = problems

    return {"gate": {"verdict": verdict, "reasons": reasons, "attempts": rewrites}, "rewrites": rewrites}
