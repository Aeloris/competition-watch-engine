# -*- coding: utf-8 -*-
"""增量「跨竞品横向对比」M1：compare_view + GET /tasks/{id}/compare + 面板段 端到端。

真实链路：POST /tasks（真实 Mock 采集 → dedupe → diff → Analyst → Writer → 门卫 PASS，
自审通过自动发布）→ 发布视图已放行条目 → compare 纯派生对齐。
验收口径（诚实边界同 docs/compare.md：维度对齐 ≠ 实体对齐）：
- 批量 W35（crm_alpha + bi_beta 同 Task 全放行）→ 200：feature 双竞品都动、bi 先动（08-24<08-25）；
  market 仅 bi（盘点=单边动作，事实陈述不称盲点）；雷达/维度覆盖如实并列；
- 路由输出 == compare_view 纯函数 == 重复请求逐字节一致（确定性，无时间戳）；
- 单竞品 Task（先审后发后不足两家）→ 422 + reason；task 不存在 → 404；
- 面板 task 页渲染横向表（★ 先行标记）；单竞品面板明说"无可比"。

雷达数字为既有实测（crm 5→3→3 radar{1,1,1} / bi 5→4→4 radar{1,1,2}），非新编。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.deps import get_service_ctx
from service import build_service_context
from service.compare import compare_view


@pytest.fixture
def client(tmp_path):
    """HTTP 层隔离 ctx：data_dir → tmp + dependency_overrides（同 test_service_api/test_panel_api）。"""
    ctx = build_service_context(data_dir=str(tmp_path))
    app.dependency_overrides[get_service_ctx] = lambda: ctx
    try:
        with TestClient(app) as c:
            c.ctx = ctx
            yield c
    finally:
        app.dependency_overrides.clear()


def _run_batch(client) -> str:
    """POST /tasks 跑 W35 双竞品批量巡检 → 返回 task_id（真实全链路，PASS 自动发布）。"""
    r = client.post("/tasks", json={"period": "2026-W35"})
    assert r.status_code == 201, r.text
    return r.json()["task_id"]


# ---------------------------------------------------------------- 批量 W35：横向对比如实对齐
def test_compare_batch_w35_alignment_and_first_mover(client):
    tid = _run_batch(client)
    resp = client.get(f"/tasks/{tid}/compare")
    assert resp.status_code == 200, resp.text
    rep = resp.json()

    assert rep["period"] == "2026-W35"
    assert [c["competitor_id"] for c in rep["competitors"]] == ["bi_beta", "crm_alpha"]  # 规范化序
    radar_by = {c["competitor_id"]: c for c in rep["competitors"]}
    assert radar_by["bi_beta"]["radar"] == {"high": 1, "medium": 1, "low": 2}
    assert radar_by["crm_alpha"]["radar"] == {"high": 1, "medium": 1, "low": 1}
    assert radar_by["bi_beta"]["dims_moved"] == ["feature", "market"]   # 枚举声明序
    assert radar_by["crm_alpha"]["dims_moved"] == ["feature"]

    rows = {r["dimension"]: r for r in rep["rows"]}
    assert list(rows) == ["feature", "market"]

    frow = rows["feature"]
    assert frow["moved_by"] == ["bi_beta", "crm_alpha"]
    assert frow["all_moved"] is True and frow["orderable"] is True
    assert frow["first_mover"] == ["bi_beta"]                            # bi 08-24 早于 crm 08-25
    fcell = {c["competitor_id"]: c for c in frow["cells"]}
    assert fcell["bi_beta"]["earliest"] == "2026-08-24"
    assert fcell["crm_alpha"]["earliest"] == "2026-08-25"

    mrow = rows["market"]
    assert mrow["moved_by"] == ["bi_beta"]                               # 单边动作（bi 盘点，crm 未动）
    assert mrow["first_mover"] == ["bi_beta"]

    assert rep["both_moved_dims"] == ["feature"]
    assert [(s["dimension"], s["mover"]) for s in rep["single_moved_dims"]] == [("market", "bi_beta")]
    assert rep["first_move_counts"] == {"bi_beta": 2}   # 零键不进 counts（与 M0 builder 一致）


# ---------------------------------------------------------------- 确定性 & 路由==纯函数
def test_compare_deterministic_and_equals_service(client):
    tid = _run_batch(client)
    a = client.get(f"/tasks/{tid}/compare").json()
    b = client.get(f"/tasks/{tid}/compare").json()          # 重复请求
    assert a == b                                          # 逐字节一致（无时间戳）
    svc = compare_view(client.ctx.task_store, client.ctx.settings, tid)
    assert a == svc                                         # 路由输出 == 服务层纯派生
    # 说明：Memory 是跨期有状态设计——同周期已在快照里再重跑 diff=0（正是跨期对比的意义），
    # 故"状态无关性"由跨 store（每个测试独立 tmp）+ 两次 GET 相等共同覆盖，不再同 store 重跑同周期。


# ---------------------------------------------------------------- 边界：单竞品 422 / 未知 404
def test_compare_single_competitor_422(client):
    r = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    assert r.status_code == 201, r.text
    tid = r.json()["task_id"]
    resp = client.get(f"/tasks/{tid}/compare")
    assert resp.status_code == 422
    assert "需 ≥2" in resp.json()["detail"]


def test_compare_unknown_task_404(client):
    resp = client.get("/tasks/no-such-task/compare")
    assert resp.status_code == 404


# ---------------------------------------------------------------- 面板段渲染
def test_panel_task_renders_compare_table_and_star(client):
    tid = _run_batch(client)
    html = client.get(f"/panel/task/{tid}").text
    assert "跨竞品横向对比" in html
    assert "★ 先行" in html                       # feature 行 bi 是先行方
    assert "全员动作维度：feature" in html
    assert "单边动作维度：market←bi_beta" in html


def test_panel_task_single_competitor_says_not_comparable(client):
    r = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    tid = r.json()["task_id"]
    html = client.get(f"/panel/task/{tid}").text
    assert "无可比：需 ≥2 家有已放行条目的竞品" in html
