# -*- coding: utf-8 -*-
"""Phase 6 Reviewer 审稿门卫：grounding 拦截 + 矛盾检测 + 与上期重复 + 审计迹/收件箱。

验收口径（README2 §7.4 row 6）：注入假事件 → 门卫能拦下（不进 PASS 可发布态），打回有 trace。

本文件锁的机器（全部确定性、离线、无 key）：
- grounding = 闭环自证：evidence_url 必须在本期采集卡（state.cards）里、fp 必须在本期
  diff 变更集（state.changes）里、理由声称命中的信号词必须真实在条目正文 → 三种编造各拦截；
- 矛盾检测 = 显式反义极性对 + 共享版本号/高共享二元组（机械互斥，宁转人工不自动二选一）；
- 合规 = 条目 fp 若属上期 unchanged → 旧闻重发拦截；
- 路由：结构缺口 → REWRITE 阶梯（另见 test_orchestration_graph）；信任问题 → 直转 human、
  不消耗改写额度；审计迹 gate_trace 逐次追加、转人工落 human_inbox；
- 真实语料（fixtures W35，crm/bi）0 误拦：Review() 干净、图级 PASS、收件箱空。

诚实边界（docs/reviewer.md）：本 grounding 是"来源可回溯 + 结论可复核"的确定性自证，
**不**做"读原文语义是否明说结论"（那要 LLM 语义层，provider-gated，本期未接）。
"""
from types import SimpleNamespace

from config.settings import get_settings
from memory import JsonMemoryStore
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
from reviewer.gate import Reviewer

SETTINGS = get_settings()
COMP = "crm_alpha"
V12_FP = "5c60e045b8853e63"  # fixtures 真值：crm W35 v12.0 发布事件 fp（复用于"真 fp 拼编造 URL"注入）


def _ctx():
    return SimpleNamespace(review_max_rewrites=2)


def _store(tmp_path):
    return JsonMemoryStore(tmp_path / "mem")


def _item(title: str = "T", *, fp: str = "fp-1", urls=("https://a.example/x",),
          severity: str = "medium", reasons=None, summary: str = "",
          dimension: str = "feature", kind: str = "add") -> dict:
    """结构性干净的门卫条目（有出处/标题/维度/已分级/带理由/有 fp）——只差"信任"层面可被下毒。"""
    return {"fp": fp, "kind": kind, "dimension": dimension, "severity": severity,
            "reasons": list(reasons) if reasons is not None else ["规则[feature_update]：常规迭代"],
            "title": title, "summary": summary, "evidence_urls": list(urls)}


def _card(url: str, title: str = "卡") -> dict:
    return {"source_url": url, "title": title, "summary": "", "competitor_id": COMP}


def _review_node(draft_items, *, cards=None, changes=None, unchanged_fps=None, rewrites=0) -> dict:
    state = {"competitor_id": COMP, "period": "2026-W35", "rewrites": rewrites,
             "draft": {"items": draft_items}}
    if cards is not None:
        state["cards"] = cards
    if changes is not None:
        state["changes"] = changes
    if unchanged_fps is not None:
        state["unchanged_fps"] = unchanged_fps
    return reviewer_node(state, _ctx())


# ---------- 真实语料（fixtures W35）0 误拦 ----------

def test_reviewer_real_w35_clean_no_false_positive(tmp_path):
    """真语料全链：Review() 返回 []（来源/fp/理由/矛盾/重复全干净），图级 PASS、收件箱空。"""
    store = _store(tmp_path)
    run_cycle(SETTINGS, COMP, "2026-W34", store=store, run_id="seed")   # 先写 W34 快照
    for comp in ("crm_alpha", "bi_beta"):
        final = run_cycle(SETTINGS, comp, "2026-W35", store=store, run_id=f"p6-{comp}")
        problems = Reviewer().review(
            final["draft"], cards=final["cards"], changes=final["changes"],
            unchanged_fps=final.get("unchanged_fps"))
        assert problems == [], f"{comp} 真实语料不该误拦：{[p.detail for p in problems]}"
        assert final["gate"]["verdict"] == "PASS"
        assert final["gate"]["problems"] == [] and final["gate"]["reasons"] == []
        assert final["human_inbox"] == []          # 真语料不进人工收件箱
        assert len(final["gate_trace"]) == 1        # PASS 也留一条审计迹
        assert final["gate_trace"][0]["verdict"] == "PASS"


# ---------- grounding：编造 URL → 拦截 ----------

