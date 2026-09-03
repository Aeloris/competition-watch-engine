# -*- coding: utf-8 -*-
"""Phase 4–5 Orchestration：LangGraph 端到端把 P1–P3 串成一条可跑的周期流水线。

诚实的验收口径（README2 §7.4：P4 = 打通编排；P5 = 把 analyst/writer 做实、reviewer 仍结构性门卫）：
- 真节点 = planner / researchers(复用 collectors 并发+失败隔离) / dedupe_diff(去重+跨期 diff+写快照)；
- P5 做实 = analyst(ThreatRubric 打 high/medium/low + 理由链，只读维度不改 fp) /
  writer(组装合规 WeeklyReport：headline/雷达/kind 计数自校验，只组装不发挥)；
- reviewer = 结构性门卫（每条必有出处/标题/维度 + 已分级带理由；语义 grounding 属 P6）；
- 条件边两条都要测到：REWRITE→回 writer（额度内打回改写）与 额度用尽→human（不自动放行）。

真值来源 = P3 fixtures（docs/memory.md）：
- crm W34 冷启动 2卡→2事件→diff 2（全 add，无上期）；crm W35 对上期 W34 = 5卡→3事件→diff 3（v12 三源并入一 evid=3）；
- 重跑同周期 = diff 0（unchanged 零重复计算）；bi 同表（W35 5卡→4事件）。
"""
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from config.settings import AppConfig, Settings, get_settings
from collectors import CollectorWorker
from collectors import build_collectors as real_build_collectors
from memory import JsonMemoryStore
from orchestration import build_graph, run_cycle
from orchestration import graph as graph_mod
from orchestration.graph import route_after_review
from orchestration.nodes import reviewer_node
from orchestration.planner import default_fetched_at, period_end_exclusive, period_start
from sources.base import SourceAdapter

SETTINGS = get_settings()
COMP = "crm_alpha"
V12_FP = "5c60e045b8853e63"  # fixtures 真值：crm W35 v12.0 发布事件 fp（P3 test_dedupe 锁死）


def _store(tmp_path):
    return JsonMemoryStore(tmp_path / "mem")


def _proj(final: dict) -> dict:
    """确定性投影：剔除 Event.created_at 等时间戳，只看编排语义。

    P5 扩展：纳入每条 severity 序列 + 威胁雷达 → 同语料重跑，分级也必须逐条稳定。
    """
    draft = final.get("draft") or {}
    return {
        "trace": final.get("trace"),
        "diff_summary": final.get("diff_summary"),
        "gate": final.get("gate"),
        "changes_fps": sorted(c.get("fp") for c in final.get("changes", [])),
        "sections": draft.get("sections"),
        "severities": tuple(i.get("severity") for i in draft.get("items", [])),
        "radar": draft.get("threat_radar"),
    }


# ---------- Planner 与周期换算 ----------

def test_period_helpers_iso_weeks():
    assert period_start("2026-W34") == date(2026, 8, 17)
    assert period_start("2026-W35") == date(2026, 8, 24)
    assert period_end_exclusive("2026-W35") == date(2026, 8, 31)
    assert default_fetched_at("2026-W35") == datetime(2026, 8, 31, 9, 0)  # 对齐 cron 周一 09:00
    with pytest.raises(ValueError):
        period_start("2026-08-17")  # 非 "YYYY-Www" 即 fail fast


# ---------- 冷启动 / 真 diff / 幂等重跑 ----------

def test_cold_start_w34_all_add(tmp_path):
    final = run_cycle(SETTINGS, COMP, "2026-W34", store=_store(tmp_path), run_id="c1")
    assert final["trace"] == {"collected": 2, "merged": 2, "diff": 2}  # 无上期快照 → 全 add
    assert final["diff_summary"]["adds"] == 2 and final["diff_summary"]["removes"] == 0
    assert final["gate"]["verdict"] == "PASS" and final["gate"]["reasons"] == []


