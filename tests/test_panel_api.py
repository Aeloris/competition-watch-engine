# -*- coding: utf-8 -*-
"""Phase 8 人工放行 + 发布视图 + 简单面板（README2 §7.4 row 8）HTTP/服务层端到端。

验收口径：
- GET /inbox?status=pending|all|resolved（缺省 pending 保 P7 语义）；条目带稳定 entry_id=`{run_id}#{seq}`
  与由 resolution 推导的 status（note 只备注不销案 → 仍 pending）；
- POST /inbox/resolve：release/dismiss = 销案（改判同一条目 → 409）；note = 留批注不销案；
  entry_id 未知 → 404；格式错 / action 非法 / by 空 → 422；
- GET /tasks/{id}/published：发布视图把 draft 按 resolution 过滤成 published/held/dismissed
  ——自审通过自动发布、全部 release 才发布(human_released)、有 dismiss 剔除、仍有未放行则挂起；
- GET /panel / /panel/task/{id}：简单面板 HTML（收件箱放行按钮 + 发布视图 + 告警可视化），404 缺任务。

真实链路：注入假 writer（跑真实 Writer 组装真实 write_items + 掺 1–2 条编造条目，复用 eval 注入样例）
→ runner 真跑 → 门卫 human → 收件箱条目带 item_index 指向 draft —— published_view 过滤的就是"真 run"产物。
"""
from __future__ import annotations

from collections import Counter

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routers.deps import get_service_ctx
from eval import adversarial as adv
from memory.schemas import EventKind
from orchestration import graph as graph_mod
from reviewer import UNGROUNDED_FP, UNSOURCED_URL
from service import build_service_context
from service.notifier import make_alert
from writer import Writer


@pytest.fixture
def client(tmp_path):
    """HTTP 层隔离 ctx（同 test_service_api）：data_dir → tmp + dependency_overrides。"""
    ctx = build_service_context(data_dir=str(tmp_path))
    app.dependency_overrides[get_service_ctx] = lambda: ctx
    try:
        with TestClient(app) as c:
            c.ctx = ctx
            yield c
    finally:
        app.dependency_overrides.clear()


# ---------------------------------------------------------------- 造"真 run + 掺假"的毒 writer

def _poisoned_writer(*poison_fns):
    """毒 writer：真实 Writer 组装真实 write_items + 追加注入条目（kind/sections 自洽，eval 同款）。"""

    def wrapped(state: dict, ctx=None) -> dict:
        items = list(state.get("write_items") or [])
        for fn in poison_fns:
            items.extend(fn(state))
        kinds = Counter(i.get("kind") for i in items)
        diff = state.get("diff_summary", {})
        report = Writer().build(
            run_id=state.get("run_id"), competitor_id=state["competitor_id"],
            period=state["period"], generated_at=state.get("fetched_at"),
            sections={"adds": kinds.get(EventKind.add.value, 0),
                      "changes": kinds.get(EventKind.change.value, 0),
                      "removes": diff.get("removes", 0)},
            items=items,
        )
        return {"draft": report.model_dump(mode="json")}

    return wrapped


# ---------------------------------------------------------------- 收件箱状态 + resolve 生命周期

