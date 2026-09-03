# -*- coding: utf-8 -*-
"""MemoryStore JSON/SQLite 双后端：round-trip、同 period 幂等覆盖、latest_snapshot 语义。

同一套断言参数化跑两个后端 → 证明"上层零感知换存储"。全部落在 pytest tmp_path，不碰仓库真 data/。
"""
from datetime import datetime

import pytest

from memory import FactCard, JsonMemoryStore, MemoryStore, Snapshot, SqliteMemoryStore

T1 = datetime(2026, 8, 25, 10, 0, 0)
T2 = datetime(2026, 8, 25, 11, 0, 0)


def _cards() -> list[FactCard]:
    return [
        FactCard(
            competitor_id="crm_alpha",
            source_url="https://alpha-crm.example.com/changelog.rss#v12-ga",
            title="【发布】v12.0 智能跟进助手 GA",
            published_at=T1,
            fetched_at=T2,
        ),
        FactCard(
            competitor_id="crm_alpha",
            source_url="https://alpha-crm.example.com/blog/v12-ga",
            title="Alpha CRM v12.0 正式发布：智能跟进助手 GA",
            published_at=T2,
            fetched_at=T2,
        ),
    ]


def _make_store(store_cls: type[MemoryStore], tmp_path) -> MemoryStore:
    return store_cls(tmp_path / "memory")


@pytest.mark.parametrize("store_cls", [JsonMemoryStore, SqliteMemoryStore], ids=["json", "sqlite"])
def test_roundtrip_and_deterministic_order(store_cls, tmp_path):
    store = _make_store(store_cls, tmp_path)
    cards = _cards()
    store.save_fact_cards("crm_alpha", "2026-W35", cards)

    back = store.list_fact_cards("crm_alpha", "2026-W35")
    assert back == sorted(cards, key=lambda c: (c.published_at.isoformat(), c.source_url))
    # 回读内容逐字段一致
    assert back[0].source_url == "https://alpha-crm.example.com/changelog.rss#v12-ga"
    assert back[0].digest == cards[0].digest and back[0].id == cards[0].id


@pytest.mark.parametrize("store_cls", [JsonMemoryStore, SqliteMemoryStore], ids=["json", "sqlite"])
def test_same_period_overwrite_is_idempotent(store_cls, tmp_path):
    store = _make_store(store_cls, tmp_path)
    store.save_fact_cards("crm_alpha", "2026-W35", _cards())
    store.save_fact_cards("crm_alpha", "2026-W35", _cards())  # 重跑不翻倍
    assert len(store.list_fact_cards("crm_alpha", "2026-W35")) == 2
    # 覆盖 = 替换而非追加：第二期内容换小集合同样生效
    smaller = [_cards()[0]]
    store.save_fact_cards("crm_alpha", "2026-W35", smaller)
    assert [c.source_url for c in store.list_fact_cards("crm_alpha", "2026-W35")] == [
        c.source_url for c in smaller
    ]


@pytest.mark.parametrize("store_cls", [JsonMemoryStore, SqliteMemoryStore], ids=["json", "sqlite"])
def test_list_missing_period_is_empty(store_cls, tmp_path):
    assert _make_store(store_cls, tmp_path).list_fact_cards("crm_alpha", "2026-W33") == []


@pytest.mark.parametrize("store_cls", [JsonMemoryStore, SqliteMemoryStore], ids=["json", "sqlite"])
def test_snapshot_latest_returns_newest_period(store_cls, tmp_path):
    store = _make_store(store_cls, tmp_path)
    assert store.latest_snapshot("crm_alpha") is None  # 无档

    store.save_snapshot(Snapshot(period="2026-W34", competitor_id="crm_alpha", event_fps=["a", "b"]))
    store.save_snapshot(Snapshot(period="2026-W35", competitor_id="crm_alpha", event_fps=["c"]))
    latest = store.latest_snapshot("crm_alpha")
    assert latest is not None
    assert latest.period == "2026-W35" and latest.event_fps == ["c"]
    # 竞品隔离：另一竞品无档
    assert store.latest_snapshot("bi_beta") is None


@pytest.mark.parametrize("store_cls", [JsonMemoryStore, SqliteMemoryStore], ids=["json", "sqlite"])
def test_artifact_lands_on_disk(store_cls, tmp_path):
    root = tmp_path / "memory"
    store = _make_store(store_cls, tmp_path)
    store.save_fact_cards("crm_alpha", "2026-W35", _cards())
    store.save_snapshot(Snapshot(period="2026-W35", competitor_id="crm_alpha", event_fps=["c"]))
    if store_cls is JsonMemoryStore:
        assert (root / "fact_cards" / "crm_alpha" / "2026-W35.json").exists()
        assert (root / "snapshots" / "crm_alpha" / "2026-W35.json").exists()
    else:
        assert (root / "memory.db").exists()
