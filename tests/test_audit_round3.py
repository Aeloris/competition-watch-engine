# -*- coding: utf-8 -*-
"""最后一轮全面审计（Round 3）回归：把这一轮修的真实缺陷逐条锁死，防回潮。

修复清单（每测对应一条，均为此前未被测试覆盖的真实代码路径缺陷）：
1. **采集断档不覆盖上期快照 + 不冒充 PASS**：全源失败/零卡时 dedupe_diff_node 不再把上期
   Snapshot 冲成空（否则下期把旧闻再当"新增"），并打 collection_gap 让 runner 记 failed。
2. **runner 告警隔离**：notifier 投递抛非 NotImplementedError（台账 IO/网络）不再把一个
   跑成功的 run 翻案成 failed、也不在失败路径把整个 Task 卡在 running（_alert 吞全异常）。
3. **JsonMemoryStore 原子写 + 损坏容忍**：save 原子化（tmp+os.replace），latest_snapshot /
   list_fact_cards 读到坏文件按"缺失"降级，不炸下游 diff；下次 save 自愈。
4. **TaskStore / 台账读容忍**：get_task 读损坏任务文件按不存在（404），read_alerts 读损坏
   台账返回空并自愈——与 list_tasks 已跳损坏文件的处理口径一致。
5. **版本锚 NFKC**：全角 "Ｖ１２．０" 归一成 "12.0"，与 ASCII 写法的同发布合并（不拆两条）。
6. **纯品牌名标题不乱并**：标题剥完别名是空的（只有公司名）不再一律并——两条无关报道
   不再合成一条假事件；仅标题完全相同才算重复。
7. **POST /tasks period 提前校验**：周号越界（2026-W99）请求期 422，不再 201 + 注定失败的 task。
8. **部分采集失败可见**：done run 也记录 collect_failures（面板可查，别把"缺数据"当"没问题"）。

诚实口径：eval 门禁（事件一致/编造拦截/来源/结构/trace/真实误拦）的 fixture 从不触发 1–8，
故以下测试不改任何门禁数字，纯属加回归。
"""
from __future__ import annotations

import pytest
from datetime import datetime
from pydantic import ValidationError

from app.routers.panel import _entry_html
from app.routers.tasks import TaskRequest
from dedupe import merge_to_events
from memory.schemas import FactCard, Snapshot
from memory.store import JsonMemoryStore
from orchestration.nodes import NodeContext, dedupe_diff_node
from service import build_service_context
from service.notifier import Notifier
from service.schemas import RunMeta, RunStatus, TaskStatus
from service.store import read_alerts

import service.runner as runner_mod


def _ctx(tmp_path, notifier=None):
    return build_service_context(data_dir=str(tmp_path), notifier=notifier)


class _BoomNotifier(Notifier):
    """deliver 抛非 NotImplementedError 异常（台账 IO/真网络故障一类）的替身。"""

    def __init__(self):
        self.deliver_calls = 0

    def deliver(self, event) -> dict:  # noqa: ARG002
        self.deliver_calls += 1
        raise OSError("告警台账写入失败(模拟磁盘 IO 故障)")

    def list_log(self) -> list[dict]:
        return []


def _card(title: str, url: str, *, at=None, comp: str = "crm_alpha") -> FactCard:
    ts = at or datetime(2026, 8, 24, 9, 0)
    return FactCard(competitor_id=comp, source_url=url, title=title,
                    summary="", published_at=ts, fetched_at=ts)


# ===========================================================================
# 1) 采集断档：不覆盖上期快照 + collection_gap 标记（节点层）
# ===========================================================================


def test_collection_gap_does_not_wipe_previous_snapshot(tmp_path):
    """全源失败 + 零卡 → 不再把上期快照冲空（防旧闻下期再当新增）；标记 collection_gap。"""
    ctx = _ctx(tmp_path)
    store = JsonMemoryStore(str(tmp_path / "mem"))
    store.save_snapshot(Snapshot(period="2026-W34", competitor_id="crm_alpha",
                                 event_fps=["fpA"], event_digests={"fpA": "digA"}))
    nctx = NodeContext.build(ctx.settings, store=store)

    state_gap = {
        "competitor_id": "crm_alpha", "period": "2026-W35", "cards": [],
        "collect_summary": {"failures": ["website/crm_alpha: 信源全挂"], "by_source": {}, "cards": 0},
    }
    out = dedupe_diff_node(state_gap, nctx)
    assert out["collection_gap"] is True
    assert out["trace"]["collected"] == 0 and out["trace"]["merged"] == 0
    latest = store.latest_snapshot("crm_alpha")
    assert latest is not None and latest.period == "2026-W34"  # 上期锚还在，未被冲空


