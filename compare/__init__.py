# -*- coding: utf-8 -*-
"""跨竞品横向对比（增量「横向对比」落地）——M0 纯函数层。

把"一个 Task = 同周期 × 多竞品"里**各竞品已放行（published）的周报条目**，
按共享的 dimension 轴对齐，产出可离线、确定性回答的横向视图：
- 谁在本期动了哪些维度、谁没动（单边动作维度 = 事实，不说谁是盲点）；
- 同维度多竞品都动时，谁先动（比最早 first_seen，同日并列，无日期证据则不断言先后）；
- 同维度高威胁条数并列呈现（同轴可比；不跨竞品排"谁更危险"——severity 是按竞品语境打的）。

诚实边界（面试/文档必讲）：**维度对齐 ≠ 实体对齐**。dimension 是共享枚举（feature/price/…），
对齐只到维度轴；"A 的 v12 == B 的图表库 3.0"这类实体级等价需语义实体解析，属后续，本层不发明。
"""
from compare.builder import build
from compare.model import (
    CompetitorRadar,
    CompetitorView,
    CrossCompareReport,
    DimensionCell,
    DimensionRow,
    SingleMovedDim,
)

__all__ = [
    "build",
    "CompetitorView",
    "CrossCompareReport",
    "CompetitorRadar",
    "DimensionRow",
    "DimensionCell",
    "SingleMovedDim",
]
