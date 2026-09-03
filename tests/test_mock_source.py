# -*- coding: utf-8 -*-
"""MockSource：按 竞品×kind×since 读 fixtures 离线数据，验证 fixture 语义（含为去重预置的多源重复事件）。"""
import asyncio
from datetime import datetime

from config.settings import get_settings
from sources import MockSource

SOURCE = MockSource(get_settings().fixtures_path / "sources")


def _fetch(competitor_id: str, kind: str, since: datetime | None = None):
    return asyncio.run(SOURCE.fetch(competitor_id, kind, since=since))


def test_fetch_filters_by_kind_and_sorted_asc():
    website = _fetch("crm_alpha", "website")
    assert website and all(it.kind == "website" for it in website)
    assert website == sorted(website, key=lambda it: it.published_at)


def test_fetch_since_returns_incremental_window():
    since = datetime(2026, 8, 24)
    rss = _fetch("bi_beta", "rss", since=since)
    assert all(it.published_at >= since for it in rss)
    assert {it.title for it in rss} == {
        "更新：看板分享新增链接权限与过期时间",
        "预告：目录级与行级权限管控下月开放",
    }


def test_fetch_unknown_competitor_is_empty():
    assert _fetch("no_such_competitor", "website") == []


def test_fixture_preseeded_cross_source_duplicate_event():
    """同一 v12.0 发布事件故意出现在 website(官网)+rss(changelog)+news(媒体) —— 供 Phase 3 去重指纹验证。"""
    titles = {
        (it.kind, it.title)
        for it in _fetch("crm_alpha", "website") + _fetch("crm_alpha", "news") + _fetch("crm_alpha", "rss")
        if it.published_at.date().isoformat() == "2026-08-25"
    }
    # 三处都在同日、标题都能归一到 “v12.0 / 跟进助手 GA”
    assert len(titles) == 3
    assert all("v12" in t or "v12.0" in t for _, t in titles)
