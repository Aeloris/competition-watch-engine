# -*- coding: utf-8 -*-
"""对抗注入集（Phase 8 Eval）：人为制造"长得合规但内容造假"的周报条目，测门卫拦不拦。

评测口径（对齐 README2 §5.11 编造拦截率）：把"编造入口"抽象成 **writer 层注入**——writer 本该把
已验证 Event 原样搬进周报，这里让它额外"幻觉"出 1–2 条假 claim（复用 Phase 6 的注入手法：
覆盖 orchestration.graph.NODE_IMPLS["writer"]，跑真实链路其余节点，看 reviewer 是否 human 拦截）。

诚实设计：
- 毒化 writer 以 **真实 write_items 为基底**（真实周报的其余条目照常在 draft 里），只在其中掺假——
  模拟"大部分正确、偶尔幻觉"的 writer，而不是空手造假；
- 每条假 claim 尽量 **只命中一个信任漏洞**（grounding/矛盾/重复各自独立），才能精确归因 code；
- 假 claim 借真实事件的 fp/url 作锚（真 fp 拼假 URL、真 URL 配假 fp…），保证是"信任层面"造假而非
  结构不整被当成格式病拦下；
- DUPLICATE（旧闻重发）需"上期 unchanged"上下文：真实 W35 与 W34 话题刻意不重叠 → unchanged 为空，
  图级自然触发不了 → 该例以**门卫级**复现（直接走 reviewer 判定路径，见 run_duplicate_gate）。
"""
from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from types import SimpleNamespace

from memory.schemas import EventKind, ThreatLevel
from orchestration import graph as graph_mod
from orchestration import run_cycle
from orchestration.nodes import reviewer_node
from reviewer import (
    CONTRADICTION,
    DUPLICATE,
    UNSOURCED_URL,
    UNGROUNDED_CLAIM,
    UNGROUNDED_FP,
)
from writer import Writer

# 对抗统一跑 crm_alpha（真实 W35 有 high/medium/low 三档、3 条变更，场景上下文最全）
ADV_COMP = "crm_alpha"
ADV_PERIOD = "2026-W35"
SEED_PERIOD = "2026-W34"

_FAKE_URL = "https://fake.example/claim"


def _item(title: str, *, fp: str, urls, severity: str = "medium", dimension: str = "feature",
          reasons=None, summary: str = "", kind: str = "add") -> dict:
    """结构性干净的周报条目（有出处/标题/维度/已分级/带理由/带 fp/带 kind）——只差"信任"层面可被下毒。"""
    if reasons is None:
        reasons = ["规则[feature_update] 常规迭代：功能级变化（注入样例）"]
    return {"fp": fp, "kind": kind, "dimension": dimension, "severity": severity,
            "reasons": list(reasons), "title": title, "summary": summary,
            "evidence_urls": list(urls)}


def _anchor(state: dict) -> tuple[str, list[str], str]:
    """取一个真实变更条目做"真锚"（fp/出处/标题），供假 claim 借用，保证只烧信任层。"""
    real = [it for it in (state.get("write_items") or []) if it.get("severity") == ThreatLevel.high.value]
    real = real or (state.get("write_items") or [])
    it = real[0]
    return it.get("fp"), list(it.get("evidence_urls") or []), it.get("title", "")


# ---------------- 各类假 claim（返回 [条目 dict]）----------------

def poison_unsourced(state: dict) -> list[dict]:
    """真 fp 拼编造 URL：事件是真的（fp 在本期 diff），但出处是编的 → UNSOURCED_URL。"""
    fp, _, _ = _anchor(state)
    return [_item("Alpha CRM v12.0 跟进助手渠道上线（更正）", fp=fp, urls=[_FAKE_URL],
                  severity="high", dimension="feature",
                  reasons=["规则[release_ga] 正式发布/GA 上线：竞品产品级变化已向用户交付"])]


def poison_ungrounded_fp(state: dict) -> list[dict]:
    """凭空 fp + 真出处：URL 真实采集到，但宣称的事件本期并不存在 → UNGROUNDED_FP。"""
    _, urls, _ = _anchor(state)
    return [_item("所谓 Alpha CRM 企业版大版本改版", fp="fp-fabricated-0001", urls=urls,
                  severity="medium", dimension="feature",
                  reasons=["规则[feature_update] 常规迭代：功能级变化（注入样例）"])]


def poison_ungrounded_claim(state: dict) -> list[dict]:
    """理由张冠李戴：真事件+真出处，但给一条"发布"动态挂"降价"的理由 → UNGROUNDED_CLAIM。"""
    fp, urls, _ = _anchor(state)
    return [_item("Alpha CRM v12.0 正式发布（存档）", fp=fp, urls=urls, severity="high",
                  dimension="price",
                  reasons=["规则[price_cut] 降价/让利：价格条款直接冲击我方定价与留客",
                           "命中信号词：降价、折扣", "维度=price；1 条独立来源"])]


