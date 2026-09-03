# -*- coding: utf-8 -*-
"""Phase 8 Eval-Harness 测试：metrics 手算 / gold 独立性 / 真实回放权威数字 / 对抗 6/6 / 门禁。"""
from __future__ import annotations

import copy
import json

import pytest

from config.settings import get_settings
from eval import dataset
from eval import metrics
from eval.harness import EvalHarness, render_markdown
from eval.run import check_gates

SETTINGS = get_settings()


@pytest.fixture(scope="module")
def report(tmp_path_factory) -> dict:
    """跑一次完整回放评测（真实 + 对抗），全模块共享一次以省时间。"""
    root = tmp_path_factory.mktemp("eval-report")
    return EvalHarness().run(tmp_root=str(root))


# ---------------- metrics 纯函数手算 ----------------

def test_metrics_match_gold_hand_computed():
    items = [
        {"title": "Alpha CRM v12.0 正式发布：智能跟进助手 GA", "severity": "high"},
        {"title": "实测 Alpha CRM 跟进助手", "severity": "medium"},
        {"title": "预告：v12.1 移动端日历与待办同步", "severity": "low"},
    ]
    gold = [
        {"anchor": "v12.0", "severity": "high"},
        {"anchor": "实测", "severity": "medium"},
        {"anchor": "行级权限", "severity": "low"},   # 不存在 → 漏匹配
    ]
    matched, total = metrics.match_gold(items, gold)
    assert (matched, total) == (2, 3)          # 贪婪、逐条只用一次


def test_metrics_match_gold_severity_must_agree():
    items = [{"title": "图表库 3.0 正式发布", "severity": "low"}]  # 引擎判 low、gold 标 high
    gold = [{"anchor": "图表库 3.0", "severity": "high"}]
    assert metrics.match_gold(items, gold) == (0, 1)


def test_metrics_grounded_fraction():
    card_urls = {"https://a.example/x", "https://b.example/y"}
    items = [
        {"evidence_urls": ["https://a.example/x"]},
        {"evidence_urls": ["https://a.example/x", "https://b.example/y"]},
        {"evidence_urls": ["https://fake.example/z"]},          # 编造出处
    ]
    assert metrics.grounded_fraction(items, card_urls) == (2, 3)


def test_metrics_structure_pass():
    # 合规正例：一条 kind/dimension/severity/reasons/出处 齐全的 add（headline 与 sections 与 items 自洽）
    good = {
        "competitor_id": "crm_alpha", "period": "2026-W35", "headline_count": 1,
        "sections": {"adds": 1, "changes": 0, "removes": 0},
        "items": [{"kind": "add", "dimension": "feature", "severity": "medium",
                   "reasons": ["规则[feature_update] 常规迭代"],
                   "title": "t", "evidence_urls": ["https://a.example/x"]}],
    }
    assert metrics.structure_pass(good) is True
    # 反例：缺分级（severity 非空枚举）+ headline 与条数不符 → schema 不过
    bad = {"competitor_id": "crm_alpha", "period": "2026-W35",
           "items": [{"title": "x", "severity": None}]}
    assert metrics.structure_pass(bad) is False


def test_metrics_trace_consistent_hand():
    ok_final = {
        "trace": {"collected": 2, "merged": 2, "diff": 1},
        "cards": [{"source_url": "a"}, {"source_url": "b"}],
        "events": [{"fp": "e1"}, {"fp": "e2"}],
        "changes": [{"fp": "e1"}],
        "draft": {"items": [{"title": "t"}],
                  "threat_radar": {"high": 1, "medium": 0, "low": 0}},
    }
    good, reasons = metrics.trace_consistent(ok_final)
    assert good and reasons == []

    ok_final["draft"]["threat_radar"] = {"high": 2, "medium": 0, "low": 0}  # 雷达和 2 != diff 1
    good, reasons = metrics.trace_consistent(ok_final)
    assert not good and any("radar" in r for r in reasons)


def test_metrics_summarize_case():
    final = {"gate": {"verdict": "human", "problems": [{"code": "UNSOURCED_URL"}]},
             "human_inbox": [{"code": "UNSOURCED_URL"}]}
    caught, codes, inbox = metrics.summarize_case(final, {"UNSOURCED_URL"})
    assert caught and codes == {"UNSOURCED_URL"} and inbox == {"UNSOURCED_URL"}
    # 期望 code 不在收件箱 → 不算拦下（不能只 verdict human 就算数）
    final["human_inbox"] = []
    assert metrics.summarize_case(final, {"UNSOURCED_URL"})[0] is False


# ---------------- gold 独立性：锚/组都来自 fixtures 原文（不反推引擎） ----------------