def test_w35_real_diff_after_w34_seed(tmp_path):
    store = _store(tmp_path)
    run_cycle(SETTINGS, COMP, "2026-W34", store=store, run_id="d1")     # 先写 W34 快照
    final = run_cycle(SETTINGS, COMP, "2026-W35", store=store, run_id="d2")
    # W35 是"对上期 diff"不是全量重算：5 卡合并剩 3 事件、diff 出 3 条新增（v12 三源并入一）
    assert final["trace"] == {"collected": 5, "merged": 3, "diff": 3}
    assert final["diff_summary"]["adds"] == 3 and final["diff_summary"]["unchanged"] == 0
    # 快照已幂等写档（W34 + W35 都在）→ 下期 diff 的比对底
    assert (tmp_path / "mem" / "snapshots" / COMP / "2026-W34.json").exists()
    assert (tmp_path / "mem" / "snapshots" / COMP / "2026-W35.json").exists()
    # v12 发布事件进周报初稿，且带 3 个出处（官网/changelog/媒体）
    v12 = [i for i in final["draft"]["items"] if i.get("fp") == V12_FP]
    assert len(v12) == 1 and len(v12[0]["evidence_urls"]) == 3


def test_rerun_same_period_diff_zero_zero_repeat_cost(tmp_path):
    store = _store(tmp_path)
    run_cycle(SETTINGS, COMP, "2026-W35", store=store, run_id="r1")     # 写 W35 快照
    final = run_cycle(SETTINGS, COMP, "2026-W35", store=store, run_id="r2")  # 再跑同周期
    # 本期内容与上期完全一致 → unchanged，diff 0 = 零重复计算（省 token 的机器）
    assert final["trace"]["diff"] == 0 and final["trace"]["collected"] == 5
    assert final["changes"] == [] and final["draft"]["headline_count"] == 0
    assert final["gate"]["verdict"] == "PASS"  # "本周无变更"是合法结果，不是失败


def test_bi_beta_two_weeks_matches_fixtures(tmp_path):
    store = _store(tmp_path)
    run_cycle(SETTINGS, "bi_beta", "2026-W34", store=store, run_id="b1")
    final = run_cycle(SETTINGS, "bi_beta", "2026-W35", store=store, run_id="b2")
    assert final["trace"] == {"collected": 5, "merged": 4, "diff": 4}  # P3 fixtures 真值：5卡→4事件


# ---------- P5：威胁雷达与 diff K 自洽、逐条分级合规 ----------

def test_w35_threat_radar_sums_to_diff_and_every_item_graded(tmp_path):
    """威胁雷达计数 = diff 出的 K（每条变更都进了雷达）；每条必带分级与理由链。"""
    expected_radar = {  # fixtures 实测真值（docs/analyst_writer.md 校准表）
        "crm_alpha": {"high": 1, "medium": 1, "low": 1},
        "bi_beta": {"high": 1, "medium": 1, "low": 2},
    }
    for comp, radar in expected_radar.items():
        store = _store(tmp_path / comp)
        run_cycle(SETTINGS, comp, "2026-W34", store=store, run_id="seed")
        final = run_cycle(SETTINGS, comp, "2026-W35", store=store, run_id="radar")
        draft = final["draft"]
        K = final["trace"]["diff"]
        assert sum(draft["threat_radar"].values()) == K           # 雷达各项相加 = 下发变更数
        assert draft["threat_radar"] == radar
        assert draft["headline_count"] == K == len(draft["items"])
        for it in draft["items"]:
            assert it["severity"] in {"high", "medium", "low"}     # 全部已分级
            assert isinstance(it["reasons"], list) and it["reasons"]
            assert it["evidence_urls"]                             # 全部可溯源


# ---------- 状态机属性：完整链 / checkpoint / 确定性 ----------

