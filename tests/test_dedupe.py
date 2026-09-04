# -*- coding: utf-8 -*-
"""Phase 3 Dedupe：确定性维度分类 + 去重合并（同事件多源并一条、evidence_urls 聚合）。

真值来源 = fixtures 逐条核对（README2 §5.4 / docs/memory.md 期表）：
- crm_alpha W35：v12.0 发布官网博客+changelog rss+媒体 3 源并入 1（evid=3）；评测/预告各 1 → 5 cards→3 events
- bi_beta   W35：图表库 3.0 官网+媒体 2 源并入 1（evid=2）；其余 3 单 → 5 cards→4 events
- 负例：followup 评测 / mobile121 预告(v121≠v120) / roundup 盘点(market) 都不并入发布事件。
"""
from datetime import datetime

from config.settings import get_settings
from dedupe import (
    classify_dimension,
    merge_to_events,
    read_competitor_aliases,
    version_anchor,
)
from memory import Dimension, build_period_cards, list_competitors

MOCK_DIR = get_settings().fixtures_path / "sources"
FETCHED_AT = datetime(2026, 8, 31, 9, 0, 0)
ALIASES = read_competitor_aliases(MOCK_DIR)


def _period_cards(competitor: str) -> dict[str, list]:
    return build_period_cards(MOCK_DIR, competitor, FETCHED_AT)


def _merge(competitor: str, period: str):
    cards = _period_cards(competitor)[period]
    return merge_to_events(cards, period=period, aliases=ALIASES)


def _by_url(events):
    return {u: e for e in events for u in e.evidence_urls}


# ---------- classify_dimension：fixtures 落点全锁 ----------

def test_classify_fixture_cards_land_on_expected_dimension():
    """每个 fixture url → 期望维度（发布/更新/功能动态全 feature；行业盘点 = market）。"""
    expected = {
        "https://alpha-crm.example.com/blog/api-v2": "feature",
        "https://alpha-crm.example.com/changelog.rss#excel-import": "feature",
        "https://alpha-crm.example.com/blog/v12-ga": "feature",
        "https://alpha-crm.example.com/changelog.rss#v12-ga": "feature",
        "https://techwire.example.com/p/alpha-crm-v12": "feature",
        "https://saasreview.example.com/a/alpha-followup": "feature",   # 无关键词 → 默认 feature
        "https://alpha-crm.example.com/changelog.rss#mobile-121": "feature",
        "https://beta-bi.example.com/blog/connectors-6": "feature",
        "https://beta-bi.example.com/changelog.rss#ai-sql": "feature",
        "https://beta-bi.example.com/changelog.rss#share-perm": "feature",
        "https://beta-bi.example.com/blog/charts3-mobile": "feature",
        "https://datanews.example.com/beta-bi-charts3": "feature",      # summary 含"发布"→feature
        "https://techwire.example.com/p/bi-tools-roundup-2026": "market",
        "https://beta-bi.example.com/changelog.rss#row-level": "feature",
    }
    for comp in list_competitors(MOCK_DIR):
        for cards in _period_cards(comp).values():
            for c in cards:
                assert classify_dimension(c.title, c.summary).value == expected[c.source_url], c.source_url


def test_classify_synthetic_samples_per_dimension():
    assert classify_dimension("Alpha CRM v13 上线：销售预测模块正式发布") == Dimension.feature
    assert classify_dimension("Alpha CRM 全产品线调价：订阅年费上涨 20%") == Dimension.price
    assert classify_dimension("BI 行业盘点：自助式厂商押注出海增长") == Dimension.market
    assert classify_dimension("Alpha CRM 任命新 CEO 并调整组织战略") == Dimension.org
    assert classify_dimension("Beta BI 通过 ISO 27001 认证并完成备案") == Dimension.compliance


# ---------- version_anchor ----------

def test_version_anchor_extracts_release_identity():
    # 锚保留小数点结构（回归）：'1.20' 与 '12.0' 是不同版本，绝不因去点拉平撞锚（防误并不同发布事件）
    assert version_anchor("Alpha CRM v12.0 正式发布：智能跟进助手 GA") == "12.0"
    assert version_anchor("【发布】v12.0 智能跟进助手 GA") == "12.0"
    assert version_anchor("Beta BI 图表库 3.0：面向业务人员的自助可视化") == "3.0"
    assert version_anchor("预告：v12.1 移动端日历与待办同步") == "12.1"
    assert version_anchor("修复 1.20 的导出崩溃（v1.20 补丁发布）") == "1.20"  # ≠ "12.0"
    assert version_anchor("2026 商业智能工具盘点：自助式分析成主线") is None   # 年份不匹配
    assert version_anchor("开放平台 API v2 上线：Webhook 事件订阅") is None      # v2 无小数点不算发布锚


