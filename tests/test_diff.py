# -*- coding: utf-8 -*-
"""Phase 3 Memory/Diff：跨周期 diff 把本期事件对上期快照 → 新增/变更/消失（README2 §4.4/§5.5）。

诚实口径锁死在测试里：
- remove 只来自显式撤回（removed_fps）；上期有、本期没报道 ≠ 消失（不自动推断）；
- change 靠"内容摘要 digest"判定（证据并集 + 摘要），同 fp 摘要变才算变更；
- unchanged = 同 fp 同摘要 → 零重复计算（省 token 的机器）。
"""
from datetime import date, datetime

import pytest

from config.settings import get_settings
from memory import (
    Dimension,
    Event,
    EventKind,
    JsonMemoryStore,
    Snapshot,
    SqliteMemoryStore,
    build_snapshot,
    diff_event_sets,
    event_content_digest,
    mark_removed,
)

COMP = "crm_alpha"
FP_A, FP_B, FP_C, FP_D, FP_X = (f"{c}" * 16 for c in "abcdx")
U1, U2, U3 = "https://x.example/1", "https://x.example/2", "https://x.example/3"


def _ev(fp: str, *, title: str = "标题", summary: str = "摘要",
        urls=(U1,), dim: Dimension = Dimension.feature) -> Event:
    return Event(
        id=fp[:16],
        competitor_id=COMP,
        dimension=dim,
        kind=EventKind.add,   # 占位；diff 会按比对结果覆写
        title=title,
        summary=summary,
        evidence_urls=list(urls),
        severity=None,
        first_seen=date(2026, 8, 25),
        last_seen=date(2026, 8, 25),
        fp=fp,
    )


# ---------- 内容摘要 ----------

def test_content_digest_stable_and_sensitive():
    a = _ev(FP_A, title="T", summary="同一摘要", urls=(U1,))
    assert event_content_digest(a) == event_content_digest(_ev(FP_A, title="T", summary="同一摘要", urls=(U1,)))
    # 摘要变了 → 变
    assert event_content_digest(a) != event_content_digest(_ev(FP_A, title="T", summary="摘要变了", urls=(U1,)))
    # 证据集变了（新增佐证 url）→ 变
    assert event_content_digest(a) != event_content_digest(_ev(FP_A, title="T", summary="同一摘要", urls=(U1, U2)))


# ---------- 快照 ----------

def test_build_snapshot_records_fps_and_digests():
    snap = build_snapshot([_ev(FP_C), _ev(FP_A)], COMP, "2026-W35")
    assert snap.competitor_id == COMP and snap.period == "2026-W35"
    assert snap.event_fps == [FP_A, FP_C]          # 升序
    assert set(snap.event_digests) == {FP_A, FP_C}
    assert snap.event_digests[FP_A] == event_content_digest(_ev(FP_A))


@pytest.mark.parametrize("store_cls", [JsonMemoryStore, SqliteMemoryStore])
def test_snapshot_event_digests_roundtrip_both_backends(tmp_path, store_cls):
    """Snapshot 新字段 event_digests 在 json/sqlite 双后端存取得回（向后兼容无破坏）。"""
    snap = build_snapshot([_ev(FP_A, summary="S"), _ev(FP_B, summary="S2")], COMP, "2026-W35")
    store = store_cls(tmp_path)
    store.save_snapshot(snap)
    loaded = store.latest_snapshot(COMP)
    assert loaded is not None
    assert loaded.model_dump() == snap.model_dump()  # 含 event_digests + created_at 全字段一致


# ---------- diff 四分类 ----------

def test_diff_add_when_fp_not_in_prev():
    prev = build_snapshot([_ev(FP_A)], COMP, "2026-W34")
    cs = diff_event_sets(prev, [_ev(FP_C)])
    assert [e.fp for e in cs.adds] == [FP_C]
    assert all(e.kind == EventKind.add for e in cs.adds)
    assert cs.changes == [] and cs.removed_fps == [] and cs.unchanged_fps == []