def test_grounding_unsourced_url_intercepts_human(tmp_path):
    """evidence_url 不在本期采集卡内（编造来源）→ UNSOURCED_URL → 直转 human、不消耗额度。"""
    item = _item(title="真实 fp 但编造出处", fp="fp-real",
                 urls=("https://fake.example/x",))
    out = _review_node([item], cards=[_card("https://a.example/x")],
                       changes=[{"fp": "fp-real"}])
    assert out["gate"]["verdict"] == "human" and out["rewrites"] == 0   # 信任问题不走 REWRITE
    codes = {p["code"] for p in out["gate"]["problems"]}
    assert UNSOURCED_URL in codes
    assert any("未采集" in r for r in out["gate"]["reasons"])
    # 打回有 trace + 落人工收件箱
    assert out["gate_trace"][-1]["verdict"] == "human"
    assert out["human_inbox"] and out["human_inbox"][0]["code"] == UNSOURCED_URL


def test_grounding_ungrounded_fp_intercepts_human(tmp_path):
    """fp 不在本期 diff 变更集（凭空 fp）→ UNGROUNDED_FP → 直转 human。"""
    item = _item(title="本期没这回事", fp="fp-fabricated",
                 urls=("https://a.example/x",))      # 出处是真的，但 fp 凭空
    out = _review_node([item], cards=[_card("https://a.example/x")],
                       changes=[{"fp": "fp-real"}])
    assert out["gate"]["verdict"] == "human"
    assert UNGROUNDED_FP in {p["code"] for p in out["gate"]["problems"]}
    assert out["human_inbox"][0]["code"] == UNGROUNDED_FP


def test_grounding_both_url_and_fp_fake_short_circuits_no_rewrites(tmp_path):
    """信任问题命中 → 不消耗改写额度：attempts/rewrites 停在 0，直接 human。"""
    item = _item(title="假事件", fp="fp-x", urls=("https://fake.example/x",))
    out = _review_node([item], cards=[_card("https://a.example/x")],
                       changes=[{"fp": "fp-real"}])
    assert out["gate"]["verdict"] == "human" and out["rewrites"] == 0
    assert all(t["verdict"] == "human" for t in out["gate_trace"])


# ---------- grounding：理由声称命中词必须真在正文 ----------

def test_grounding_claim_word_mismatch_intercepts(tmp_path):
    """理由声称命中「降价」但正文没有该词 → UNGROUNDED_CLAIM（张冠李戴，防给发布挂降价理由）。"""
    item = _item(title="Alpha CRM 发布看板增强", fp="fp-c",
                 reasons=["规则[price_cut] 降价/让利：价格条款直接冲击我方定价与留客",
                          "命中信号词：降价、折扣",
                          "维度=price；1 条独立来源"])
    problems = Reviewer().review({"items": [item]})
    codes = {p.code for p in problems}
    assert UNGROUNDED_CLAIM in codes
    claim = next(p for p in problems if p.code == UNGROUNDED_CLAIM)
    assert "降价、折扣" in claim.detail and claim.fp == "fp-c"


def test_grounding_real_hit_line_no_false_positive(tmp_path):
    """rubric 真写的"命中信号词"确实在正文 → 不误拦（对齐 W35 真值条目格式）。"""
    item = _item(title="Alpha CRM v12.0 正式发布：智能跟进助手 GA", fp="fp-real",
                 urls=("https://a.example/blog",),
                 reasons=["规则[release_ga] 正式发布/GA 上线：竞品产品级变化已向用户交付",
                          "命中信号词：正式发布", "维度=feature；1 条独立来源"])
    problems = Reviewer().review({"items": [item]})
    assert all(p.code != UNGROUNDED_CLAIM for p in problems)


# ---------- 矛盾检测：显式反义极性 + 共享主体锚 ----------

def test_contradiction_opposite_same_version_intercepts_human(tmp_path):
    """同竞品本期两条：同版本 v12.1 一"上调"一"降价" → CONTRADICTION → 转人工（不自动二选一）。"""
    a = _item("v12.1 移动端套餐价格上调", fp="fp-a", urls=("https://a.example/p1",))
    b = _item("v12.1 全版本订阅降价促销", fp="fp-b", urls=("https://a.example/p2",))
    out = _review_node([a, b])
    assert out["gate"]["verdict"] == "human"
    assert CONTRADICTION in {p["code"] for p in out["gate"]["problems"]}
    assert any("互斥存疑" in r for r in out["gate"]["reasons"])
    assert out["human_inbox"] and out["human_inbox"][0]["code"] == CONTRADICTION