def test_genuinely_quiet_week_still_writes_empty_snapshot(tmp_path):
    """正常空期（无失败、零卡 = 世界真没变化）照常写空快照，与"断档"区分开。"""
    ctx = _ctx(tmp_path)
    store = JsonMemoryStore(str(tmp_path / "mem"))
    store.save_snapshot(Snapshot(period="2026-W34", competitor_id="crm_alpha",
                                 event_fps=["fpA"], event_digests={"fpA": "digA"}))
    nctx = NodeContext.build(ctx.settings, store=store)
    state_quiet = {
        "competitor_id": "crm_alpha", "period": "2026-W35", "cards": [],
        "collect_summary": {"failures": [], "by_source": {}, "cards": 0},
    }
    out = dedupe_diff_node(state_quiet, nctx)
    assert out["collection_gap"] is False
    assert store.latest_snapshot("crm_alpha").period == "2026-W35"  # 空快照已落盘（区分断档）


# ===========================================================================
# 2) 告警投递失败：不翻案成功 run、不把 Task 卡在 running（runner 层）
# ===========================================================================


def test_alert_failure_does_not_flip_done_run_or_hang_task(tmp_path):
    """健康 run 但 notifier.deliver 抛 OSError → run 仍是 done（不翻案 failed）、Task completed、
    不因告警把整个 Task 卡在 running（旧实现：失败路径的 self._alert 再抛 → execute_task 中断）。"""
    boom = _BoomNotifier()
    ctx = _ctx(tmp_path, notifier=boom)
    meta = ctx.runner.execute_task("t-alert-boom", period="2026-W35", competitors=["crm_alpha"])
    assert meta.status == TaskStatus.completed            # 不卡 running，正常收口
    r = meta.runs[0]
    assert r.status == RunStatus.done and r.verdict == "PASS"
    assert r.error is None                                # 没被"告警失败"污染成 failed
    assert r.collect_failures == 0
    assert boom.deliver_calls >= 1                        # high_threat 告警确实尝试过 → 被吞（修复 #6 生效）
    assert ctx.list_alerts() == []                        # 告警尽力而为：投递失败即跳过，不落台账


def test_alert_failure_on_failed_run_still_finalizes_task(tmp_path, monkeypatch):
    """单竞品 run_cycle 真失败 + 告警也抛 → Task 仍收口（partial），不再把剩余竞品中断、留在 running。"""
    ctx = _ctx(tmp_path, notifier=_BoomNotifier())
    orig = runner_mod.run_cycle

    def flaky(settings, competitor_id, period, **kw):
        if competitor_id == "crm_alpha":
            raise RuntimeError("采集器全挂(模拟)")
        return orig(settings, competitor_id, period, **kw)

    monkeypatch.setattr(runner_mod, "run_cycle", flaky)
    meta = ctx.runner.execute_task("t-drill-alert", period="2026-W35")
    assert meta.status == TaskStatus.partial              # 失败路径的告警异常不再中止整 Task
    assert meta.finished_at is not None                   # _finalize 跑到了
    runs = {r.competitor_id: r for r in meta.runs}
    assert runs["crm_alpha"].status == RunStatus.failed
    assert runs["bi_beta"].status == RunStatus.done       # 健康竞品照常出结果


# ===========================================================================
# 1b) 采集断档在 runner 层 → 记 failed（不产空周报冒充 PASS）
# ===========================================================================