def test_diff_unchanged_same_fp_same_digest_skips():
    prev = build_snapshot([_ev(FP_A, summary="S", urls=(U1,))], COMP, "2026-W34")
    cs = diff_event_sets(prev, [_ev(FP_A, summary="S", urls=(U1,))])
    assert cs.unchanged_fps == [FP_A]
    assert cs.adds == [] and cs.changes == [] and cs.removed_fps == []


def test_diff_change_when_same_fp_content_changed():
    prev = build_snapshot([_ev(FP_B, summary="旧摘要", urls=(U1,))], COMP, "2026-W34")
    curr = _ev(FP_B, summary="旧摘要", urls=(U1, U2))  # 新增佐证 → digest 变
    cs = diff_event_sets(prev, [curr])
    assert [e.fp for e in cs.changes] == [FP_B]
    assert cs.changes[0].kind == EventKind.change
    assert cs.adds == [] and cs.unchanged_fps == [] and cs.removed_fps == []
    # change 事件携带的是本期最新内容（下游 Writer 直接消费）
    assert set(cs.changes[0].evidence_urls) == {U1, U2}


def test_diff_remove_only_explicit_not_automatic():
    # prev 里有 A（本期也有）、D、X（本期都没有）；只显式撤 D → D removed，X 必须原样留记忆
    prev = build_snapshot([_ev(FP_A), _ev(FP_D), _ev(FP_X)], COMP, "2026-W34")
    curr = [_ev(FP_A)]
    cs = diff_event_sets(prev, curr, removed_fps=[FP_D])
    assert cs.removed_fps == [FP_D]
    assert FP_X not in cs.removed_fps and FP_A not in cs.removed_fps  # 上期有但本期没报道 ≠ 消失
    assert cs.unchanged_fps == [FP_A]


def test_diff_no_removed_param_removes_nothing():
    prev = build_snapshot([_ev(FP_D), _ev(FP_X)], COMP, "2026-W34")
    cs = diff_event_sets(prev, [_ev(FP_A)])  # 不传 removed_fps = 永不自动消失
    assert cs.removed_fps == []
    assert cs.adds == [e for e in cs.adds if e.fp == FP_A]


def test_mark_removed_unknown_fp_fails_fast():
    prev = build_snapshot([_ev(FP_A)], COMP, "2026-W34")
    with pytest.raises(ValueError):
        mark_removed(prev, ["deadbeef" * 2])


def test_diff_empty_prev_or_curr():
    empty = Snapshot(period="2026-W34", competitor_id=COMP)
    cs = diff_event_sets(empty, [_ev(FP_C)])
    assert [e.fp for e in cs.adds] == [FP_C]
    # curr 空：默认什么也不发生
    prev = build_snapshot([_ev(FP_A), _ev(FP_B)], COMP, "2026-W34")
    cs2 = diff_event_sets(prev, [])
    assert cs2.adds == [] and cs2.changes == [] and cs2.removed_fps == [] and cs2.unchanged_fps == []


def test_diff_summary_counts_and_total():
    prev = build_snapshot([_ev(FP_A, summary="S1"), _ev(FP_B, summary="S2"), _ev(FP_D)], COMP, "2026-W34")
    curr = [
        _ev(FP_A, summary="S1"),                       # unchanged
        _ev(FP_B, summary="S2", urls=(U1, U2)),        # change
        _ev(FP_C),                                     # add
    ]
    cs = diff_event_sets(prev, curr, removed_fps=[FP_D])
    s = cs.summary()
    assert (s.adds, s.changes, s.removes, s.unchanged) == (1, 1, 1, 1)
    assert s.total == 2  # 本期要下发的变更 = 新增 + 变更（消失不进周报正文）


def test_diff_output_is_deterministic():
    prev = build_snapshot([_ev(FP_B, summary="S2"), _ev(FP_A, summary="S1")], COMP, "2026-W34")
    curr = [_ev(FP_C), _ev(FP_B, summary="S2", urls=(U1, U2)), _ev(FP_A, summary="S1")]
    one = diff_event_sets(prev, curr)
    two = diff_event_sets(prev, curr)
    proj = lambda cs: ([e.fp for e in cs.adds], [e.fp for e in cs.changes], cs.unchanged_fps, cs.removed_fps)
    assert proj(one) == proj(two)
