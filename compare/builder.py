# -*- coding: utf-8 -*-
"""跨竞品横向对比纯函数 build()：CompetitorView 清单 → CrossCompareReport。

确定性承诺（测试锁死）：
- 竞品按 competitor_id 规范化排序 → 输入乱序不影响输出；
- 行序 = Dimension 声明序（feature→price→market→org→compliance），只含真实出现的维度；
- 格内条目按 (first_seen, fp, title) 稳定排序；雷达/条数一律由 items 重算，不信任调用方。

派生语义（口径见 model.py 与 docs/compare.md）：
- moved_by / all_moved：事实覆盖；single_moved = 只有一家动（不称"盲点"）；
- first_mover：单边动作 → 独占即它（无时序断言）；多竞品同维度 → 比最早 first_seen，
  同日并列、有日期的参与排序；全无日期证据 → orderable=False、不给 first_mover（宁可不说）。
"""
from __future__ import annotations

from memory import Dimension, ThreatLevel
from writer import ReportItem

from compare.model import (
    CompetitorRadar,
    CompetitorView,
    CrossCompareReport,
    DimensionCell,
    DimensionRow,
    SingleMovedDim,
)

_SEVERITY_RANK: dict[ThreatLevel, int] = {
    ThreatLevel.high: 0,
    ThreatLevel.medium: 1,
    ThreatLevel.low: 2,
}


def _canonical(views: list[CompetitorView]) -> list[CompetitorView]:
    """入参防御：≥1 家、competitor_id 唯一；按 id 规范化排序（确定性底座）。"""
    if not views:
        raise ValueError("横向对比至少需要 1 个竞品视图（CompetitorView）")
    ids = [v.competitor_id for v in views]
    if len(set(ids)) != len(ids):
        raise ValueError(f"competitor_id 重复：{ids}——一次对比只收同周期不同竞品")
    return sorted(views, key=lambda v: v.competitor_id)


def _sort_items(items: list[ReportItem]) -> list[ReportItem]:
    """格内稳定排序：first_seen（ISO date 字典序即时间序）→ fp → title。"""
    return sorted(
        items,
        key=lambda it: (it.first_seen or "", it.fp or "", it.title),
    )


def _earliest(items: list[ReportItem]) -> str | None:
    dates = [it.first_seen for it in items if it.first_seen]
    return min(dates) if dates else None


def _max_severity(items: list[ReportItem]) -> ThreatLevel | None:
    ranks = [it.severity for it in items]
    if not ranks:
        return None
    return min(ranks, key=lambda s: _SEVERITY_RANK[s])


def _radar(items: list[ReportItem]) -> dict[str, int]:
    return {
        "high": sum(1 for it in items if it.severity is ThreatLevel.high),
        "medium": sum(1 for it in items if it.severity is ThreatLevel.medium),
        "low": sum(1 for it in items if it.severity is ThreatLevel.low),
    }


def build(views: list[CompetitorView]) -> CrossCompareReport:
    """把多竞品已放行条目对齐成横向对比报告（无副作用、确定性、可离线）。"""
    comps = _canonical(views)
    total = len(comps)

    # 列头：逐竞品雷达/维度覆盖（重算，不信任调用方）
    competitors: list[CompetitorRadar] = []
    for v in comps:
        items = list(v.items)
        radar = _radar(items)
        dims = [d for d in Dimension if any(it.dimension is d for it in items)]
        competitors.append(
            CompetitorRadar(
                competitor_id=v.competitor_id,
                run_id=v.run_id,
                headline_count=len(items),
                radar=radar,
                dims_moved=[d.value for d in dims],
            )
        )

    # 行：只含真实出现的维度，按 Dimension 声明序
    rows: list[DimensionRow] = []
    present_dims = {it.dimension for v in comps for it in v.items}
    for dim in Dimension:
        if dim not in present_dims:
            continue
        moved = [
            v
            for v in comps
            if any(it.dimension is dim for it in v.items)
        ]
        cells: list[DimensionCell] = []
        for v in moved:  # moved 承 _canonical 顺序 → competitor_id 升序
            cell_items = _sort_items([it for it in v.items if it.dimension is dim])
            cells.append(
                DimensionCell(
                    competitor_id=v.competitor_id,
                    item_count=len(cell_items),
                    earliest=_earliest(cell_items),
                    max_severity=_max_severity(cell_items),
                    high_count=sum(1 for it in cell_items if it.severity is ThreatLevel.high),
                    items=cell_items,
                )
            )
        # first_mover：单边动作 → 有可排序日期才算"该竞品先行"（无日期断言不出 first_mover，
        # 否则给了第一名却没给时序依据，横向对比表会"自说自话"）；多竞品 → 最早 first_seen，并列可多条
        if len(moved) == 1:
            sole = moved[0]
            orderable = cells[0].earliest is not None
            first_mover = [sole.competitor_id] if orderable else []
        else:
            dated = [c for c in cells if c.earliest is not None]
            orderable = len(dated) > 0
            if orderable:
                min_date = min(c.earliest for c in dated)  # type: ignore[arg-type]
                first_mover = sorted(c.competitor_id for c in dated if c.earliest == min_date)
            else:
                first_mover = []
        rows.append(
            DimensionRow(
                dimension=dim,
                moved_by=[v.competitor_id for v in moved],
                all_moved=len(moved) == total,
                orderable=orderable,
                first_mover=first_mover,
                cells=cells,
            )
        )

    # derived 汇总
    both_moved_dims = [r.dimension.value for r in rows if total >= 2 and r.all_moved]
    single_moved_dims = [
        SingleMovedDim(dimension=r.dimension, mover=r.moved_by[0])
        for r in rows
        if len(r.moved_by) == 1
    ]
    first_move_counts: dict[str, int] = {}
    for r in rows:
        for cid in r.first_mover:
            first_move_counts[cid] = first_move_counts.get(cid, 0) + 1

    return CrossCompareReport(
        competitors=competitors,
        rows=rows,
        both_moved_dims=both_moved_dims,
        single_moved_dims=single_moved_dims,
        first_move_counts=first_move_counts,
    )
