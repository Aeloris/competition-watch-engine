# -*- coding: utf-8 -*-
"""Phase 7 服务化 HTTP 层（README2 §5.10）：任务管理 / 收件箱 / 告警路由端到端。

验收口径（README2 §7.4 row 7"接口全套"）：
- POST /tasks：跑一次"本期巡检"→ 201 + 终态（计数链 + 门卫判定）；未知竞品 404；
  GET /tasks（倒序列表）/ GET /tasks/{id}（404 不存在）/ GET /tasks/{id}/report（合并全量周报 draft）；
- GET /inbox：只有门卫 verdict=human 的 run 才进人工收件箱（status:"pending"，Phase 8 面板消费）；
- GET /alerts / POST /alerts/hook：台账倒序展示；手动投递演练 accepted 回执；非法 event_type 422。

隔离：测试把 data_dir 指到 tmp，并用 dependency_overrides 替换 get_service_ctx → 不碰开发 data/，
全部离线确定性；trace/radar 数字沿用 test_service_runtime 实测口径（crm W35 5→3→3）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.deps import get_service_ctx
from orchestration import graph as graph_mod
from reviewer import UNSOURCED_URL
from service import build_service_context

V12_FP = "5c60e045b8853e63"  # crm W35 真 fp（用于"真 fp 拼编造 URL"注入）


def _item(title: str = "T", *, fp: str = "fp-1", urls=("https://a.example/x",)) -> dict:
    return {"fp": fp, "kind": "add", "dimension": "feature", "severity": "medium",
            "reasons": ["规则[feature_update]：常规迭代"], "title": title, "summary": "",
            "evidence_urls": list(urls)}


@pytest.fixture
def client(tmp_path):
    """HTTP 层隔离 ctx：data_dir 指向 tmp + dependency_overrides 掉全局 ctx。"""
    ctx = build_service_context(data_dir=str(tmp_path))
    app.dependency_overrides[get_service_ctx] = lambda: ctx
    try:
        with TestClient(app) as c:  # lifespan：schedule.enabled=False → 不启调度器
            c.ctx = ctx
            yield c
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- 任务管理

def test_post_task_returns_terminal_meta_with_chain(client):
    """POST /tasks（单竞品 crm W35）→ 201 终态：completed + done + PASS + 计数链 5→3→3 + 雷达。"""
    res = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    assert res.status_code == 201
    body = res.json()
    assert body["status"] == "completed" and body["period"] == "2026-W35"
    run = body["runs"][0]
    assert run["competitor_id"] == "crm_alpha" and run["status"] == "done" and run["verdict"] == "PASS"
    assert run["trace"] == {"collected": 5, "merged": 3, "diff": 3}
    assert run["threat_radar"] == {"high": 1, "medium": 1, "low": 1}
    assert body["summary"]["chains"][0]["trace"]["diff"] == 3   # 计数链已聚合进 summary


def test_get_tasks_list_detail_and_report(client):
    """GET /tasks（列表）→ GET /tasks/{id}（详情）→ GET /tasks/{id}/report（回读 pipeline 全量 draft）。"""
    post = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    task_id = post.json()["task_id"]
    assert post.status_code == 201

    lst = client.get("/tasks").json()
    assert lst["count"] >= 1 and lst["tasks"][0]["task_id"] == task_id  # 倒序：最新在前

    detail = client.get(f"/tasks/{task_id}").json()
    assert detail["status"] == "completed" and detail["runs"][0]["verdict"] == "PASS"

    rep = client.get(f"/tasks/{task_id}/report").json()
    assert rep["task_id"] == task_id
    comp = rep["report"][0]
    assert comp["competitor_id"] == "crm_alpha" and comp["status"] == "done"
    assert comp["trace"] == {"collected": 5, "merged": 3, "diff": 3}
    assert comp["draft"] is not None                       # 全量周报（headline/items/…）从 pipeline json 合并回来
    assert len(comp["draft"].get("items") or []) >= 1
    assert comp["inbox_count"] == 0


def test_post_unknown_competitor_404_and_unknown_task_404(client):
    res = client.post("/tasks", json={"competitor_id": "nope"})
    assert res.status_code == 404 and "未知竞品" in res.json()["detail"]
    assert client.get("/tasks/nope").status_code == 404
    assert client.get("/tasks/nope/report").status_code == 404


def test_report_handles_corrupt_pipeline_file_not_500(client):
    """A8 回归：pipeline JSON 半截/损坏时 report 路由不能 500——如实标注 draft 缺失(draft_broken)，
    一条坏文件不把整页 report 打挂（同 list_tasks 容忍损坏文件的读取哲学）。"""
    post = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    body = post.json()
    run = body["runs"][0]
    pipeline = client.ctx.settings.data_path / "pipeline" / f"{run['run_id']}.json"
    assert pipeline.exists()
    pipeline.write_text("{corrupt json, not valid", encoding="utf-8")
    rep = client.get(f"/tasks/{body['task_id']}/report")
    assert rep.status_code == 200
    comp = rep.json()["report"][0]
    assert comp["draft"] is None and comp["draft_broken"] is True


# ---------------------------------------------------------------- 收件箱

def test_inbox_shows_pending_after_human_run(client, monkeypatch):
    """注入假 writer → run 门卫 human → GET /inbox 出现 pending 条目（先审后发在 HTTP 层可见）。"""
    def fake_writer(state: dict, ctx=None) -> dict:
        return {"draft": {"competitor_id": state["competitor_id"], "period": state["period"],
                          "headline_count": 1, "sections": {"adds": 1, "changes": 0, "removes": 0},
                          "items": [_item(title="真事件换了个编造出处", fp=V12_FP,
                                          urls=("https://fake.example/x",))]}}

    monkeypatch.setitem(graph_mod.NODE_IMPLS, "writer", fake_writer)
    res = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    body = res.json()
    assert body["runs"][0]["verdict"] == "human" and body["runs"][0]["inbox_count"] == 1

    inbox = client.get("/inbox").json()
    assert inbox["count"] >= 1
    entry = inbox["entries"][0]
    assert entry["competitor_id"] == "crm_alpha" and entry["status"] == "pending"
    assert entry["code"] == UNSOURCED_URL


def test_inbox_empty_when_all_passed(client):
    """全 PASS 的真实语料：收件箱为空（真语料 0 误拦延伸到 HTTP 层）。"""
    client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    inbox = client.get("/inbox").json()
    assert inbox["count"] == 0 and inbox["entries"] == []


# ---------------------------------------------------------------- 告警

def test_alerts_listed_after_high_threat_run(client):
    """真实 W35 有 high → run 后 GET /alerts 台账含 high_threat（倒序展示：最新在前）。"""
    client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    alerts = client.get("/alerts").json()
    assert alerts["count"] >= 1
    top = alerts["alerts"][0]
    assert top["event_type"] == "high_threat" and top["competitor_id"] == "crm_alpha"
    assert top["delivered_by"] == "mock"


def test_hook_manual_alert_and_invalid_type(client):
    """POST /alerts/hook：合法类型 → accepted 回执并入台账；非法类型 → 422。"""
    ok = client.post("/alerts/hook", json={"event_type": "high_threat", "summary": "手动投递演练"})
    assert ok.status_code == 200 and ok.json()["accepted"] is True
    assert ok.json()["delivered_by"] == "mock"
    assert client.get("/alerts").json()["count"] >= 1

    bad = client.post("/alerts/hook", json={"event_type": "whatever", "summary": "x"})
    assert bad.status_code == 422 and "event_type" in bad.json()["detail"]
