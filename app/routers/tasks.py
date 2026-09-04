# -*- coding: utf-8 -*-
"""任务管理路由（Phase 7，README2 §5.10）：POST /tasks 跑一次"本期巡检"，GET /tasks 查列表与详情。

- POST /tasks：建 Task 并**同步执行**（period 缺省 = 最近已结束 ISO 周；competitor 缺省 = 全部竞品，
  传 competitor_id 可只跑单竞品调试）→ 返回终态（done/partial/failed + 计数链 + 门卫判定）。
- GET /tasks / GET /tasks/{id}：TaskStore 文件即状态直接读；404 = 任务不存在。
- GET /tasks/{id}/report：把该 Task 各 done run 的全量周报 draft（headline/sections/items/sources）
  从 data/pipeline/{run_id}.json 合并回来（TaskStore 只存元数据索引，全量 state 在 pipeline 层）。
- GET /tasks/{id}/published（Phase 8）：发布视图——把 draft 按人工 resolution 过滤成
  published / held / dismissed（先审后发闭环的可复核快照；逻辑在 service/publish）。
- GET /tasks/{id}/compare（增量横向对比）：跨竞品横向对比——消费发布视图，把 ≥2 家有已放行
  条目的 run 按 dimension 轴对齐（维度对齐 ≠ 实体对齐，口径见 docs/compare.md）；
  不足两家 422 + reason（单竞品/全挂起无可比）。
"""
from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.routers.deps import get_service_ctx
from orchestration.planner import period_start
from service.compare import CompareNotPossible, compare_view
from service.context import ServiceContext
from service.publish import EntryNotFound, published_view
from service.runner import available_competitors
from service.schemas import RunStatus

router = APIRouter(prefix="/tasks", tags=["tasks"])


class TaskRequest(BaseModel):
    period: str | None = Field(None, description='周期 "YYYY-Www"；缺省 = 最近已结束 ISO 周')
    competitor_id: str | None = Field(None, description="只跑单竞品（调试用）；缺省 = 全部可用竞品")

    @field_validator("period")
    @classmethod
    def _period_must_be_valid_iso_week(cls, v: str | None) -> str | None:
        # 请求期就校验：格式对但周号越界（如 2026-W99）会让每个 run 在 period_start 里 ValueError
        # → 全 run failed + 201"注定失败"的任务。这里提前 422（orchestration.planner.period_start
        # 的 ValueError 被 pydantic 转 422），客户端拿到的是格式错误而非后端内部异常。
        if v is not None:
            period_start(v)
        return v


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
        draft_broken = False
        if pipeline.exists():
            try:
                final = json.loads(pipeline.read_text(encoding="utf-8"))
                draft = final.get("draft")
            except (ValueError, OSError):
                # 半截/损坏 pipeline 文件：draft 置空并如实标注，别让单条坏文件把整页 report 打成 500
                draft_broken = True
        comps.append(
            {
                "competitor_id": run.competitor_id,
                "run_id": run.run_id,
                "status": run.status.value,
                "verdict": run.verdict,
                "threat_radar": run.threat_radar,
                "trace": run.trace,
                "draft": draft,  # headline + sections + items + sources
                "draft_broken": draft_broken,
                "note": "pipeline 文件损坏，draft 缺失" if draft_broken else None,
                "inbox_count": run.inbox_count,
            }
        )
    return {
        "task_id": task_id,
        "period": meta.period,
        "status": meta.status.value,
        "report": comps,
    }


@router.get("/{task_id}/published")
def get_task_published(task_id: str, ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    """发布视图（Phase 8）：人工放行闭环后的可发布周报 + 挂起/剔除审计区（service/publish.published_view）。"""
    try:
        return published_view(ctx.task_store, ctx.settings, task_id)
    except EntryNotFound:
        raise HTTPException(status_code=404, detail=f"task {task_id} 不存在")


@router.get("/{task_id}/compare")
def get_task_compare(task_id: str, ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    """跨竞品横向对比（增量）：≥2 家有已放行条目才可比（422+原因），task 不存在 404。"""
    try:
        return compare_view(ctx.task_store, ctx.settings, task_id)
    except EntryNotFound:
        raise HTTPException(status_code=404, detail=f"task {task_id} 不存在")
    except CompareNotPossible as e:
        raise HTTPException(status_code=422, detail=e.reason)