def test_contradiction_requires_shared_subject_anchor(tmp_path):
    """同轴反义词但**不共享主体**（版本号不同且二元组共享 <3）→ 不判矛盾（不同产品各涨各跌合法）。"""
    a = _item("v12.1 移动端套餐价格上调", fp="fp-a", urls=("https://a.example/p1",))
    b = _item("旧版 9.0 退款让利活动", fp="fp-b", urls=("https://a.example/p2",))
    problems = Reviewer().review({"items": [a, b]})
    assert all(p.code != CONTRADICTION for p in problems)


def test_contradiction_same_direction_not_conflict(tmp_path):
    """同版本但**同一方向**（都上调）→ 不判矛盾。"""
    a = _item("v12.1 移动端套餐价格上调", fp="fp-a", urls=("https://a.example/p1",))
    b = _item("v12.1 企业版年费上调", fp="fp-b", urls=("https://a.example/p2",))
    problems = Reviewer().review({"items": [a, b]})
    assert all(p.code != CONTRADICTION for p in problems)


# ---------- 口径合规：与上期重复 ----------

def test_duplicate_vs_unchanged_intercepts_human(tmp_path):
    """条目 fp 属上期 unchanged（把旧闻又发出来）→ DUPLICATE → 转 human（diff 已 drop，这是下游 bug）。"""
    item = _item(title="上期就报过的旧闻", fp="fp-unchanged", urls=("https://a.example/x",))
    out = _review_node([item], unchanged_fps=["fp-unchanged"])
    assert out["gate"]["verdict"] == "human"
    assert DUPLICATE in {p["code"] for p in out["gate"]["problems"]}
    assert any("旧闻" in r for r in out["gate"]["reasons"])


def test_duplicate_not_flagged_when_fp_is_fresh(tmp_path):
    item = _item(title="本期新动态", fp="fp-new", urls=("https://a.example/x",))
    problems = Reviewer().review({"items": [item]}, unchanged_fps=["fp-other"])
    assert all(p.code != DUPLICATE for p in problems)


# ---------- 路由优先级：信任问题压过结构缺口 ----------

def test_trust_beats_structural_routing_human_not_rewrite(tmp_path):
    """同一条既缺 severity（结构）又带编造 URL（信任）→ 信任优先：直转 human，不先走 REWRITE。"""
    item = _item(title="既缺分级又编造出处", fp="fp-real",
                 urls=("https://fake.example/x",), severity=None)
    out = _review_node([item], cards=[_card("https://a.example/x")],
                       changes=[{"fp": "fp-real"}])
    assert out["gate"]["verdict"] == "human" and out["rewrites"] == 0
    codes = {p["code"] for p in out["gate"]["problems"]}
    assert UNSOURCED_URL in codes and "MISSING_SEVERITY" in codes


# ---------- 图级端到端：注入假事件被拦、不进可发布态 ----------

def test_graph_injected_fake_item_intercepted_not_published(tmp_path, monkeypatch):
    """注入一条"长得合规"的假事件（真 fp 拼编造 URL）→ 门卫 human 拦下，PASS 态不存在。

    模拟下游 bug/编造入口（同 test_orchestration_graph 的 poison_writer 手法）：writer 把一条
    本应指向真实来源的条目换成了编造 URL —— Review 的 grounding 要能拦住（README2 §7.4 row 6）。
    """
    def fake_writer(state: dict, ctx=None) -> dict:
        return {"draft": {"competitor_id": state["competitor_id"], "period": state["period"],
                          "headline_count": 1, "sections": {"adds": 1, "changes": 0, "removes": 0},
                          "items": [_item(title="真事件换了个编造出处", fp=V12_FP,
                                          urls=("https://fake.example/x",))]}}

    monkeypatch.setitem(graph_mod.NODE_IMPLS, "writer", fake_writer)
    final = run_cycle(SETTINGS, COMP, "2026-W35", store=_store(tmp_path), run_id="gate-fake")
    assert final["gate"]["verdict"] == "human" and final["rewrites"] == 0  # 信任问题不消耗额度
    codes = {p["code"] for p in final["gate"]["problems"]}
    assert UNSOURCED_URL in codes                    # grounding 拦下编造 URL
    assert final["human_inbox"]                       # 落人工收件箱 → 人工确认才放行
    assert final["gate_trace"][-1]["verdict"] == "human"  # 打回有 trace
    assert final["gate"]["verdict"] != "PASS"         # 假事件绝不会进"可发布"态
