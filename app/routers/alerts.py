# -*- coding: utf-8 -*-
"""告警路由（Phase 7，README2 §5.9）：台账查询 + 手动投递演练。

- GET /alerts：读 data/service/alerts.json 台账（MockNotifier 写；含真实 W35 跑出的 high_threat）；
- POST /alerts/hook：手动触发一次告警投递（demo/联调用，body 指定事件类型与摘要）→ 返回投递回执。
  默认 notifier = Mock 落台账；若配置了 WebhookNotifier（真 HTTP 推送占位）投递会抛 NotImplemented，
  服务端捕获后返回 501，不假装已发。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.routers.deps import get_service_ctx
from service.context import ServiceContext
from service.notifier import make_alert

router = APIRouter(tags=["alerts"])

_ALLOWED_TYPES = ("high_threat", "human_inbox", "run_failed")


class HookRequest(BaseModel):
    event_type: str = Field(..., description="high_threat | human_inbox | run_failed")
    summary: str = Field(..., description="告警一句话摘要")
    competitor_id: str | None = None
    period: str | None = None
    run_id: str | None = None
    payload: dict = Field(default_factory=dict)


@router.get("/alerts")
def list_alerts(ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    alerts = ctx.list_alerts()
    # 台账按追加序存，展示倒序（最新在前）
    return {"count": len(alerts), "alerts": list(reversed(alerts))}


@router.post("/alerts/hook")
def hook_alert(req: HookRequest, ctx: ServiceContext = Depends(get_service_ctx)) -> dict:
    if req.event_type not in _ALLOWED_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"event_type 必须是 {list(_ALLOWED_TYPES)} 之一，收到 {req.event_type!r}",
        )
    event = make_alert(
        req.event_type,
        summary=req.summary,
        competitor_id=req.competitor_id,
        period=req.period,
        run_id=req.run_id,
        payload=req.payload,
    )
    try:
        receipt = ctx.notifier.deliver(event)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=f"Webhook 占位未接真 HTTP：{exc}")
    return {"accepted": True, **receipt}