def test_inbox_status_filter_and_resolve_lifecycle(client, monkeypatch):
    monkeypatch.setitem(graph_mod.NODE_IMPLS, "writer", _poisoned_writer(adv.poison_unsourced))
    res = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    body = res.json()
    run = body["runs"][0]
    assert run["verdict"] == "human" and run["inbox_count"] == 1

    # 默认 status=pending → 这条在待办；带稳定 entry_id；item_index 指向 draft（真条目1-3 + 注入第4）
    inbox = client.get("/inbox").json()
    assert inbox["status_filter"] == "pending" and inbox["count"] == 1
    e0 = inbox["entries"][0]
    assert e0["entry_id"].startswith(f"{run['run_id']}#") and e0["status"] == "pending"
    assert e0["code"] == UNSOURCED_URL and e0["item_index"] == 4
    assert e0["resolution"] is None
    eid = e0["entry_id"]

    # note = 只备注不销案：仍 pending，但 resolution 已落
    n = client.post("/inbox/resolve", json={"entry_id": eid, "action": "note",
                                            "by": "测试员", "note": "等周报口径核对"})
    assert n.status_code == 200 and n.json()["entry"]["status"] == "pending"
    assert n.json()["entry"]["resolution"]["action"] == "note"
    assert client.get("/inbox").json()["count"] == 1  # 备注不销案 → 还在待办

    # release 放行 → 销案；默认 pending 消失，resolved/all 可见
    ok = client.post("/inbox/resolve", json={"entry_id": eid, "action": "release", "by": "测试员"})
    assert ok.status_code == 200
    assert ok.json()["entry"]["status"] == "resolved"
    assert ok.json()["entry"]["resolution"]["action"] == "release"
    assert client.get("/inbox").json()["count"] == 0
    r = client.get("/inbox", params={"status": "resolved"}).json()
    assert r["count"] == 1 and r["entries"][0]["resolution"]["action"] == "release"
    assert client.get("/inbox", params={"status": "all"}).json()["count"] == 1

    # 已销案再定案 → 409（不许覆盖审计决定）；未知条目 → 404；格式/字段非法 → 422
    dup = client.post("/inbox/resolve", json={"entry_id": eid, "action": "dismiss", "by": "x"})
    assert dup.status_code == 409 and "已定案" in dup.json()["detail"]
    assert client.post("/inbox/resolve", json={"entry_id": f"{run['run_id']}#99",
                                               "action": "release", "by": "x"}).status_code == 404
    assert client.post("/inbox/resolve", json={"entry_id": "no-hash",
                                               "action": "release", "by": "x"}).status_code == 422
    assert client.post("/inbox/resolve", json={"entry_id": f"{run['run_id']}#x",
                                               "action": "release", "by": "x"}).status_code == 422
    assert client.post("/inbox/resolve", json={"entry_id": eid, "action": "delete",
                                               "by": "x"}).status_code == 422
    assert client.post("/inbox/resolve", json={"entry_id": eid, "action": "release",
                                               "by": ""}).status_code == 422
    # A5 回归：全空白 by（"   "）也要 422（先 strip 再判空，防把空白处理人存进审计）
    assert client.post("/inbox/resolve", json={"entry_id": eid, "action": "release",
                                               "by": "   "}).status_code == 422


# ---------------------------------------------------------------- 发布视图过滤

def test_published_view_holds_then_release_and_dismiss(client, monkeypatch):
    monkeypatch.setitem(graph_mod.NODE_IMPLS, "writer",
                        _poisoned_writer(adv.poison_unsourced, adv.poison_ungrounded_fp))
    post = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    task_id, run = post.json()["task_id"], post.json()["runs"][0]
    assert run["verdict"] == "human" and run["inbox_count"] == 2
    ids = [e["code"] for e in run["human_inbox"]]
    assert set(ids) == {UNSOURCED_URL, UNGROUNDED_FP}

    inbox = client.get("/inbox").json()["entries"]
    assert [e["code"] for e in inbox] == [UNSOURCED_URL, UNGROUNDED_FP]  # 与 draft 条序一致

    def pub():
        return client.get(f"/tasks/{task_id}/published").json()

    # 未处理：3 条真实(自审通过)自动发布；2 条注入挂起 held；无剔除
    pv = pub()
    run_v = pv["runs"][0]
    assert run_v["verdict"] == "human"
    assert pv["published_total"] == 3 and pv["held_total"] == 2 and pv["dismissed_total"] == 0
    assert run_v["published_count"] == 3 and run_v["held_count"] == 2 and run_v["dismissed_count"] == 0
    assert all(it["human_released"] is False for it in run_v["published"])  # 自审通过，非人工放行
    held_codes = {p["code"] for h in run_v["held"] for p in h["problems"]}
    assert held_codes == {UNSOURCED_URL, UNGROUNDED_FP}
    assert all(p["status"] == "pending" for h in run_v["held"] for p in h["problems"])

    # 放行 UNSOURCED_URL 那条（inbox seq0=item_index 4）→ 发布 4、held 1
    rel = client.post("/inbox/resolve", json={"entry_id": inbox[0]["entry_id"],
                                              "action": "release", "by": "分析师A"})
    assert rel.status_code == 200
    pv2 = pub()
    run_v2 = pv2["runs"][0]
    assert run_v2["published_count"] == 4 and run_v2["held_count"] == 1 and run_v2["dismissed_count"] == 0
    released = [it for it in run_v2["published"] if it["human_released"] is True]
    assert len(released) == 1 and released[0]["resolution"]["action"] == "release"
    assert released[0]["resolution"]["by"] == "分析师A"

    # 驳回 UNGROUNDED_FP 那条 → 剔除（从发布剔除、进 dismissed 审计区）；held 清空
    dis = client.post("/inbox/resolve", json={"entry_id": inbox[1]["entry_id"],
                                              "action": "dismiss", "by": "分析师A",
                                              "note": "非本期真变更，误报"})
    assert dis.status_code == 200
    pv3 = pub()
    run_v3 = pv3["runs"][0]
    assert run_v3["published_count"] == 4 and run_v3["held_count"] == 0 and run_v3["dismissed_count"] == 1
    assert run_v3["dismissed"][0]["problems"][0]["code"] == UNGROUNDED_FP
    assert pv3["published_total"] == 4 and pv3["dismissed_total"] == 1
    # 发布条目里不再有注入的假 fp（UNGROUNDED_FP 那条的 fp 是 fabricated）
    pub_fps = {it["fp"] for it in run_v3["published"]}
    assert "fp-fabricated-0001" not in pub_fps


