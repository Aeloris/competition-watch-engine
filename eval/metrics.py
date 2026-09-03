# -*- coding: utf-8 -*-
"""评测指标（Phase 8 Eval）：**纯函数**，只吃 dict/list，不吃引擎/网络，手算即可回归。

指标口径（对齐 README2 §5.11，全部在"固定语料回放"上实测）：
- 事件一致率：gold 标注的真实事件，引擎 diff 出的条目标题能命中锚、severity 一致 → 召回同义项；
- 去重合并一致：gold 的"多源应并一条"组合，引擎合并后证据集逐组一致、事件总数不欠并/不过并；
- 来源/grounding 准确率：真实周报每条 evidence_url ⊆ 本期采集卡 URL 集（闭环自证，0 编造）；
- 结构合规率：周报 draft 能通过 WeeklyReport schema 校验；
- trace 计数链一致：collected==cards 数、merged==events 数、diff==changes 数、
  radar 各项之和==diff、draft.items 数==diff（README2 §5.10 计数链收口）；
- 编造拦截率 / 真实误拦：对抗场景 caught/总注入；真实语料 0 误拦（全 PASS、收件箱空）。

所有函数确定性：同输入永远同输出，测试里逐只手算断言。
"""
from __future__ import annotations

from writer.report import WeeklyReport

TRUST_CODES = ("UNSOURCED_URL", "UNGROUNDED_FP", "UNGROUNDED_CLAIM", "CONTRADICTION", "DUPLICATE")


# ---------------- 事件一致率 ----------------

def match_gold(engine_items: list[dict], gold_events: list[dict]) -> tuple[int, int]:
    """每个 gold 事件找一个引擎条目：severity 相同且 anchor 命中条目标题。每个条目最多匹配一次。

    返回 (匹配数, gold 总数)。贪心匹配，确定性。
    """
    used: set[int] = set()
    matched = 0
    for g in gold_events:
        for idx, it in enumerate(engine_items):
            if idx in used:
                continue
            if g["severity"] == it.get("severity") and g["anchor"] in (it.get("title") or ""):
                matched += 1
                used.add(idx)
                break
    return matched, len(gold_events)


# ---------------- 去重合并一致 ----------------

def dedupe_groups_ok(merged_events: list[dict], groups: list[dict]) -> bool:
    """每个 gold 多源组必须且只能并入"一个"引擎事件，且其证据集 == 该组全部 URL（不过并/不欠并）。"""
    for g in groups:
        hits = [e for e in merged_events if set(e.get("evidence_urls") or []) == set(g["urls"])]
        if len(hits) != 1:
            return False
    return True


def event_count_matches(merged_events: list[dict], expected: int) -> bool:
    """引擎合并后的事件总数 == 该竞品 gold 独立事件数（防跨事件误并 / 误拆）。"""
    return len(merged_events) == expected


# ---------------- 来源/grounding 准确率 ----------------

def grounded_fraction(items: list[dict], card_urls: set[str]) -> tuple[int, int]:
    """真实周报里每条 evidence_url 全部 ∈ 本期采集卡 URL 集 的条数 / 总条数（闭环自证 0 编造）。"""
    ok = sum(1 for it in items if set(it.get("evidence_urls") or []) <= card_urls)
    return ok, len(items)


# ---------------- 结构合规率 ----------------

def structure_pass(draft: dict) -> bool:
    """draft 能通过 WeeklyReport schema 校验（severity/理由/出处齐全、计数/雷达自洽）即合规。"""
    try:
        WeeklyReport.model_validate(draft)
        return True
    except Exception:
        return False


# ---------------- trace 计数链一致 ----------------

def trace_consistent(final_state: dict) -> tuple[bool, list[str]]:
    """校验 README2 §5.10 计数链：N→M→K 与 cards/events/changes/draft.items/雷达逐项自洽。"""
    reasons: list[str] = []
    trace = final_state.get("trace", {})
    cards = final_state.get("cards") or []
    events = final_state.get("events") or []
    changes = final_state.get("changes") or []
    items = (final_state.get("draft") or {}).get("items") or []
    radar = (final_state.get("draft") or {}).get("threat_radar") or {}

    def _eq(name: str, a, b) -> None:
        if a != b:
            reasons.append(f"{name}: {a} != {b}")

    _eq("collected==cards 数", trace.get("collected"), len(cards))
    _eq("merged==events 数", trace.get("merged"), len(events))
    _eq("diff==changes 数", trace.get("diff"), len(changes))
    _eq("radar 之和==diff", sum((radar.get(k) or 0) for k in ("high", "medium", "low")), trace.get("diff"))
    _eq("draft.items==diff", len(items), trace.get("diff"))
    return not reasons, reasons


# ---------------- 拦截 / 误拦 ----------------

def detection_rate(caught: int, total: int) -> float:
    """编造拦截率 = 被门卫拦下(转 human 且落对应 code)的对抗场景 / 注入场景总数。"""
    return caught / total if total else 0.0


def summarize_case(final: dict, expected: set[str]) -> tuple[bool, set[str], set[str]]:
    """从一次对抗回放的最终 State 归纳：是否被拦 + 门卫判定 codes + 收件箱 codes。

    拦下口径 = verdict 非 PASS（信任问题直转 human）且收件箱含期望 code。
    """
    gate = final.get("gate") or {}
    codes = {p.get("code") for p in gate.get("problems", [])}
    inbox_codes = {e.get("code") for e in final.get("human_inbox", [])}
    caught = gate.get("verdict") != "PASS" and bool(expected & inbox_codes)
    return caught, codes, inbox_codes
