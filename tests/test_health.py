# -*- coding: utf-8 -*-
"""/health 冒烟：Phase 0 骨架可启动、配置接通、Mock 数据在位。"""
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_ok():
    r = client.get("/")
    assert r.status_code == 200
    assert r.json()["app"] == "competition-watch-agent"


def test_health_reports_provider_and_sources():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["llm_provider"] == "mock"  # 默认离线
    assert set(body["sources_enabled"]) == {"website", "news", "rss"}
    # fixtures 里两个 Mock 竞品必须在位（离线回放底座）
    assert {"crm_alpha", "bi_beta"} <= set(body["mock_sources"])
