# -*- coding: utf-8 -*-
"""Phase 5 Writer：模板化周报（WeeklyReport schema）——结构一致 + 模型自校验（fail-fast）。

锁四条性质（面试可复述"为什么周报走 pydantic"）：
1. 只组装不发挥：每条 title/summary/evidence 逐字来自已验证 Event，sources 恰为条目出处并集（排序确定），
   Writer 不引入任何 items 之外的内容；
2. schema 合规在校验期强制：缺 severity / reasons 空 / 无出处 / headline 与条数对不上 / kind 计数与 diff 对不上，
   一律 raise —— 结构不整的周报发不出去；
3. 派生计数不信任调用方：threat_radar 由模型按 items 重算（手填错也纠正）；
4. 空周报合法："本周无变更"是结果不是失败（headline 0 / 雷达全 0 / sources []）。
"""
from datetime import datetime

import pytest
from pydantic import ValidationError

from memory import Dimension, EventKind, ThreatLevel
from writer import ReportItem, ThreatRadar, WeeklyReport, Writer


def _item(
    kind: str = "add",
    dim: str = "feature",
    sev: str = "medium",
    reasons: tuple[str, ...] = ("规则[feature_update] 功能/产品迭代：常规迭代（feature 维度基线）",),
    urls: tuple[str, ...] = ("https://x.example/1",),
    title: str = "更新：看板分享新增链接权限",
) -> dict:
    return {
        "fp": None,
        "kind": kind,
        "dimension": dim,
        "severity": sev,
        "reasons": list(reasons),
        "title": title,
        "summary": "正文摘要",
        "evidence_urls": list(urls),
        "first_seen": "2026-08-25",
    }


# ---------- 性质 1：只组装、不发挥 ----------

def test_build_passes_content_verbatim_and_sources_are_union():
    items = [
        _item(title="图表库 3.0 正式发布", sev="high", urls=("https://a/1", "https://a/2")),
        _item(title="2026 工具盘点", dim="market", sev="low", urls=("https://a/2", "https://b/1")),
    ]
    r = Writer().build(competitor_id="bi_beta", period="2026-W35", items=items,
                       sections={"adds": 2, "changes": 0, "removes": 0})
    assert [i.title for i in r.items] == ["图表库 3.0 正式发布", "2026 工具盘点"]   # 逐字搬运
    assert [i.summary for i in r.items] == ["正文摘要", "正文摘要"]
    assert r.sources == ["https://a/1", "https://a/2", "https://b/1"]          # 并集、去重、排序
    assert all(url in r.sources for i in r.items for url in i.evidence_urls)    # 不引入 items 外的源


# ---------- 性质 2：schema 合规 fail-fast ----------

def test_item_missing_severity_is_rejected():
    bad = _item()
    del bad["severity"]
    with pytest.raises(ValidationError):
        ReportItem.model_validate(bad)
    with pytest.raises(ValidationError):
        Writer().build(competitor_id="x", period="2026-W35", items=[bad])


def test_item_empty_reasons_is_rejected():
    bad = _item(reasons=())
    with pytest.raises(ValidationError):
        ReportItem.model_validate(bad)


def test_item_without_evidence_is_rejected():
    bad = _item(urls=())
    with pytest.raises(ValidationError):
        ReportItem.model_validate(bad)


def test_headline_count_mismatch_raises():
    with pytest.raises(ValueError):
        WeeklyReport(competitor_id="x", period="2026-W35", headline_count=2,
                     items=[ReportItem.model_validate(_item())], sections={})


def test_kind_counts_vs_diff_sections_mismatch_raises():
    """写 2 条 add 却声称 sections.adds=1 → 自校验抓出口径撒谎。"""
    items = [ReportItem.model_validate(_item()) for _ in range(2)]
    with pytest.raises(ValueError):
        Writer().build(competitor_id="x", period="2026-W35", items=[i.model_dump() for i in items],
                       sections={"adds": 1, "changes": 0, "removes": 0})


# ---------- 性质 3：派生计数重算，不信任调用方手填 ----------

def test_threat_radar_recomputed_not_trusted():
    items = [
        ReportItem.model_validate(_item(sev="high")),
        ReportItem.model_validate(_item(sev="medium")),
        ReportItem.model_validate(_item(dim="market", sev="low")),
    ]
    r = WeeklyReport(competitor_id="x", period="2026-W35", headline_count=3,
                     sections={"adds": 3, "changes": 0, "removes": 0},
                     items=items, threat_radar=ThreatRadar(high=99, medium=99, low=99))
    assert (r.threat_radar.high, r.threat_radar.medium, r.threat_radar.low) == (1, 1, 1)


def test_happy_build_radar_and_headline_consistent():
    items = [_item(sev="high"), _item(sev="low"), _item(dim="market", sev="medium")]
    r = Writer().build(competitor_id="crm_alpha", period="2026-W35", items=items,
                       sections={"adds": 3, "changes": 0, "removes": 0})
    assert r.headline_count == 3 == len(r.items)
    assert (r.threat_radar.high, r.threat_radar.medium, r.threat_radar.low) == (1, 1, 1)


# ---------- 性质 4：空周报合法 ----------

def test_empty_weekly_report_is_legal():
    r = Writer().build(competitor_id="crm_alpha", period="2026-W35", items=[])
    assert r.headline_count == 0 and r.items == []
    assert (r.threat_radar.high, r.threat_radar.medium, r.threat_radar.low) == (0, 0, 0)
    assert r.sources == [] and r.sections == {"adds": 0, "changes": 0, "removes": 0}


# ---------- JSON-safe 出模型（进共享 State / 落盘） ----------

def test_model_dump_json_is_json_serializable():
    items = [_item(sev="high"), _item(dim="market", sev="low")]
    r = Writer().build(competitor_id="bi_beta", period="2026-W35", run_id="w1",
                       generated_at=datetime(2026, 8, 31, 9, 0).isoformat(),
                       items=items, sections={"adds": 2, "changes": 0, "removes": 0})
    d = r.model_dump(mode="json")
    import json
    json.dumps(d, ensure_ascii=False)              # 能落盘 → 可被下游/审计消费
    assert d["items"][0]["severity"] == "high"     # 枚举已序列化为 .value 字符串
    assert d["items"][0]["dimension"] == "feature"
    assert d["items"][0]["kind"] == "add"
    assert d["threat_radar"] == {"high": 1, "medium": 0, "low": 1}
    assert set(d.keys()) == {"run_id", "competitor_id", "period", "generated_at",
                             "headline_count", "sections", "items", "threat_radar", "sources"}


# ---------- 端到端语义：kind=change 与 diff 计数对应 ----------

def test_change_kind_item_balances_sections():
    items = [_item(kind="change", title="v12.0 更新详情", urls=("https://a/1",))]
    r = Writer().build(competitor_id="crm_alpha", period="2026-W35", items=items,
                       sections={"adds": 0, "changes": 1, "removes": 0})
    assert r.headline_count == 1 and r.items[0].kind is EventKind.change
    assert r.sections == {"adds": 0, "changes": 1, "removes": 0}
