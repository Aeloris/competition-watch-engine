# -*- coding: utf-8 -*-
"""FastAPI 依赖注入：从 request.app.state 拿 ServiceContext。

main 启动时挂 app.state.service = build_service_context()；
测试用 app.dependency_overrides[get_service_ctx] = lambda: 临时 ctx（data_dir 指向 tmp，全离线确定性）。
"""
from __future__ import annotations

from fastapi import Request

from service.context import ServiceContext


def get_service_ctx(request: Request) -> ServiceContext:
    return request.app.state.service