def test_collection_gap_run_marks_failed_not_clean_pass(tmp_path, monkeypatch):
    """run_cycle 返回 collection_gap=True（全源失败零卡）→ runner 记 failed + 人话 error，
    而不是用空周报发一个干净 PASS。"""
    ctx = _ctx(tmp_path)

    def gap_final(settings, competitor_id, period, **kw):
        return {
            "collection_gap": True,
            "competitor_id": competitor_id, "period": period,
            "trace": {"collected": 0, "merged": 0, "diff": 0},
            "collect_summary": {"failures": [f"website/{competitor_id}: 全挂"], "by_source": {}, "cards": 0},
            "cards": [], "events": [], "changes": [],
            "diff_summary": {"adds": 0, "changes": 0, "removes": 0, "unchanged": 0},
            "draft": {}, "gate": {"verdict": "PASS"}, "gate_trace": [], "human_inbox": [],
        }

    monkeypatch.setattr(runner_mod, "run_cycle", gap_final)
    meta = ctx.runner.execute_task("t-gap", period="2026-W35", competitors=["crm_alpha"])
    r = meta.runs[0]
    assert r.status == RunStatus.failed
    assert "采集全源失败" in (r.error or "") and "快照已保留" in (r.error or "")
    assert meta.status == TaskStatus.failed
    fails = [a for a in ctx.list_alerts() if a["event_type"] == "run_failed"]
    assert len(fails) == 1 and fails[0]["competitor_id"] == "crm_alpha"


# ===========================================================================
# 3) JsonMemoryStore：原子写 + 损坏容忍（读不炸、写自愈）
# ===========================================================================


def test_json_store_atomic_write_no_tmp_leftover(tmp_path):
    store = JsonMemoryStore(str(tmp_path / "mem"))
    store.save_snapshot(Snapshot(period="2026-W35", competitor_id="crm_alpha",
                                 event_fps=["f"], event_digests={"f": "d"}))
    assert store.latest_snapshot("crm_alpha").period == "2026-W35"
    # 原子写：不留半截 .tmp（旧实现 write_text 崩溃即产半截文件，也没有 tmp+replace）
    assert not list((tmp_path / "mem").rglob("*.tmp"))


def test_json_store_corrupt_reads_do_not_raise_and_self_heal(tmp_path):
    store = JsonMemoryStore(str(tmp_path / "mem"))
    store.save_snapshot(Snapshot(period="2026-W36", competitor_id="crm_alpha",
                                 event_fps=["f"], event_digests={"f": "d"}))
    p = tmp_path / "mem" / "snapshots" / "crm_alpha" / "2026-W36.json"
    p.write_text("{broken", encoding="utf-8")          # 外部手改/半截
    assert store.latest_snapshot("crm_alpha") is None  # 不抛 → 冷启动降级，而不是炸掉下游 diff
    store.save_snapshot(Snapshot(period="2026-W37", competitor_id="crm_alpha",
                                 event_fps=[], event_digests={}))
    assert store.latest_snapshot("crm_alpha").period == "2026-W37"  # 原子写覆盖坏文件 → 自愈
    # fact_cards 损坏按 [] 读
    fc = tmp_path / "mem" / "fact_cards" / "crm_alpha" / "2026-W35.json"
    fc.parent.mkdir(parents=True, exist_ok=True)
    fc.write_text("[{bad", encoding="utf-8")
    assert store.list_fact_cards("crm_alpha", "2026-W35") == []


# ===========================================================================
# 4) service 读路径容忍：get_task / read_alerts 与 list_tasks 同口径
# ===========================================================================


def test_get_task_corrupt_returns_none_not_500(tmp_path):
    ctx = _ctx(tmp_path)
    ts = ctx.task_store
    ts.create_task("t-ok", period="2026-W35", competitors_requested=["crm_alpha"],
                   created_at="2026-09-01T09:00:00+00:00")
    bad = tmp_path / "service" / "tasks" / "t-bad.json"
    bad.write_text("{broken", encoding="utf-8")
    assert ts.get_task("t-bad") is None                # 损坏任务文件按不存在（404），不 500
    assert ts.get_task("t-ok") is not None
    assert ts.get_task("t-bad") is None                # list_tasks 本来就跳过它，口径一致