def _fixture_items(competitor: str) -> list[dict]:
    p = SETTINGS.fixtures_path / "sources" / f"{competitor}.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return data["items"]


def test_gold_events_anchor_present_in_fixture_titles():
    for e in dataset.W35_GOLD_EVENTS:
        titles = [it["title"] for it in _fixture_items(e["competitor"])]
        assert any(e["anchor"] in t for t in titles), \
            f"gold anchor {e['anchor']!r} 不在 {e['competitor']} fixtures 原文标题里——标注脱离语料"


def test_gold_duplicate_group_urls_are_real_sources():
    for g in dataset.DUPLICATE_GROUPS:
        urls = {it["url"] for it in _fixture_items(g["competitor"])}
        assert set(g["urls"]) <= urls, f"组 {g['anchor']} 的 url 不在 fixtures 来源里"
        assert g["n"] == len(g["urls"]) and g["n"] >= 2


def test_gold_health_check_passes():
    assert dataset.gold_health_check() == []


# ---------------- 真实回放：权威数字（非编造，harnss 实测） ----------------

def test_report_real_replay_canonical_numbers(report):
    m = report["metrics"]
    assert m["gold_health_ok"]
    assert m["event_agreement"] == {"matched": 7, "total": 7, "rate": 1.0}
    assert m["dedupe"] == {"groups_merged_ok": True, "event_count_ok": True}
    assert m["source_accuracy"]["grounded_items"] == 7
    assert m["structure_compliance"]["ok_runs"] == 4
    assert m["trace_consistent"] is True
    assert m["real_false_positive"] == 0

    by = {r["competitor"]: r for r in report["per_competitor"]}
    crm, bi = by["crm_alpha"], by["bi_beta"]
    assert crm["trace_w35"] == {"collected": 5, "merged": 3, "diff": 3}
    assert bi["trace_w35"] == {"collected": 5, "merged": 4, "diff": 4}
    assert crm["items_w35"] == 3 and crm["gold_events"] == 3 and crm["event_matched"] == 3
    assert bi["items_w35"] == 4 and bi["gold_events"] == 4 and bi["event_matched"] == 4
    assert crm["radar_w35"] == {"high": 1, "medium": 1, "low": 1}
    assert bi["radar_w35"] == {"high": 1, "medium": 1, "low": 2}
    assert crm["verdict_w35"] == "PASS" and crm["inbox_w35"] == 0
    assert bi["verdict_w35"] == "PASS" and bi["inbox_w35"] == 0


# ---------------- 对抗：6/6 拦截 + 逐码纯净 ----------------

def test_report_adversarial_all_six_caught_with_exact_codes(report):
    rows = report["adversarial_cases"]
    assert len(rows) == 6
    fi = report["metrics"]["fabrication_interception"]
    assert (fi["caught"], fi["total"]) == (6, 6)
    for row in rows:
        assert row["caught"] is True, f"{row['name']} 没被拦"
        assert row["verdict"] == "human"
        # 单码纯净：门卫判定 codes == 期望 code（假事件不会带无关脏码蒙混）
        assert set(row["gate_codes"]) == set(row["expected"]), row
        assert set(row["inbox_codes"]) == set(row["expected"]), row


def test_report_graph_level_used_for_five_and_gate_level_for_duplicate(report):
    levels = {r["name"]: r["level"] for r in report["adversarial_cases"]}
    graph_names = {"unsourced_url", "ungrounded_fp", "ungrounded_claim",
                   "contradiction", "mixed_batch"}
    assert {n for n, lv in levels.items() if lv == "graph"} == graph_names
    assert levels["duplicate"] == "gate"   # 旧闻需 unchanged 上下文，门卫级复现


# ---------------- 门禁 ----------------

def test_gates_pass_on_canonical_report(report):
    assert check_gates(report, SETTINGS) == []


def test_gates_fail_when_metric_tampered(report):
    bad = copy.deepcopy(report)
    bad["metrics"]["fabrication_interception"]["rate"] = 0.5
    fails = check_gates(bad, SETTINGS)
    assert any("编造拦截率" in f for f in fails)

    bad2 = copy.deepcopy(report)
    bad2["metrics"]["real_false_positive"] = 2
    assert any("真实语料误拦" in f for f in check_gates(bad2, SETTINGS))

    bad3 = copy.deepcopy(report)
    bad3["metrics"]["trace_consistent"] = False
    assert any("trace" in f for f in check_gates(bad3, SETTINGS))


def test_render_markdown_reports_all_sections(report):
    md = render_markdown(report)
    for needle in ("口径声明", "事件一致率", "编造拦截率", "来源/grounding", "trace 计数链",
                   "crm_alpha", "bi_beta", "unsourced_url", "duplicate"):
        assert needle in md
