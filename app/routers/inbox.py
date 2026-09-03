# -*- coding: utf-8 -*-
"""人工收件箱路由（Phase 7）：把 P6 落进每 run state 的 human_inbox 汇总成可查的清单。

口径：只有门卫 verdict=human 的 run 才进收件箱（PASS 的 run 即使有 high 也不在此列）；
每条 = {task_id, run_id, competitor_id, period, status:"pending", code/detail/fp/attempts…}。
resolution（人工确认放行/关闭）留 Phase 8 面板，本期只读"有待处理"。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.routers.deps import get_service_ctx
from service.context import ServiceContext

router = APIRouter(tags=["inbox"])


@router.get("/inbox")
def list_inbox(ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    entries = []
    for task in ctx.task_store.list_tasks():  # 已按时间倒序
        for run in task.runs:
            if run.verdict != "human":
                continue
            for entry in run.human_inbox:
                entries.append(
                    {
                        "task_id": task.task_id,
                        "period": task.period,
                        "run_id": run.run_id,
                        "competitor_id": run.competitor_id,
                        "status": "pending",
                        **entry,
                    }
                )
    return {"count": len(entries), "entries": entries}