def poison_contradiction(state: dict) -> list[dict]:
    """同竞品同版本反义：真实 GA 条目（正式发布）旁再发"停止支持" → CONTRADICTION。"""
    fp, urls, _ = _anchor(state)
    return [_item("Alpha CRM v12.0 停止支持（下架通知）", fp=fp, urls=urls, severity="low",
                  dimension="feature",
                  reasons=["规则[compliance_other] 生命周期变更：需人工复核"])]


def poison_mixed(state: dict) -> list[dict]:
    """一次幻觉两条：编造 URL + 同版本反义 → 期望同时命中 UNSOURCED_URL 与 CONTRADICTION。"""
    fp, urls, _ = _anchor(state)
    return [
        _item("Alpha CRM v12.0 跟进助手渠道上线（更正）", fp=fp, urls=[_FAKE_URL],
              severity="high", dimension="feature",
              reasons=["规则[release_ga] 正式发布/GA 上线：竞品产品级变化已向用户交付"]),
        _item("Alpha CRM v12.0 停止支持（下架通知）", fp=fp, urls=urls, severity="low",
              dimension="feature", reasons=["规则[compliance_other] 生命周期变更：需人工复核"]),
    ]


# ---------------- writer 毒化（真实基底 + 掺假）----------------

def _make_poison_writer(poison_fn):
    """生成毒化 writer：跑真实 Writer().build（真实 write_items）+ 掺假条目，kind/sections 自洽。"""

    def poisoned_writer(state: dict, ctx=None) -> dict:
        real = list(state.get("write_items") or [])
        items = real + list(poison_fn(state))
        kinds = Counter(i.get("kind") for i in items)
        diff = state.get("diff_summary", {})
        report = Writer().build(
            run_id=state.get("run_id"),
            competitor_id=state["competitor_id"],
            period=state["period"],
            generated_at=state.get("fetched_at"),
            sections={
                "adds": kinds.get(EventKind.add.value, 0),
                "changes": kinds.get(EventKind.change.value, 0),
                "removes": diff.get("removes", 0),
            },
            items=items,
        )
        return {"draft": report.model_dump(mode="json")}

    return poisoned_writer


@contextmanager
def override_writer(writer_fn):
    """覆盖 NODE_IMPLS["writer"] 跑一次 run_cycle，finally 恢复（Phase 6/7 同款注入手法）。"""
    prev = graph_mod.NODE_IMPLS.get("writer")
    graph_mod.NODE_IMPLS["writer"] = writer_fn
    try:
        yield
    finally:
        if prev is not None:
            graph_mod.NODE_IMPLS["writer"] = prev
        else:
            graph_mod.NODE_IMPLS.pop("writer", None)


# ---------------- 场景执行 ----------------

def run_graph_case(iso_settings, store, poison_fn, *, run_id: str) -> dict:
    """图级对抗：store 已含 W34 真实快照 → W35 用毒化 writer 跑全链路，返回最终 State。"""
    with override_writer(_make_poison_writer(poison_fn)):
        return run_cycle(iso_settings, ADV_COMP, ADV_PERIOD, store=store, run_id=run_id)


def run_duplicate_gate(old_event: dict) -> dict:
    """门卫级旧闻重发：老事件 fp 属"上期 unchanged"却被 writer 当本期发出 → DUPLICATE。

    old_event 来自真实 W34 回放（fixtures 里真实存在过的事件）；因真实 W35 与 W34 话题不重叠，
    unchanged_fps 为空、图级触发不了，这里以 reviewer 判定路径直接复现（见模块 docstring）。
    """
    fp = old_event["fp"]
    item = _item(old_event["title"] or "（旧闻重发）", fp=fp,
                 urls=old_event.get("evidence_urls") or ["https://a.example/x"],
                 severity="medium", dimension=old_event.get("dimension", "feature"),
                 summary=old_event.get("summary", ""))
    ctx = SimpleNamespace(review_max_rewrites=2)
    state = {"competitor_id": old_event["competitor_id"], "period": ADV_PERIOD,
             "rewrites": 0, "draft": {"items": [item]}, "unchanged_fps": [fp]}
    return reviewer_node(state, ctx)


# 场景目录：expected = 该场景必须被门卫拦下且收件箱出现的 code（信任漏洞码）
ADVERSARIAL_CASES = [
    {"name": "unsourced_url", "level": "graph", "expected": {UNSOURCED_URL},
     "poison": poison_unsourced},
    {"name": "ungrounded_fp", "level": "graph", "expected": {UNGROUNDED_FP},
     "poison": poison_ungrounded_fp},
    {"name": "ungrounded_claim", "level": "graph", "expected": {UNGROUNDED_CLAIM},
     "poison": poison_ungrounded_claim},
    {"name": "contradiction", "level": "graph", "expected": {CONTRADICTION},
     "poison": poison_contradiction},
    {"name": "duplicate", "level": "gate", "expected": {DUPLICATE}, "poison": None},
    {"name": "mixed_batch", "level": "graph", "expected": {UNSOURCED_URL, CONTRADICTION},
     "poison": poison_mixed},
]
