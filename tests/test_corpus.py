# -*- coding: utf-8 -*-
"""Mock 语料装载：两周 fixture 切成两期、条数与 fixture 一致、两次装载完全一致（回放确定性）。"""
from datetime import datetime

from config.settings import get_settings
from memory import build_period_cards, list_competitors

MOCK_DIR = get_settings().fixtures_path / "sources"
FETCHED_AT = datetime(2026, 8, 31, 9, 0, 0)  # 固定入库时间 → 装载可回归


def test_list_competitors_scans_fixtures():
    assert list_competitors(MOCK_DIR) == ["bi_beta", "crm_alpha"]


def test_corpus_partitions_two_iso_weeks_with_fixture_counts():
    """2026-W34=08-17..08-23 / W35=08-24..08-30；两竞品各 7 条，分布 2+5。"""
    for competitor, expected in {"crm_alpha": 7, "bi_beta": 7}.items():
        periods = build_period_cards(MOCK_DIR, competitor, FETCHED_AT)
        assert list(periods) == ["2026-W34", "2026-W35"]  # 期按字典序即时间序
        assert sum(len(cards) for cards in periods.values()) == expected
        assert len(periods["2026-W34"]) == 2
        assert len(periods["2026-W35"]) == 5


def test_build_is_replayable_deterministic():
    first = build_period_cards(MOCK_DIR, "crm_alpha", FETCHED_AT)
    second = build_period_cards(MOCK_DIR, "crm_alpha", FETCHED_AT)
    assert first == second  # FactCard 逐字段相等
    # 跨竞品稳定：bi_beta 每次同样两期
    assert list(build_period_cards(MOCK_DIR, "bi_beta", FETCHED_AT)) == ["2026-W34", "2026-W35"]


def test_unknown_competitor_yields_empty_periods():
    assert build_period_cards(MOCK_DIR, "no_such_competitor", FETCHED_AT) == {}
