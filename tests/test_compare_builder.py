# -*- coding: utf-8 -*-
"""增量「跨竞品横向对比」M0：compare.builder.build 纯函数性质。

锁的性质（面试可复述"横向对比为什么不搞语义实体解析也能讲真话"）：
1. **确定性**：竞品输入乱序 / 两次 build → 输出字节级一致（competitor_id 规范化 + 稳定排序）；
2. **维度轴对齐 ≠ 实体对齐**：行 = 共享 dimension 枚举，first_mover 只比该维度动作时间先后，
   无 first_seen 日期证据时宁可不说（orderable=False、first_mover 空），绝不编一个"先动"；
3. **并列呈现不排序危险**：高威胁条数/雷达并列计数；max_severity 只在单竞品格内（事实汇总）；
4. **边界诚实**：无条目的竞品在 rows 里消失、仍在列头雷达里（0）；单竞品不报错但不宣称横向。
"""
import pytest

from compare import build, CompetitorView
from memory import Dimension, EventKind, ThreatLevel
from writer import ReportItem


def _item(
    dimension: Dimension,
    first_seen: str | None,
    severity: ThreatLevel = ThreatLevel.medium,
    title: str = "某条变更",
    fp: str | None = None,
) -> ReportItem:
    return ReportItem(
        fp=fp,
        kind=EventKind.add,
        dimension=dimension,
        severity=severity,
        reasons=["关键词规则给出分级"],
        title=title,
        evidence_urls=["https://example.com/source"],
        first_seen=first_seen,
    )


def _view(cid: str, items: list[ReportItem]) -> CompetitorView:
    return CompetitorView(competitor_id=cid, run_id=f"run-{cid}", items=items)


def _dump(r):
    return r.model_dump(mode="json")


# --- 1) 同维度双竞品 → 谁先动（比最早 first_seen）---
def test_row_same_dimension_first_mover_by_earliest():
    crm = _view("crm_alpha", [_item(Dimension.feature, "2026-08-25", ThreatLevel.medium, "v12 GA")])
    bi = _view("bi_beta", [_item(Dimension.feature, "2026-08-24", ThreatLevel.high, "图表库 3.0")])
    rep = build([crm, bi])

    assert [r.dimension for r in rep.rows] == [Dimension.feature]
    row = rep.rows[0]
    assert row.moved_by == ["bi_beta", "crm_alpha"]          # 规范化顺序
    assert row.all_moved is True and row.orderable is True
    assert row.first_mover == ["bi_beta"]                     # bi 08-24 早于 crm 08-25
    assert rep.both_moved_dims == ["feature"]
    assert rep.single_moved_dims == []
    assert rep.first_move_counts == {"bi_beta": 1}


# --- 2) 各动各的维度 → 单边动作（事实，不称盲点）+ 行按枚举序 ---
def test_single_moved_dimensions_sorted_rows():
    crm = _view("crm_alpha", [_item(Dimension.price, "2026-08-26", ThreatLevel.high, "订阅涨价")])
    bi = _view("bi_beta", [_item(Dimension.feature, "2026-08-24", ThreatLevel.low, "看板更新")])
    rep = build([crm, bi])

    assert [r.dimension for r in rep.rows] == [Dimension.feature, Dimension.price]  # 枚举声明序 feature→price
    assert rep.both_moved_dims == []
    got = {(s.dimension, s.mover) for s in rep.single_moved_dims}
    assert got == {(Dimension.price, "crm_alpha"), (Dimension.feature, "bi_beta")}
    assert rep.first_move_counts == {"crm_alpha": 1, "bi_beta": 1}  # 独占动作也计"率先"


# --- 3) 同日并列 → first_mover 双写、排序确定 ---
def test_tie_same_date_parallel_first_mover():
    crm = _view("crm_alpha", [_item(Dimension.feature, "2026-08-25")])
    bi = _view("bi_beta", [_item(Dimension.feature, "2026-08-25")])
    rep = build([crm, bi])
    assert rep.rows[0].first_mover == ["bi_beta", "crm_alpha"]  # 都最早 → 并列
    assert rep.first_move_counts == {"bi_beta": 1, "crm_alpha": 1}


# --- 4) 无日期证据 → 不断言先后（宁可不说，不编"先动"）---
def test_no_date_evidence_no_fabricated_order():
    crm = _view("crm_alpha", [_item(Dimension.feature, None)])
    bi = _view("bi_beta", [_item(Dimension.feature, None)])
    rep = build([crm, bi])
    row = rep.rows[0]
    assert row.orderable is False
    assert row.first_mover == []
    assert rep.first_move_counts == {}


