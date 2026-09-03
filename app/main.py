# -*- coding: utf-8 -*-
"""应用入口（FastAPI）。Phase 0 /health 冒烟 → Phase 7 挂流水线路由（任务/收件箱/告警）。

- app.state.service：ServiceContext（settings + TaskStore + Notifier + Runner），routers 经依赖注入读取；
- schedule.enabled=true 时 lifespan 启动 APScheduler 每周巡检（单进程长跑服务定位）；默认关、离线可测。
版本对齐：0.1.0(Phase0) → 0.7.0(Phase7 服务化)。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from app.routers import alerts, inbox, tasks
from config.settings import get_settings
from service import build_scheduler, build_service_context

settings = get_settings()
_service_ctx = build_service_context(settings)


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler = None
    if _service_ctx.settings.schedule.enabled:
        scheduler = build_scheduler(_service_ctx)
        scheduler.start()
        print(f"[schedule] 每周巡检已启动：{_service_ctx.settings.schedule.cron}")
    yield
    if scheduler is not None:
        scheduler.shutdown(wait=False)


app = FastAPI(
    title="competition-watch-agent",
    description="竞观 Agent：竞品信号 MultiAgent 情报流水线（先审后发、可溯源、能答“本周变了什么”）。Phase 7 服务化。",
    version="0.7.0",
    lifespan=lifespan,
)
app.state.service = _service_ctx

app.include_router(tasks.router)
app.include_router(inbox.router)
app.include_router(alerts.router)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "app": settings.app.name,
        "docs": "/docs",
        "health": "/health",
        "tasks": "POST /tasks（跑一次本期巡检）· GET /tasks · GET /tasks/{id}/report",
        "inbox": "GET /inbox（人工收件箱：门卫转人工的条目）",
        "alerts": "GET /alerts · POST /alerts/hook（告警台账 + 手动投递演练）",
        "note": "Phase 7：任务管理 + APScheduler 定时 + 告警 webhook + 健壮性 + trace 计数链。",
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    mock_sources = [
        p.stem
        for p in (settings.repo_root / settings.sources.mock_dir).glob("*.json")
        if p.is_file()
    ]
    return {
        "status": "ok",
        "app": settings.app.name,
        "version": app.version,
        "llm_provider": settings.llm.provider,
        "sources_enabled": settings.sources.enabled,
        "mock_sources": sorted(mock_sources),
    }