def test_read_alerts_corrupt_returns_empty_and_self_heals(tmp_path):
    ctx = _ctx(tmp_path)
    from service.store import alerts_path
    alerts_path(ctx.settings).parent.mkdir(parents=True, exist_ok=True)
    alerts_path(ctx.settings).write_text("[{broken", encoding="utf-8")
    assert ctx.list_alerts() == []                     # GET /alerts 不 500
    from service.notifier import make_alert
    ev = make_alert("run_failed", summary="s", competitor_id="crm_alpha", period="2026-W35")
    ctx.notifier.deliver(ev)                           # 追加时读到损坏→空→整体重写 = 自愈
    log = read_alerts(ctx.settings)
    assert len(log) == 1 and log[0]["event_type"] == "run_failed"


# ===========================================================================
# 5) 版本锚 NFKC：全角写法与 ASCII 同发布合并
# ===========================================================================


def test_version_anchor_nfkc_full_width():
    from dedupe.merge import version_anchor
    assert version_anchor("Alpha CRM v12.0 正式发布") == "12.0"
    assert version_anchor("Alpha CRM v１２．０ 正式发布") == "12.0"  # 旧实现 → None


def test_full_width_version_release_merges_with_ascii_sibling():
    cards = [
        _card("Alpha CRM v12.0 正式发布", "https://a.example/blog/v12"),
        _card("Alpha CRM v１２．０ 正式发布", "https://b.example/news/v12"),
    ]
    events, _ = merge_to_events(cards, period="2026-W35")
    assert len(events) == 1  # 同一发布（全角版）+ 正常版 → 并入一条；旧实现拆成两条


# ===========================================================================
# 6) 纯品牌名标题不乱并（仅在标题完全相同时算重复）
# ===========================================================================


def test_brand_only_titles_do_not_collapse_distinct_posts():
    cards = [
        _card("Alpha CRM", "https://a.example/one"),
        _card("阿尔法 CRM", "https://b.example/two"),
    ]
    aliases = {"crm_alpha": ["alphacrm", "阿尔法crm"]}  # 别名覆盖两种写法 → 剥完都是空
    events, summary = merge_to_events(cards, period="2026-W35", aliases=aliases)
    assert len(events) == 2 and summary.merges == 0  # 无关报道不合成一条假事件


def test_brand_only_identical_titles_still_dedupe():
    cards = [
        _card("Alpha CRM", "https://a.example/one"),
        _card("Alpha CRM", "https://b.example/two"),
    ]
    events, summary = merge_to_events(cards, period="2026-W35",
                                      aliases={"crm_alpha": ["alphacrm", "阿尔法crm"]})
    assert len(events) == 1 and summary.merges == 1  # 标题完全相同 → 真重复照并


# ===========================================================================
# 7) period 请求期校验（422 而非 201 + 注定失败）
# ===========================================================================


def test_task_request_rejects_out_of_range_period():
    TaskRequest(period=None)                  # 缺省合法
    TaskRequest(period="2026-W35")            # 合法 ISO 周
    with pytest.raises(ValidationError):      # 格式对但周号越界 → 422
        TaskRequest(period="2026-W99")
    with pytest.raises(ValidationError):
        TaskRequest(period="not-a-period")


# ===========================================================================
# 8) collect_failures 字段可见（部分失败 done run 不装没事）
# ===========================================================================


def test_run_meta_collect_failures_default_and_passthrough(tmp_path):
    assert RunMeta(run_id="r", competitor_id="c", period="2026-W35").collect_failures == 0
    ctx = _ctx(tmp_path)
    meta = ctx.runner.execute_task("t-cf", period="2026-W35", competitors=["crm_alpha"])
    assert meta.runs[0].collect_failures == 0      # 健康 run：0 失败源，面板 failures=0


# ===========================================================================
# 9) 面板 entry_id 不再拼进 JS 字符串字面量（data-eid 属性承载）
# ===========================================================================


def test_panel_entry_html_does_not_inline_entry_id_into_js():
    e = {"entry_id": "r1'#0", "code": "UNSOURCED_URL", "status": "pending",
         "fp": "fp-x", "item_index": 1, "attempts": 0,
         "detail": "<script>x</script>", "resolution": None}
    html = _entry_html(e)
    assert "resolve('r1'#0'" not in html            # 没有把裸 entry_id 拼进 JS
    assert "data-eid=\"" in html                    # 改为 data 属性承载（quote=True 全转义）
    assert "&#x27;" in html                         # 单引号已被转义
    assert "<script>" not in html                   # detail 等字段 html.escape