# ---------- merge：fixtures 真值 ----------

def test_crm_w35_v12_trio_merges_into_one_with_three_evidences():
    events, summ = _merge("crm_alpha", "2026-W35")
    assert summ.cards_in == 5 and summ.events_out == 3 and summ.merges == 2
    v12 = [e for e in events if e.fp == "5c60e045b8853e63"]
    assert len(v12) == 1
    e = v12[0]
    assert set(e.evidence_urls) == {
        "https://alpha-crm.example.com/blog/v12-ga",
        "https://alpha-crm.example.com/changelog.rss#v12-ga",
        "https://techwire.example.com/p/alpha-crm-v12",
    }
    assert e.dimension == Dimension.feature
    assert (e.first_seen, e.last_seen) == (datetime(2026, 8, 25).date(), datetime(2026, 8, 25).date())
    assert e.title.startswith("Alpha CRM v12.0")  # canonical = 最早发布卡（官网博客 08:00）
    assert len(e.evidence_urls) == 3


def test_bi_w35_charts_pair_merges_with_two_evidences():
    events, summ = _merge("bi_beta", "2026-W35")
    assert summ.cards_in == 5 and summ.events_out == 4 and summ.merges == 1
    charts = [e for e in events if len(e.evidence_urls) == 2]
    assert len(charts) == 1
    assert set(charts[0].evidence_urls) == {
        "https://beta-bi.example.com/blog/charts3-mobile",
        "https://datanews.example.com/beta-bi-charts3",
    }


def test_no_merge_weeks_without_duplicates():
    for comp, period in [("crm_alpha", "2026-W34"), ("bi_beta", "2026-W34")]:
        _, summ = _merge(comp, period)
        assert summ.cards_in == 2 and summ.events_out == 2 and summ.merges == 0


def test_negatives_stay_separate_events():
    """followup 评测 / v12.1 预告 / roundup 盘点 各自单条，绝不并入 v12/图表库 发布事件。"""
    crm, _ = _merge("crm_alpha", "2026-W35")
    crm_by_url = _by_url(crm)
    assert len(crm_by_url["https://saasreview.example.com/a/alpha-followup"].evidence_urls) == 1
    assert len(crm_by_url["https://alpha-crm.example.com/changelog.rss#mobile-121"].evidence_urls) == 1
    fps = {e.fp for e in crm}
    assert len(fps) == 3  # v12 / followup / mobile121 三者不同 fp

    bi, _ = _merge("bi_beta", "2026-W35")
    bi_by_url = _by_url(bi)
    roundup = bi_by_url["https://techwire.example.com/p/bi-tools-roundup-2026"]
    assert roundup.dimension == Dimension.market      # 维度不同，本就不会并入 feature 簇
    assert len(bi_by_url["https://beta-bi.example.com/changelog.rss#row-level"].evidence_urls) == 1


def test_events_have_fp_id_and_min_one_evidence():
    for comp in list_competitors(MOCK_DIR):
        for period in _period_cards(comp):
            events, _ = _merge(comp, period)
            for e in events:
                assert e.fp and len(e.fp) == 16
                assert e.id and len(e.id) == 16
                assert len(e.evidence_urls) >= 1
                assert e.evidence_urls == sorted(e.evidence_urls)  # 证据升序 → 可溯源确定


def test_merge_is_replayable_deterministic():
    for comp, period in [("crm_alpha", "2026-W35"), ("bi_beta", "2026-W35")]:
        e1, s1 = _merge(comp, period)
        e2, s2 = _merge(comp, period)
        assert s1 == s2
        proj = lambda es: [(x.fp, x.evidence_urls, x.title, x.summary) for x in es]
        assert proj(e1) == proj(e2)


def test_read_competitor_aliases_returns_fixture_names():
    assert set(ALIASES) == {"crm_alpha", "bi_beta"}
    assert "Alpha CRM" in ALIASES["crm_alpha"]
    assert "Beta" in ALIASES["bi_beta"]
