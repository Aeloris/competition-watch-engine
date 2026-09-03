# -*- coding: utf-8 -*-
"""应用入口（FastAPI）。Phase 0 只提供 /health 冒烟与骨架说明；流水线端点 Phase 7 起挂 routers。"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI

from config.settings import get_settings

settings = get_settings()

app = FastAPI(
    title="competition-watch-agent",
    description="竞观 Agent：竞品信号 MultiAgent 情报流水线（先审后发、可溯源、能答“本周变了什么”）。Phase 0 骨架。",
    version="0.1.0",
)


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "app": settings.app.name,
        "docs": "/docs",
        "health": "/health",
        "note": "Phase 0 骨架：/health 冒烟；完整流水线 API Phase 7 提供。",
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
