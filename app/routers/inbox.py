# -*- coding: utf-8 -*-
"""人工收件箱路由（Phase 7 只读 → Phase 8 可闭环）：把 P6 落进每 run state 的 human_inbox
汇总成可查可处理的清单 + 人工定案（放行/驳回/备注）。

口径（逻辑全在 service/publish，HTTP 层只翻译状态码）：
- GET /inbox?status=pending|all|resolved（缺省 pending = P7 语义，只看未处理）；
  只有门卫 verdict=human 的 run 才进收件箱（PASS 的 run 即使有 high 也不在此列）；
  每条含稳定 entry_id = `{run_id}#{seq}`，resolution 由 service 推导 status。
- POST /inbox/resolve：{entry_id, action: release|dismiss|note, by, note?} →
  200 = 定案后的条目视图；404 = 条目不存在；409 = 已 release/dismiss 终态（不许覆盖审计决定）；
  422 = action 非法 / by 为空 / entry_id 格式错（pydantic Literal/min_length 拦大半）。
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.routers.deps import get_service_ctx
from service.context import ServiceContext
from service.publish import (
    ALL_ACTIONS,
    EntryAlreadyResolved,
    EntryNotFound,
    list_inbox_entries,
    parse_entry_id,
    resolve_inbox_entry,
)

router = APIRouter(tags=["inbox"])


class ResolveRequest(BaseModel):
    entry_id: str = Field(..., description="收件箱条目 id：`{run_id}#{seq}`（GET /inbox 返回）")
    action: Literal["release", "dismiss", "note"] = Field(
        ..., description="release=放行(进发布视图) | dismiss=驳回(剔除) | note=仅备注(不销案)")
    by: str = Field(..., min_length=1, description="处理人（可追责）")
    note: str = Field("", description="批注/说明（可选）")

    @field_validator("by")
    @classmethod
    def _by_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("by 不能为空白（处理人需可追责）")
        return v


@router.get("/inbox")
def list_inbox(
    status: Literal["pending", "all", "resolved"] = "pending",
    ctx: ServiceContext = Depends(get_service_ctx),
) -> dict:
    entries = list_inbox_entries(ctx.task_store, status=status)
    return {"count": len(entries), "status_filter": status, "entries": entries}


@router.post("/inbox/resolve", status_code=200)
def resolve(req: ResolveRequest, ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    # 先解析 entry_id：格式错 → 422（与 pydantic 校验失败同一状态码，便于客户端统一处理）
    try:
        parse_entry_id(req.entry_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    try:
        view = resolve_inbox_entry(
            ctx.task_store, req.entry_id, action=req.action, by=req.by, note=req.note,
        )
    except EntryNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except EntryAlreadyResolved as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        # pydantic 已拦空白 by/非法 action，此为服务层兜底（别让校验漏洞变 500）
        raise HTTPException(status_code=422, detail=str(exc))
    return {"resolved": True, "entry": view}