def test_final_state_has_full_chain(tmp_path):
    final = run_cycle(SETTINGS, COMP, "2026-W35", store=_store(tmp_path), run_id="f1")
    for key in ("plan", "collect_summary", "cards", "changes", "diff_summary",
                "write_items", "draft", "gate", "trace"):
        assert key in final, f"最终 State 缺 {key}"
    assert final["plan"]["period"] == "2026-W35"
    assert len(final["collect_summary"]["failures"]) == 0          # 全健康 → 无失败标注
    assert len(final["write_items"]) == len(final["draft"]["items"])
    assert final["trace"]["diff"] == len(final["changes"])          # K = 本期下发条数


def test_checkpointer_get_state_retrieves_run(tmp_path):
    app = build_graph(SETTINGS, store=_store(tmp_path))
    init = {"competitor_id": COMP, "period": "2026-W35", "run_id": "ck1",
            "fetched_at": default_fetched_at("2026-W35").isoformat()}
    final = app.invoke(init, config={"configurable": {"thread_id": "ck1"}})
    snap = app.get_state({"configurable": {"thread_id": "ck1"}})
    assert snap.values.get("run_id") == "ck1"
    assert snap.values.get("gate") == final["gate"]                # checkpoint 能取回最终判定
    assert final["trace"]["merged"] == 3


def test_run_cycle_is_deterministic(tmp_path):
    # 两条独立链：各自先跑 W34 做冷启动底座 → 再跑 W35，比较编排语义投影是否一致。
    # （不能用同一 store 连跑两次 —— 第二次会读到第一次写的 W35 快照，那是"记忆累积"不是不确定。）
    sa, sb = _store(tmp_path / "a"), _store(tmp_path / "b")
    for st in (sa, sb):
        run_cycle(SETTINGS, COMP, "2026-W34", store=st, run_id="seed")
    a = run_cycle(SETTINGS, COMP, "2026-W35", store=sa, run_id="det")
    b = run_cycle(SETTINGS, COMP, "2026-W35", store=sb, run_id="det")
    assert _proj(a) == _proj(b)  # 同输入（同语料/同 fetched_at/同底库）→ 同编排语义


def test_run_cycle_persist_writes_state_file(tmp_path):
    s = Settings(app=AppConfig(name="x", data_dir=str(tmp_path / "data"), fixtures_dir="./fixtures"))
    run_cycle(s, COMP, "2026-W34", store=_store(tmp_path), run_id="p1", persist=True)
    assert (tmp_path / "data" / "pipeline" / "p1.json").exists()    # 文件即状态 → P7/P8 审计对象


# ---------- reviewer（结构性门卫）单元 + 条件边 ----------

def _ctx():
    return SimpleNamespace(review_max_rewrites=2)


def _clean_item(title: str = "T", urls=None, severity: str = "medium", reasons=None) -> dict:
    """P5 合规条目：有出处 + 已分级 + 带理由（reviewer 通过门槛的最低形态）。

    注意：空 list/None 是"故意缺口"，必须原样保留（不能用 `x or 默认`——会把空列表顶成默认值）。
    """
    return {"title": title, "evidence_urls": ["https://x/1"] if urls is None else list(urls),
            "dimension": "feature", "severity": severity,
            "reasons": ["规则[feature_update]：常规迭代"] if reasons is None else list(reasons)}


def test_reviewer_unit_clean_pass(tmp_path):
    state = {"draft": {"items": [_clean_item()]}, "rewrites": 0}
    out = reviewer_node(state, _ctx())
    assert out["gate"]["verdict"] == "PASS" and out["gate"]["reasons"] == []


def test_reviewer_unit_gap_rewrite_then_human(tmp_path):
    poisoned = {"draft": {"items": [_clean_item(title="无出处", urls=[])]}}
    r1 = reviewer_node({**poisoned, "rewrites": 0}, _ctx())
    assert r1["gate"]["verdict"] == "REWRITE" and r1["rewrites"] == 1
    r2 = reviewer_node({**poisoned, "rewrites": 1}, _ctx())
    assert r2["gate"]["verdict"] == "REWRITE" and r2["rewrites"] == 2
    r3 = reviewer_node({**poisoned, "rewrites": 2}, _ctx())
    assert r3["gate"]["verdict"] == "human" and r3["rewrites"] == 2  # 额度用尽 → 转人工不自动放行


