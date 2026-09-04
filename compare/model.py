# -*- coding: utf-8 -*-
"""跨竞品横向对比数据模型（增量 M0）：pydantic 强制输出结构。

输入（CompetitorView）= 单个竞品本期**已放行**的周报条目（published items）；
输出（CrossCompareReport）= 维度轴对齐的横向视图。消费方：M1 service.compare_view
把 Task 的发布视图映射成 CompetitorView 清单再 build；本模型不落盘、无副作用。

诚实边界与口径（与 README2 不一致时以此为准并写进 docs/compare.md）：
- **维度对齐 ≠ 实体对齐**：dimension 是共享枚举，行 = dimension；不做"A 的 v12 对应 B 的图表库 3.0"
  这类实体级等价（那属语义实体解析/后续）。first_mover 指"该维度本期动作时间先后"，不宣称同一竞赛。
- **并列呈现不排序危险**：同维度高威胁条数/雷达只并列计数；severity 是各竞品语境下 Analyst 打的，
  跨竞品直接判"谁更危险"= 过度解读，因此本模型不产出全局"最危险竞品"。
- 严重度顺序仅用于**单竞品内**的 max_severity（high > medium > low），是事实汇总，非跨竞品比较。
- first_seen 全缺（无日期证据）时不断言先后：orderable=False、first_mover 为空，宁可不说。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from memory import Dimension, ThreatLevel
from writer import ReportItem


class CompetitorView(BaseModel):
    """一次横向对比的输入视图：一个竞品本期已放行条目清单（M1 由发布视图映射而来）。"""

    competitor_id: str = Field(..., min_length=1)
    run_id: str | None = None
    period: str | None = None
    items: list[ReportItem] = Field(default_factory=list)


class CompetitorRadar(BaseModel):
    """输出里的竞品列头：本期雷达（由 items 重算，不信任调用方）+ 动了哪些维度。"""

    competitor_id: str
    run_id: str | None = None
    headline_count: int = 0
    radar: dict[str, int] = Field(default_factory=dict)  # {high,medium,low}
    dims_moved: list[str] = Field(default_factory=list)  # 按 Dimension 声明序


class DimensionCell(BaseModel):
    """某 dimension 行 × 某竞品 的单元格：该竞品在本维度的动作事实汇总。"""

    competitor_id: str
    item_count: int
    earliest: str | None = None     # 该格条目最早 first_seen（ISO date；全缺为 None）
    max_severity: ThreatLevel | None = None  # 单竞品内最高（high>medium>low）
    high_count: int = 0            # 该格 high 威胁条数（供"谁 high 更多"并列看，不排序）
    items: list[ReportItem] = Field(default_factory=list)  # 排序确定：(first_seen, fp, title)


class DimensionRow(BaseModel):
    """一条 dimension 轴行：谁动了、谁先动（并列可多条）、逐竞品格。"""

    dimension: Dimension
    moved_by: list[str] = Field(default_factory=list)  # 本期在该维度有发布条目的竞品（排序确定）
    all_moved: bool = False         # moved_by 覆盖全部输入竞品
    orderable: bool = False         # 有 first_seen 日期证据才排先后
    first_mover: list[str] = Field(default_factory=list)  # 最早 first_seen 的竞品；同日并列、独占=仅它
    cells: list[DimensionCell] = Field(default_factory=list)


class SingleMovedDim(BaseModel):
    """derived：只有一家动的维度（事实陈述，不称对方"盲点"）。"""

    dimension: Dimension
    mover: str


class CrossCompareReport(BaseModel):
    """横向对比报告：competitors 列头 + 维度行 + derived 汇总。行序 = Dimension 声明序（确定）。"""

    task_id: str | None = None
    period: str | None = None
    generated_at: str | None = None
    competitors: list[CompetitorRadar] = Field(default_factory=list)   # 按 competitor_id 排序
    rows: list[DimensionRow] = Field(default_factory=list)
    both_moved_dims: list[str] = Field(default_factory=list)           # 全员动作维度名（覆盖全部竞品）
    single_moved_dims: list[SingleMovedDim] = Field(default_factory=list)
    first_move_counts: dict[str, int] = Field(default_factory=dict)    # 率先(含独占)动作维度数