def test_published_view_pass_run_publishes_everything(client):
    """真实 PASS run：无门卫指向 → 全部自动发布，held/dismissed 空（不因"有 high"就挂起）。"""
    post = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    assert post.json()["runs"][0]["verdict"] == "PASS"
    pv = client.get(f"/tasks/{post.json()['task_id']}/published").json()
    run_v = pv["runs"][0]
    assert run_v["verdict"] == "PASS" and pv["published_total"] == 3   # crm W35 3 条真实变更
    assert pv["held_total"] == 0 and pv["dismissed_total"] == 0
    assert all(it["human_released"] is False for it in run_v["published"])


def test_published_view_404_unknown_task(client):
    assert client.get("/tasks/nope/published").status_code == 404


# ---------------------------------------------------------------- 简单面板

def test_panel_overview_and_task_detail(client, monkeypatch):
    monkeypatch.setitem(graph_mod.NODE_IMPLS, "writer", _poisoned_writer(adv.poison_unsourced))
    post = client.post("/tasks", json={"period": "2026-W35", "competitor_id": "crm_alpha"})
    task_id = post.json()["task_id"]

    overview = client.get("/panel")
    assert overview.status_code == 200 and "text/html" in overview.headers["content-type"]
    html = overview.text
    assert "人工放行面板" in html and "人工收件箱待办" in html and UNSOURCED_URL in html
    assert f"/panel/task/{task_id}" in html

    detail = client.get(f"/panel/task/{task_id}")
    assert detail.status_code == 200 and "text/html" in detail.headers["content-type"]
    dhtml = detail.text
    assert "发布视图" in dhtml and "放行" in dhtml and "驳回" in dhtml and "备注" in dhtml
    entry_id = client.get("/inbox").json()["entries"][0]["entry_id"]
    assert entry_id in dhtml            # 面板能定位到具体待办条目
    assert UNGROUNDED_FP not in dhtml   # 只注入了 UNSOURCED_URL 这一种
    assert client.get("/panel/task/nope").status_code == 404


def test_panel_empty_state_ok(client):
    """空 ctx：面板 200 且给"暂无任务/无待办"提示（不 500）。"""
    html = client.get("/panel").text
    assert "人工放行面板" in html and "待办已清空" in html and "暂无任务" in html


def test_panel_alerts_newest_first(client):
    """A7 回归：告警台账按 append 序存（旧→新），面板"最近告警"必须最新在前——
    旧实现 `[:8]` 直接取头 = 把最旧的 8 条当"最近告警"（注释还错标"台账已倒序存"）。"""
    ctx = client.ctx
    ctx.notifier.deliver(make_alert("high_threat", summary="ALERT-OLD",
                                    competitor_id="crm_alpha", period="2026-W35"))
    ctx.notifier.deliver(make_alert("high_threat", summary="ALERT-NEW",
                                    competitor_id="crm_alpha", period="2026-W35"))
    html = client.get("/panel").text
    assert html.index("ALERT-NEW") < html.index("ALERT-OLD")   # 最新在前（append 序须反转再截取）