def test_reviewer_unit_missing_severity_rewrite_then_human(tmp_path):
    """P5 门卫新增校验：条目未分级（缺 severity）→ 打回；额度用尽 → human 不自动放行。"""
    ungraded = {"draft": {"items": [_clean_item(severity=None)]}}
    r1 = reviewer_node({**ungraded, "rewrites": 0}, _ctx())
    assert r1["gate"]["verdict"] == "REWRITE" and r1["rewrites"] == 1
    assert any("未分级" in p for p in r1["gate"]["reasons"])
    r3 = reviewer_node({**ungraded, "rewrites": 2}, _ctx())
    assert r3["gate"]["verdict"] == "human" and r3["rewrites"] == 2


def test_reviewer_unit_missing_reasons_rewrite(tmp_path):
    ungraded = {"draft": {"items": [_clean_item(reasons=[])]}}  # 显式空理由 = "缺理由"缺口
    r1 = reviewer_node({**ungraded, "rewrites": 0}, _ctx())
    assert r1["gate"]["verdict"] == "REWRITE"
    assert any("reasons 空" in p for p in r1["gate"]["reasons"])


def test_route_after_review_maps_conditional_edge():
    assert route_after_review({"gate": {"verdict": "REWRITE"}}) == "writer"
    assert route_after_review({"gate": {"verdict": "PASS"}}) == "__end__"
    assert route_after_review({"gate": {"verdict": "human"}}) == "__end__"


def test_graph_rewrite_loop_ends_human_on_poison(tmp_path, monkeypatch):
    """注入缺出处的 writer（模拟下游 bug/编造入口）→ 图走 打回≤2次 → human，不硬放行。

    其余字段（severity/reasons/dimension）合规，只毒 evidence_urls —— 让门卫精准抓到"无出处"这一个缺口。
    """
    def poison_writer(state: dict, ctx=None) -> dict:  # 签名对齐 NODE_IMPLS 被 bind 的 (state, ctx)
        return {"draft": {"competitor_id": state["competitor_id"], "period": state["period"],
                          "headline_count": 1, "sections": {"adds": 1, "changes": 0, "removes": 0},
                          "items": [_clean_item(title="编造动态", urls=[])]}}

    monkeypatch.setitem(graph_mod.NODE_IMPLS, "writer", poison_writer)
    final = run_cycle(SETTINGS, COMP, "2026-W35", store=_store(tmp_path), run_id="poison")
    assert final["gate"]["verdict"] == "human" and final["rewrites"] == 2


# ---------- 失败隔离在编排层成立 ----------

def test_single_source_failure_isolated_in_graph(tmp_path, monkeypatch):
    class _BoomAdapter(SourceAdapter):
        name = "boom"

        async def fetch(self, competitor_id, kind, *, since=None):
            raise ConnectionError("simulated source outage")

    def fake_build(settings):
        workers = real_build_collectors(settings)
        workers["rss"] = CollectorWorker(_BoomAdapter(), "rss", max_retries=1)
        return workers

    monkeypatch.setattr("orchestration.nodes.build_collectors", fake_build)
    final = run_cycle(SETTINGS, COMP, "2026-W35", store=_store(tmp_path), run_id="boom")
    # 节点不 raise；失败源准确记账；健康源照常产出（website+news 只剩 3 卡 → 去重 2 事件）
    assert len(final["collect_summary"]["failures"]) == 1
    assert final["collect_summary"]["failures"][0].startswith("rss/crm_alpha")
    assert "ConnectionError" in final["collect_summary"]["failures"][0]
    assert final["trace"]["collected"] == 3 and final["trace"]["merged"] == 2
    assert final["gate"]["verdict"] == "PASS"  # 剩下的都有出处 → 仍过结构门卫
