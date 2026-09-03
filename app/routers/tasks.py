# -*- coding: utf-8 -*-
"""任务管理路由（Phase 7，README2 §5.10）：POST /tasks 跑一次"本期巡检"，GET /tasks 查列表与详情。

- POST /tasks：建 Task 并**同步执行**（period 缺省 = 最近已结束 ISO 周；competitor 缺省 = 全部竞品，
  传 competitor_id 可只跑单竞品调试）→ 返回终态（done/partial/failed + 计数链 + 门卫判定）。
- GET /tasks / GET /tasks/{id}：TaskStore 文件即状态直接读；404 = 任务不存在。
- GET /tasks/{id}/report：把该 Task 各 done run 的全量周报 draft（headline/sections/items/sources）
  从 data/pipeline/{run_id}.json 合并回来（TaskStore 只存元数据索引，全量 state 在 pipeline 层）。
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.routers.deps import get_service_ctx
from service.context import ServiceContext
from service.runner import available_competitors
from service.schemas import RunStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskRequest(BaseModel):
    period: str | None = Field(None, description='周期 "YYYY-Www"；缺省 = 最近已结束 ISO 周')
    competitor_id: str | None = Field(None, description="只跑单竞品（调试用）；缺省 = 全部可用竞品")


@router.post("", status_code=201)
def create_task(req: TaskRequest, ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    competitors = None
    if req.competitor_id is not None:
        if req.competitor_id not in available_competitors(ctx.settings):
            raise HTTPException(
                status_code=404,
                detail=f"未知竞品 {req.competitor_id}（可用：{available_competitors(ctx.settings)}）",
            )
        competitors = [req.competitor_id]
    meta = ctx.runner.execute_task(uuid.uuid4().hex[:12], period=req.period, competitors=competitors)
    return meta.as_dict()


@router.get("")
def list_tasks(ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    metas = ctx.task_store.list_tasks()
    return {
        "count": len(metas),
        "tasks": [m.as_dict() for m in metas],
    }


@router.get("/{task_id}")
def get_task(task_id: str, ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    meta = ctx.task_store.get_task(task_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} 不存在")
    return meta.as_dict()


@router.get("/{task_id}/report")
def get_task_report(task_id: str, ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    """该 Task 的合规周报：逐 done run 从 pipeline json 合并 draft（周报只来自已跑成功的 run）。"""
    meta = ctx.task_store.get_task(task_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} 不存在")

    comps = []
    for run in meta.runs:
        if run.status != RunStatus.done:
            comps.append(
                {
                    "competitor_id": run.competitor_id,
                    "status": run.status.value,
                    "note": "该 run 未成功，无周报",
                }
            )
            continue
        pipeline = ctx.settings.data_path / "pipeline" / f"{run.run_id}.json"
        draft = None
        if pipeline.exists():
            final = json.loads(pipeline.read_text(encoding="utf-8"))
            draft = final.get("draft")
        comps.append(
            {
                "competitor_id": run.competitor_id,
                "run_id": run.run_id,
                "status": run.status.value,
                "verdict": run.verdict,
                "threat_radar": run.threat_radar,
                "trace": run.trace,
                "draft": draft,  # headline + sections + items + sources
                "inbox_count": run.inbox_count,
            }
        )
    return {
        "task_id": task_id,
        "period": meta.period,
        "status": meta.status.value,
        "report": comps,
    }
