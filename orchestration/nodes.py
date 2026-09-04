# -*- coding: utf-8 -*-
"""编排节点（Phase 4 落地版）：一条周期流水线的七个 LangGraph 节点函数。

节点契约 = LangGraph 约定：`(state: dict) -> dict`（返回的部分更新按 key 覆盖进共享 State）。
每个节点只读自己需要的 key、只写自己的 key —— 这就是"节点边界"的工程含义。

Phase 演进：P4 打通编排（图跑得通、测得死）；P5 把两个薄节点做实、图一条边不改：
- **plan / researchers / dedupe_diff 是真节点**：分别复用 orchestration.Planner、
  collectors（并发采 + 失败隔离 + fetched_at 确定性）与 dedupe + memory.diff（去重 + 跨期 diff + 写快照）；
- **analyst（P5 做实）** → 用 rubric 给每个变更事件打 high/medium/low + 理由链
  （确定性、可解释；只读 dimension、不改 fp；LLM 补语义等真 provider 再接）；
- **writer（P5 做实）** → 把已分级条目组装成 WeeklyReport（pydantic schema 强制合规：
  每条必有 severity/reasons/出处；威胁雷达与 kind 计数自洽），只组装、绝不补充常识；
- **reviewer（P6 做实）** → 审稿门卫：grounding 闭环自证（来源必在本期采集卡、fp 必在本期
  diff 变更集、理由命中词必在正文）+ 显式反义极性矛盾 + 与上期重复；结构性缺口打回 Writer
  （限次），信任类问题直转人工收件箱并落 gate_trace / human_inbox（先审后发）。

数据流里的 pydantic 只在节点内部出现；写进共享 State 的永远是 JSON-safe 的 dict/list，
保证"共享 State 可序列化"字面成立（最终可直接落盘 / checkpoint）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

from analyst import Analyst
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
from reviewer import STRUCTURAL_CODES, TRUST_CODES, Reviewer
from writer import Writer


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

    # diff 锚钉"本期及之前"的最新快照（up_to=period）：正常周更 = 上期；补跑/重跑历史周期时不被
    # 更晚已跑的周期污染（否则回填 W34 会用 W35 当上期，把 W34 相对 W33 的新增漏报成 unchanged）。
    prev = ctx.store.latest_snapshot(comp, up_to=period)
    prev_snap = prev if prev is not None else Snapshot(period="", competitor_id=comp)
    cs: ChangeSet = diff_event_sets(prev_snap, events)   # 不传 removed_fps = 绝不自动推断消失

    # 采集断档守卫：全部信源失败且零卡 = 本轮"没看到世界"，不是"世界没变化"。此时**不覆盖**
    # 上期快照**——否则把 diff 锚冲成空，下期再把旧闻当"新增"上报（假情报入口）。空档标记交
    # runner 记 failed，让"采集坏了"显形，而不是用空周报冒充干净 PASS。
    failures = (state.get("collect_summary") or {}).get("failures") or []
    collection_gap = bool(failures) and not target
    if not collection_gap:
        # 记忆写档：本期固化（fps + digests），下期 diff 的比对底；同 (竞品,期) 幂等覆盖
        # （正常空期也照写空快照 = "本周确实无事发生"与断档区分开）
        ctx.store.save_snapshot(build_snapshot(events, comp, period))

    return {
        "cards": [c.model_dump(mode="json") for c in target],  # state.cards 收敛为"目标期卡"
        "events": [e.model_dump(mode="json") for e in events],
        "changes": [e.model_dump(mode="json") for e in (cs.adds + cs.changes)],
        "diff_summary": cs.summary().model_dump(),
        "removed_fps": list(cs.removed_fps),
        "unchanged_fps": list(cs.unchanged_fps),
        "collection_gap": collection_gap,
        "trace": {
            "collected": len(target),   # N：本期喂给去重的卡
            "merged": dsum.events_out,  # M：去重剩多少事件
            "diff": cs.summary().total, # K：diff 出多少变更（新增+变更）
        },
    }


# ---------------------------------------------------------------- analyst（P5 做实）
def analyst_node(state: dict, ctx: NodeContext) -> dict:
    """对"本周值得写"的变更事件做威胁分级（rubric：high/medium/low + 理由链）。

    只消费 diff 出的 changes（adds+changes，unchanged 已 drop）；只读 dimension、不改 fp。
    确定性：同事件永远同分级 → 同语料重跑，周报每条 severity 稳定（测试锁死）。
    """
    events = [Event.model_validate(e) for e in state.get("changes", [])]
    write_items = Analyst().grade(events)  # JSON-safe dicts：severity=high/medium/low + reasons[]
    return {"write_items": write_items}


# ---------------------------------------------------------------- writer（P5 做实）
def writer_node(state: dict, ctx: NodeContext) -> dict:
    """把已分级条目组装成合规周报（WeeklyReport schema 强制；只组装、不补充常识）。"""
    diff = state.get("diff_summary", {})
    report = Writer().build(
        run_id=state.get("run_id"),
        competitor_id=state["competitor_id"],
        period=state["period"],
        generated_at=state.get("fetched_at"),
        sections={
            "adds": diff.get("adds", 0),
            "changes": diff.get("changes", 0),
            "removes": diff.get("removes", 0),
        },
        items=state.get("write_items", []),
    )
    return {"draft": report.model_dump(mode="json")}


# ---------------------------------------------------------------- reviewer（P6 审稿门卫）
def reviewer_node(state: dict, ctx: NodeContext) -> dict:
    """审稿门卫（P6）：grounding 闭环自证 + 反义极性矛盾 + 与上期重复 + 结构合规。

    判定依据是**本 run 的证据**（state.cards / changes / unchanged_fps），不是 Writer 自述
    （门卫独立，reviewer 包不依赖 writer 包）。路由策略：
      - 结构性缺口（格式病）→ 额度内 REWRITE 打回 Writer，用尽 → human；
      - 信任类问题（编造 URL / 凭空 fp / 理由与文本不符 / 条目互斥 / 旧闻重发）
        → **直转 human，不走改写**（让 Writer 改写一个没出处的结论 = 逼它编造），并落人工收件箱。
    每次判定 append gate_trace（README2 §5.8 打回有 trace）；human 时把问题条目落 human_inbox。
    """
    draft = state.get("draft", {})
    problems = Reviewer().review(
        draft,
        cards=state.get("cards"),          # 本期采集卡 → 编造 URL 拦截的证据索引
        changes=state.get("changes"),      # 本期 diff 变更事件 → 凭空 fp 拦截的回溯锚
        unchanged_fps=state.get("unchanged_fps"),
    )
    reasons = [p.detail for p in problems]                       # 人话文案（保留原中文定位词）
    typed = [p.as_dict() for p in problems]                      # 结构化问题（机器可判）
    trust = [p for p in problems if p.code in TRUST_CODES]
    structural = [p for p in problems if p.code in STRUCTURAL_CODES]

    attempts = int(state.get("rewrites", 0))
    if not problems:
        verdict, attempts = "PASS", attempts
    elif trust:
        # 信任问题：改不了（改写一个没出处的结论 = 编造）→ 直转人工，不消耗改写额度
        verdict = "human"
    elif attempts < ctx.review_max_rewrites:
        verdict, attempts = "REWRITE", attempts + 1
    else:
        verdict = "human"  # 结构缺口额度用尽 → 低置信转人工收件箱（不自动放行）

    # 打回/判定审计迹：每次门卫判定都记一条（PASS 也记，README2 §5.8 "打回有 trace"）
    gate_trace = list(state.get("gate_trace") or []) + [{
        "attempt": attempts, "verdict": verdict,
        "problems": typed, "reasons": reasons,
    }]

    # 转人工 → 问题条目落人工收件箱（人工确认才放行）。
    # 信任问题直转 human 时，本跑即终止、结构缺口不再走 REWRITE——若只落信任类，结构有缺的条目
    # 会在 published_view 里"无门卫指向"被当自审通过自动发布。故 human 一律把本次全部问题落箱。
    human_inbox = list(state.get("human_inbox") or [])
    if verdict == "human":
        for p in problems:
            human_inbox.append({
                "competitor_id": state.get("competitor_id"),
                "period": state.get("period"),
                "item_index": p.item_index,
                "fp": p.fp,
                "code": p.code,
                "detail": p.detail,
                "attempts": attempts,
            })

    return {
        "gate": {"verdict": verdict, "reasons": reasons,
                 "attempts": attempts, "problems": typed},
        "rewrites": attempts,
        "gate_trace": gate_trace,
        "human_inbox": human_inbox,
    }
