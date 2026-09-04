# -*- coding: utf-8 -*-
"""跨竞品横向对比·服务化接入（增量横向对比 M1）：Task 发布视图 → 横向对比报告。

compare_view(task_store, settings, task_id) 三步：
1. 调 service.publish.published_view，只取各 run **已放行（published）** 条目——先审后发纪律延续到
   对比层：被门卫转人工、仍挂起（held）或人工驳回（dismissed）的条目不进横向视图，
   不会拿"审后剔除的假货"去误导横向判断；
2. 过滤出有 ≥1 条已放行的 run → 映射成 compare.CompetitorView（条目逐字复用 ReportItem schema）；
3. 不足 2 家有已放行条目的竞品 → raise CompareNotPossible（路由映射 422 + reason）：
   单竞品/全挂起没有"横向"可言，宁可明说不比。

确定性：输出 = compare.build(发布视图) 的纯派生 JSON，不落盘、不带时间戳
（generated_at 留 None）→ 同状态同输入逐字节相等，HTTP 层可复测。
"""
from __future__ import annotations

from compare import build
from compare.model import CompetitorView
from service.publish import published_view
from writer import ReportItem


class CompareNotPossible(RuntimeError):
    """横向对比前提不满足（<2 家有已放行条目的竞品）。路由映射 422，detail=reason。"""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def compare_view(task_store, settings, task_id: str) -> dict:
    """一个 Task 的跨竞品横向对比（纯派生，无副作用）。task 不存在 → EntryNotFound（路由 404）。"""
    pv = published_view(task_store, settings, task_id)
    candidates = [r for r in pv["runs"] if r["published"]]
    if len(candidates) < 2:
        raise CompareNotPossible(
            f"横向对比需 ≥2 个有已放行条目的竞品 run（本 Task 实际 {len(candidates)} 家有已放行条目）——"
            "单竞品或全部挂起/剔除则无横向可比，先审后发后才见真章"
        )
    views = [
        CompetitorView(
            competitor_id=r["competitor_id"],
            run_id=r["run_id"],
            period=r["period"],
            items=[
                ReportItem(**{k: v for k, v in item.items() if k in ReportItem.model_fields})
                for item in r["published"]
            ],
        )
        for r in candidates
    ]
    rep = build(views)
    out = rep.model_dump(mode="json")
    out["task_id"] = task_id
    out["period"] = pv["period"]
    return out
