# -*- coding: utf-8 -*-
"""核心数据对象约束：FactCard 必带 source_url、id/digest 派生且确定、Event 证据非空。"""
from datetime import datetime

import pytest
from pydantic import ValidationError

from memory import Dimension, Event, EventKind, FactCard, Snapshot

T = datetime(2026, 8, 25, 8, 0, 0)


def _card(**over):
    base = dict(
        competitor_id="crm_alpha",
        source_url="https://alpha-crm.example.com/blog/v12-ga",
        title="Alpha CRM v12.0 正式发布：智能跟进助手 GA",
        summary="面向全部付费版开放。",
        published_at=T,
        fetched_at=T,
    )
    base.update(over)
    return FactCard(**base)


def test_fact_card_requires_source_url():
    with pytest.raises(ValidationError):
        _card(source_url="")


def test_fact_card_id_and_digest_derived_deterministically():
    a, b = _card(), _card()
    assert a.id and a.digest and b.id == a.id and b.digest == a.digest
    # id = sha1(竞品|url|published_at)；不同原文时间 → 不同 id（同 URL 更新算新证据）
    assert _card(published_at=datetime(2026, 8, 25, 9, 0)).id != a.id


def test_fact_card_digest_is_normalize_sensitive_not_case_sensitive():
    # digest 基于 normalize_title：纯大小写/全半角差异 → 相同 digest；措辞真变 → 不同
    assert _card(title="Alpha CRM v12.0 正式发布").digest == _card(
        title="ALPHA CRM v12.0 正式发布"
    ).digest
    assert _card(title="Alpha CRM v12.0 正式发布").digest != _card(
        title="Alpha CRM 上线 AI 助手"
    ).digest


def test_event_requires_evidence():
    base = dict(
        id="e1",
        competitor_id="crm_alpha",
        dimension=Dimension.feature,
        kind=EventKind.add,
        title="v12.0 智能跟进助手 GA",
        first_seen=T.date(),
        last_seen=T.date(),
    )
    with pytest.raises(ValidationError):
        Event(evidence_urls=[], **base)  # 无来源支撑的情报不成立
    ev = Event(evidence_urls=["https://a.example.com/x"], **base)
    assert ev.severity is None  # Phase 5 由 Analyst 填
    assert ev.fp is None  # Phase 3 由指纹/diff 层填


def test_snapshot_defaults_empty_fps():
    snap = Snapshot(period="2026-W35", competitor_id="crm_alpha")
    assert snap.event_fps == []
    assert snap.created_at is not None
