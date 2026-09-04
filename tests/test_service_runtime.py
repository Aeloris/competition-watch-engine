# -*- coding: utf-8 -*-
"""Phase 7 服务运行时（README2 §5.9 定时与告警 + §5.10 服务层健壮性与观测）。

验收口径（README2 §7.4 row 7 的任务/定时/告警/健壮性）：
- execute_task：一个 Task = period × 多竞品，逐 run 执行；TaskStore 收终态 + 聚合 summary；
- **run 级失败隔离**：单竞品 run_cycle 抛错 → 记 failed + error，健康竞品照常 done（Task=partial）
  —— 这是 P5 采集层"源级隔离"在服务层的升级；run_failed 告警落台账；
- **trace 计数链收口**：RunMeta 抄 collected→merged→diff + 门卫判定（verdict/gate_problems/
  gate_trace_count/inbox）——"采集 N→去重 M→diff K→门卫拦 X"在服务层可查；
- 高威胁（verdict PASS 且 radar.high>0，缺省 schedule.alert_on_high_threat=True）→ high_threat 即时告警；
- **故障演练 A**：monkeypatch run_cycle 抛错 → 单 run failed + Task partial + run_failed 告警；
- **故障演练 B**：注入"真 fp 拼编造 URL"的假 writer（复用 test_reviewer_gate 手法）→ 门卫 human 拦下
  → 人工收件箱条目 + human_inbox 告警（先审后发延伸到服务层，假事件绝不"零收件箱地 completed"）；
- 调度：weekly_report job 的 cron 触发点 = 每周一 09:00（不 start 不真等）；run_now 手动触发即跑；
- 告警适配：MockNotifier 落台账；WebhookNotifier 空 url fail-fast ValueError、deliver 抛 NotImplemented
  （诚实：不假装已发真 HTTP）。

全部离线确定性：ctx = build_service_context(data_dir=tmp)，memory/pipeline/service 全隔离在 tmp。
trace/radar 数字为实测（非编造）：fresh-store W35 单跑 crm 5→3→3、radar{high1,med1,low1}；
bi 5→4→4、radar{high1,med1,low2}（demo 与 §6 口径一致）。
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

from orchestration import graph as graph_mod
from reviewer import UNSOURCED_URL
from service import (
    MockNotifier,
    WebhookNotifier,
    available_competitors,
    build_scheduler,
    build_service_context,
    last_completed_iso_week,
    run_now,
    schedule_weekly_report,
)
from service.notifier import make_alert
from service.schemas import RunMeta, RunStatus, TaskStatus
from service.store import read_alerts
import service.runner as runner_mod

V12_FP = "5c60e045b8853e63"  # fixtures 真值：crm W35 v12.0 发布事件 fp（复用于"真 fp 拼编造 URL"注入）


def _ctx(tmp_path):
    return build_service_context(data_dir=str(tmp_path))


def _item(title: str = "T", *, fp: str = "fp-1", urls=("https://a.example/x",),
          severity: str = "medium", reasons=None) -> dict:
    """结构性干净的门卫条目（有出处/标题/维度/已分级/带理由/有 fp）——只差"信任"层面可被下毒。"""
    return {"fp": fp, "kind": "add", "dimension": "feature", "severity": severity,
            "reasons": list(reasons) if reasons is not None else ["规则[feature_update]：常规迭代"],
            "title": title, "summary": "", "evidence_urls": list(urls)}


# ---------------------------------------------------------------- 辅助纯函数

def test_last_completed_iso_week_boundaries():
    """'最近一个已结束的 ISO 周'口径：本周一 09:00 cron 触发时正好报"上一完整周"；周中/周日/跨年对齐。"""
    # 值实测（2026-09-04 真跑）：见 runner.last_completed_iso_week 注释例。
    assert last_completed_iso_week(date(2026, 9, 4)) == "2026-W35"   # 周五：W35(8-24~30) 已完整过去
    assert last_completed_iso_week(date(2026, 9, 7)) == "2026-W36"   # 周一 09:00 cron：报 8-31~9-6
    assert last_completed_iso_week(date(2026, 9, 13)) == "2026-W36"  # 周日：W37(9-7~13) 尚在进行不计
    assert last_completed_iso_week(date(2026, 9, 14)) == "2026-W37"  # 下周一：W37 已结束 → 换周
    # 跨年：2025-12-31(周三) → ISO 2025-W52；2026-01-05(周一) → 2026-W01（2025-12-29 归属 2026）
    assert last_completed_iso_week(date(2025, 12, 31)) == "2025-W52"
    assert last_completed_iso_week(date(2026, 1, 5)) == "2026-W01"


def test_available_competitors_from_fixtures():
    """可用竞品 = fixtures mock 目录里的 json（离线底座，确定性）；与数据目录无关。"""
    import tempfile
    ctx = _ctx(tempfile.mkdtemp())
    comps = available_competitors(ctx.settings)
    assert comps == ["bi_beta", "crm_alpha"]  # 排序稳定
    assert comps == sorted(comps)


# ---------------------------------------------------------------- TaskStore 文件即状态

def test_task_store_roundtrip_order_and_tolerates_corrupt(tmp_path):
    ctx = _ctx(tmp_path)
    ts = ctx.task_store
    ts.create_task("t1", period="2026-W35", competitors_requested=["crm_alpha"],
                   created_at="2026-09-01T09:00:00+00:00")
    ts.create_task("t2", period="2026-W35", competitors_requested=[],
                   created_at="2026-09-02T09:00:00+00:00")

    # 同一 run_id 幂等覆盖、不同 run 追加
    run = RunMeta(run_id="r1", competitor_id="crm_alpha", period="2026-W35",
                  status=RunStatus.done, verdict="PASS", trace={"collected": 5})
    ts.add_run("t1", run)
    run2 = run.model_copy(update={"trace": {"collected": 6}})
    ts.add_run("t1", run2)  # 同 run_id 覆盖
    t1 = ts.finalize("t1", finished_at="2026-09-03T09:00:00+00:00", summary={"done": 1, "failed": 0})
    assert len(t1.runs) == 1 and t1.runs[0].trace == {"collected": 6}
    assert t1.status == TaskStatus.completed

    # list 时间倒序；损坏文件被容忍跳过
    (tmp_path / "service" / "tasks" / "bad.json").write_text("{not json", encoding="utf-8")
    metas = ts.list_tasks()
    assert [m.task_id for m in metas] == ["t2", "t1"]
    assert ts.get_task("nope") is None


def test_alert_ledger_file_append_read(tmp_path):
    ctx = _ctx(tmp_path)
    assert ctx.list_alerts() == []
    ev = make_alert("high_threat", summary="测", competitor_id="crm_alpha", period="2026-W35", run_id="r1")
    receipt = ctx.notifier.deliver(ev)
    assert receipt["delivered_by"] == "mock" and receipt["alert_id"] == ev.alert_id
    log = read_alerts(ctx.settings)
    assert len(log) == 1 and log[0]["event_type"] == "high_threat"
    assert ctx.list_alerts()[0]["event_type"] == "high_threat"


def test_add_run_replaces_in_place_not_moves_to_tail(tmp_path):
    """A6 回归：同名 run_id 覆盖必须**原位**替换，不能"去重后追加到尾部"——
    否则重跑记录被挪到最后，runs 顺序与真实执行顺序错位（报告/发布视图按 runs 顺序拼装会错）。"""
    ts = _ctx(tmp_path).task_store
    ts.create_task("t", period="2026-W35", competitors_requested=["crm_alpha", "bi_beta"],
                   created_at="2026-09-01T09:00:00+00:00")
    ra = RunMeta(run_id="ra", competitor_id="crm_alpha", period="2026-W35",
                 status=RunStatus.done, verdict="PASS", trace={"collected": 5})
    rb = RunMeta(run_id="rb", competitor_id="bi_beta", period="2026-W35",
                 status=RunStatus.done, verdict="PASS", trace={"collected": 6})
    ts.add_run("t", ra)
    ts.add_run("t", rb)
    ra2 = ra.model_copy(update={"verdict": "human"})      # 重跑 ra：原位覆盖，rb 的位置不动
    ts.add_run("t", ra2)
    meta = ts.get_task("t")
    assert [r.run_id for r in meta.runs] == ["ra", "rb"]  # 旧实现 → ["rb", "ra"]
    assert meta.runs[0].verdict == "human"


# ---------------------------------------------------------------- Notifier 适配层

def test_webhook_fail_fast_no_url_and_deliver_placeholder(tmp_path):
    ctx = _ctx(tmp_path)
    assert isinstance(ctx.notifier, MockNotifier)
    with pytest.raises(ValueError):
        WebhookNotifier("")  # 空 url fail-fast，不许静默退化成不发
    ev = make_alert("run_failed", summary="s", competitor_id="crm_alpha", period="2026-W35", run_id="r1")
    with pytest.raises(NotImplementedError):
        WebhookNotifier("https://hooks.example/x").deliver(ev)  # 诚实：占位不真发 HTTP
    # A9 回归：壳不落台账 → list_log 返回空（此前 raise NotImplementedError → GET /alerts 500）
    assert WebhookNotifier("https://hooks.example/x").list_log() == []


# ---------------------------------------------------------------- execute_task 正常路径

def test_runner_single_competitor_w35_canonical(tmp_path):
    """单竞品 W35：crm 5→3→3、radar{1,1,1}、PASS、零收件箱，high_threat 即时告警（实测数字）。"""
    ctx = _ctx(tmp_path)
    meta = ctx.runner.execute_task("t-w35", period="2026-W35", competitors=["crm_alpha"])
    assert meta.status == TaskStatus.completed
    r = meta.runs[0]
    assert r.competitor_id == "crm_alpha" and r.status == RunStatus.done and r.verdict == "PASS"
    assert r.trace == {"collected": 5, "merged": 3, "diff": 3}   # 计数链：采集→去重→diff
    assert r.threat_radar == {"high": 1, "medium": 1, "low": 1}
    assert r.gate_problems == 0 and r.inbox_count == 0
    assert r.gate_trace_count == 1                               # PASS 也留一条审计迹
    assert meta.summary["runs"] == 1 and meta.summary["done"] == 1 and meta.summary["failed"] == 0
    highs = [a for a in ctx.list_alerts() if a["event_type"] == "high_threat"]
    assert len(highs) == 1 and highs[0]["competitor_id"] == "crm_alpha"
    # 全量周报已落 data/pipeline/{run_id}.json（file=state，报告路由可回读）
    assert (ctx.settings.data_path / "pipeline" / f"{r.run_id}.json").exists()


def test_runner_batch_both_competitors_w35(tmp_path):
    """缺省跑全部可用竞品：两 run 全 done；各自 high_threat 告警（bi 5→4→4 radar{1,1,2}）。"""
    ctx = _ctx(tmp_path)
    meta = ctx.runner.execute_task("t-batch", period="2026-W35")
    assert meta.status == TaskStatus.completed and meta.summary["done"] == 2
    by_comp = {r.competitor_id: r for r in meta.runs}
    assert by_comp["crm_alpha"].trace == {"collected": 5, "merged": 3, "diff": 3}
    assert by_comp["bi_beta"].trace == {"collected": 5, "merged": 4, "diff": 4}
    assert by_comp["bi_beta"].threat_radar == {"high": 1, "medium": 1, "low": 2}
    types = {(a["event_type"], a["competitor_id"]) for a in ctx.list_alerts()}
    assert ("high_threat", "crm_alpha") in types and ("high_threat", "bi_beta") in types


def test_runner_empty_competitors_failed_task(tmp_path):
    ctx = _ctx(tmp_path)
    meta = ctx.runner.execute_task("t-none", period="2026-W35", competitors=[])
    assert meta.status == TaskStatus.failed
    assert "无可用竞品" in (meta.error or "")


class _FakeToday:
    """monkeypatch service.runner.date 的替身：today() 固定返回 2026-09-04（周五）。"""
    @staticmethod
    def today():
        return date(2026, 9, 4)


def test_runner_default_period_is_last_completed_week(tmp_path, monkeypatch):
    """period 缺省 = 最近已结束 ISO 周（对齐 cron 周一 09:00 报上周）；today=周五 → 2026-W35。"""
    monkeypatch.setattr(runner_mod, "date", _FakeToday)
    ctx = _ctx(tmp_path)
    meta = ctx.runner.execute_task("t-def", competitors=["crm_alpha"])
    assert meta.period == "2026-W35" and meta.status == TaskStatus.completed


# ---------------------------------------------------------------- 故障演练 A：run 级失败隔离

def test_fault_drill_run_level_isolation(tmp_path, monkeypatch):
    """单个竞品 run_cycle 抛错 → 该 run failed+error，健康竞品照常 done；Task=partial；run_failed 告警。"""
    ctx = _ctx(tmp_path)
    orig = runner_mod.run_cycle

    def flaky(settings, competitor_id, period, **kw):
        if competitor_id == "crm_alpha":
            raise RuntimeError("采集器全挂(模拟故障)")
        return orig(settings, competitor_id, period, **kw)

    monkeypatch.setattr(runner_mod, "run_cycle", flaky)
    meta = ctx.runner.execute_task("t-drill-a", period="2026-W35")
    assert meta.status == TaskStatus.partial  # 部分 run 失败 = partial，不是整个 Task failed
    runs = {r.competitor_id: r for r in meta.runs}
    assert runs["crm_alpha"].status == RunStatus.failed
    assert "RuntimeError" in (runs["crm_alpha"].error or "")
    assert runs["bi_beta"].status == RunStatus.done and runs["bi_beta"].verdict == "PASS"
    assert meta.summary["done"] == 1 and meta.summary["failed"] == 1
    fails = [a for a in ctx.list_alerts() if a["event_type"] == "run_failed"]
    assert len(fails) == 1 and fails[0]["competitor_id"] == "crm_alpha"


# ---------------------------------------------------------------- 故障演练 B：注入假事件 → 门卫拦截 → 收件箱

def test_fault_drill_bad_writer_human_inbox_and_alert(tmp_path, monkeypatch):
    """注入'真 fp 拼编造 URL'的假 writer → 门卫 human 拦下、落人工收件箱、发 human_inbox 告警。"""
    ctx = _ctx(tmp_path)

    def fake_writer(state: dict, ctx=None) -> dict:
        return {"draft": {"competitor_id": state["competitor_id"], "period": state["period"],
                          "headline_count": 1, "sections": {"adds": 1, "changes": 0, "removes": 0},
                          "items": [_item(title="真事件换了个编造出处", fp=V12_FP,
                                          urls=("https://fake.example/x",))]}}

    monkeypatch.setitem(graph_mod.NODE_IMPLS, "writer", fake_writer)
    meta = ctx.runner.execute_task("t-drill-b", period="2026-W35", competitors=["crm_alpha"])
    r = meta.runs[0]
    assert r.status == RunStatus.done and r.verdict == "human"   # Task 正常执行完，但门卫转人工
    assert r.inbox_count == 1 and r.gate_problems == 1
    assert UNSOURCED_URL in {e["code"] for e in r.human_inbox}
    hs = [a for a in ctx.list_alerts() if a["event_type"] == "human_inbox"]
    assert len(hs) == 1 and hs[0]["competitor_id"] == "crm_alpha"


# ---------------------------------------------------------------- 调度：cron 注册 + 手动触发

def test_weekly_cron_fires_monday_0900(tmp_path):
    """调度默认 cron 的触发点 = 每周一 09:00（真从 config 读 + 走 weekly_report job 注册路径）。

    口径坑（实测锁死）：APScheduler 的 CronTrigger 数值 day-of-week 是 0=周一、最大 6 ——
    与标准 cron（1=周一、0=周日）不同，故 config 用名字 "mon" 免歧义；若有人改回 "0 9 * * 1"
    这里会真触发到周二，本测试会红（§5.9"定时可演示"不许静默漂移）。
    """
    ctx = _ctx(tmp_path)
    assert ctx.settings.schedule.cron == "0 9 * * mon"     # 锁死配置默认（名字，不歧义）
    sched = schedule_weekly_report(ctx)                     # 真注册路径（不 start 不真等）
    job = sched.get_job("weekly_report")
    assert job is not None
    # 周一 08:00 起等下一个触发 → 当天 09:00
    nxt = job.trigger.get_next_fire_time(None, datetime(2026, 9, 7, 8, 0))
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 9, 7, 9, 0)
    # 周一 09:00 之后触发 → 下周一 09:00
    nxt2 = job.trigger.get_next_fire_time(None, datetime(2026, 9, 7, 9, 1))
    assert (nxt2.year, nxt2.month, nxt2.day, nxt2.hour, nxt2.minute) == (2026, 9, 14, 9, 0)


def test_scheduler_registers_weekly_job_and_run_now(tmp_path):
    """build_scheduler 注册 weekly_report job（不 start 不真等）；run_now 手动触发立即执行。"""
    ctx = _ctx(tmp_path)
    sched = build_scheduler(ctx)
    job = sched.get_job("weekly_report")
    assert job is not None and job.name.startswith("weekly_report(")
    assert job.id == "weekly_report"

    meta = run_now(ctx.runner, period="2026-W35")  # 手动触发 = demo/演示路径，不等周一
    assert meta.status == TaskStatus.completed and meta.summary["done"] == 2
    assert ctx.task_store.get_task(meta.task_id) is not None


def test_data_layer_isolation_between_ctxs(tmp_path):
    """两个 ctx 数据根隔离：互不写穿（memory/pipeline/service 都收在各自 data_dir）。"""
    a = _ctx(tmp_path / "a")
    b = _ctx(tmp_path / "b")
    a.runner.execute_task("t-a", period="2026-W35", competitors=["crm_alpha"])
    assert b.task_store.list_tasks() == []                      # b 不受 a 影响
    assert not (b.settings.data_path / "pipeline").exists()
    assert len(list((a.settings.data_path / "pipeline").glob("*.json"))) == 1