# --- 5) 确定性：输入乱序 & 重复 build → 字节级一致 ---
def test_deterministic_input_order_and_rerun():
    crm_items = [
        _item(Dimension.feature, "2026-08-25", ThreatLevel.high, "v12 GA"),
        _item(Dimension.market, "2026-08-27", ThreatLevel.low, "渠道活动"),
    ]
    bi_items = [_item(Dimension.feature, "2026-08-24", ThreatLevel.medium, "看板更新")]
    rep_a = build([_view("crm_alpha", crm_items), _view("bi_beta", bi_items)])
    rep_b = build([_view("bi_beta", bi_items), _view("crm_alpha", crm_items)])  # 乱序输入
    assert _dump(rep_a) == _dump(rep_b)
    assert _dump(rep_a) == _dump(build([_view("crm_alpha", crm_items), _view("bi_beta", bi_items)]))
    assert _dump(rep_a) == _dump(rep_a)  # 同对象重复 dump 恒等


# --- 6) 空条目竞品：不出现在行里、仍在列头雷达（0）---
def test_empty_competitor_stays_in_header_radar_not_rows():
    crm = _view("crm_alpha", [_item(Dimension.feature, "2026-08-25", ThreatLevel.high)])
    bi = _view("bi_beta", [_item(Dimension.feature, "2026-08-24", ThreatLevel.medium)])
    third = _view("crm_legacy", [])  # 本期无发布变更
    rep = build([crm, third, bi])

    by_id = {c.competitor_id: c for c in rep.competitors}
    assert by_id["crm_legacy"].headline_count == 0
    assert by_id["crm_legacy"].radar == {"high": 0, "medium": 0, "low": 0}
    assert by_id["crm_legacy"].dims_moved == []
    row = rep.rows[0]
    assert row.moved_by == ["bi_beta", "crm_alpha"]   # 空竞品不占 moved
    assert row.all_moved is False                      # 未覆盖全部输入竞品
    assert rep.single_moved_dims == []


# --- 7) 格内事实汇总：high 计数 / max_severity 只在单竞品格内 ---
def test_cell_severity_summary_is_per_competitor():
    crm = _view(
        "crm_alpha",
        [_item(Dimension.feature, "2026-08-25", ThreatLevel.medium, "v12 GA")],
    )
    bi = _view(
        "bi_beta",
        [
            _item(Dimension.feature, "2026-08-24", ThreatLevel.high, "图表 3.0"),
            _item(Dimension.feature, "2026-08-26", ThreatLevel.low, "移动端适配"),
        ],
    )
    rep = build([crm, bi])
    cells = {c.competitor_id: c for c in rep.rows[0].cells}
    assert cells["bi_beta"].item_count == 2
    assert cells["bi_beta"].high_count == 1
    assert cells["bi_beta"].max_severity is ThreatLevel.high
    assert cells["bi_beta"].earliest == "2026-08-24"
    assert cells["crm_alpha"].max_severity is ThreatLevel.medium
    assert cells["crm_alpha"].earliest == "2026-08-25"
    # 雷达由 items 重算（列头竞品逐家）
    radar_by = {c.competitor_id: c.radar for c in rep.competitors}
    assert radar_by["bi_beta"] == {"high": 1, "medium": 0, "low": 1}
    assert radar_by["crm_alpha"] == {"high": 0, "medium": 1, "low": 0}


# --- 8) 入参防御：空清单 / 竞品重复 ---
def test_guards_empty_and_duplicate():
    with pytest.raises(ValueError):
        build([])
    with pytest.raises(ValueError):
        build([_view("crm_alpha", []), _view("crm_alpha", [_item(Dimension.feature, "2026-08-25")])])


# --- 9) 单竞品合法：不报错、不宣称横向 ---
def test_single_competitor_builds_but_no_cross_claims():
    rep = build([_view("crm_alpha", [_item(Dimension.feature, "2026-08-25", ThreatLevel.high)])])
    assert len(rep.rows) == 1
    assert rep.rows[0].first_mover == ["crm_alpha"]       # 独占即它
    assert rep.both_moved_dims == []                       # 无"横向"可言
    assert [(s.dimension, s.mover) for s in rep.single_moved_dims] == [(Dimension.feature, "crm_alpha")]
    assert rep.first_move_counts == {"crm_alpha": 1}
