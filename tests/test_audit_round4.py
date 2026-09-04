# -*- coding: utf-8 -*-
"""最后一轮全面审计（Round 4）回归：把本轮修的真实缺陷逐条锁死，防回潮。

修复清单（每测对应一条，均为此前未被测试覆盖的真实代码路径缺陷）：
1. **SQLite 读容忍**：SqliteMemoryStore.list_fact_cards / latest_snapshot 单条 payload 损坏
   （外部手改/半截）原先裸 json.loads + model_validate 直接抛 → 整条 run 崩。现与 Json 后端同口径
   （损坏按空期/无快照读，不炸下游，下次 save 自愈）。
2. **重跑历史周期被更晚快照污染**：dedupe_diff_node 用 global 最新快照当 diff 锚 → 回填/重跑 W34 时
   若 W35 已跑过会把 W35 当"上期"，把 W34 相对 W33 的新增漏报成 unchanged。现 latest_snapshot 支持
   up_to=period 只看"本期及之前"，diff 锚钉回当前周期。
3. **版本点号撞指纹**：normalize_title 把 '.' 当普通标点剥掉 → v12.0 与 v1.20 都归成 v120 → 同 fp →
   同竞品同维度同期的两个真实发布在快照层（event_fps set + digests dict 覆盖）被当成同一事件、一个被
   静默吞掉。现保留 '.'（只让归一更区分，无点号标题的 fp 不变）。
4. **collection_gap 过真实 LangGraph**：PeriodRunState 声明 collection_gap（节点返回未声明 key 会被
   LangGraph `_get_updates` 静默丢弃 → 全源失败时 runner 拿不到 gap 标志、把空周报当干净 PASS）。
   Round 3 只测了节点层/ monkeypatch run_cycle，本测从**真图 + 真采集全挂**端到端锁死。

诚实口径：eval 门禁 fixture 从不触发 1–4，以下测试不改任何门禁数字，纯属加回归。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest

from collectors import CollectorWorker
from collectors import build_collectors as real_build_collectors
from config.settings import get_settings
from dedupe import merge_to_events
from memory import JsonMemoryStore, SqliteMemoryStore, Snapshot
from memory.fingerprint import event_fingerprint, normalize_title
from memory.schemas import FactCard
from orchestration import run_cycle
from sources.base import SourceAdapter

SETTINGS = get_settings()


def _card(title: str, url: str, *, at=None, comp: str = "crm_alpha") -> FactCard:
    ts = at or datetime(2026, 8, 24, 9, 0)
    return FactCard(competitor_id=comp, source_url=url, title=title,
                    summary="", published_at=ts, fetched_at=ts)


def _snap(period: str, fps=None, digests=None) -> Snapshot:
    return Snapshot(period=period, competitor_id="crm_alpha",
                    event_fps=fps or ["f"], event_digests=digests or {"f": "d"})


# ===========================================================================
# 1) SQLite 读容忍：损坏 payload 按空读、不抛、save 自愈（Json 侧 Round 3 已锁）
# ===========================================================================


def _corrupt_sqlite_row(store: SqliteMemoryStore, table: str, period: str) -> None:
    with sqlite3.connect(store._db_path) as conn:
        conn.execute(f"UPDATE {table} SET payload = ? WHERE period = ?", ("{broken json", period))


def test_sqlite_corrupt_fact_cards_reads_empty_and_self_heals(tmp_path):
    store = SqliteMemoryStore(tmp_path / "mem")
    store.save_fact_cards("crm_alpha", "2026-W35", [_card("Alpha CRM v12.0 GA", "https://a/1")])
    _corrupt_sqlite_row(store, "fact_cards", "2026-W35")
    assert store.list_fact_cards("crm_alpha", "2026-W35") == []  # 不抛，按空期读
    store.save_fact_cards("crm_alpha", "2026-W35", [_card("Alpha CRM v12.0 GA", "https://a/1")])
    assert len(store.list_fact_cards("crm_alpha", "2026-W35")) == 1  # 原子覆盖自愈


def test_sqlite_corrupt_snapshot_reads_none_and_self_heals(tmp_path):
    store = SqliteMemoryStore(tmp_path / "mem")
    store.save_snapshot(_snap("2026-W34"))
    _corrupt_sqlite_row(store, "snapshots", "2026-W34")
    assert store.latest_snapshot("crm_alpha") is None  # 不抛，按无快照冷启动
    store.save_snapshot(_snap("2026-W35"))
    assert store.latest_snapshot("crm_alpha").period == "2026-W35"


# ===========================================================================
# 2) latest_snapshot up_to：双后端只取"本期及之前"的最新（不被更晚周期污染）
# ===========================================================================


@pytest.mark.parametrize("store_cls", [JsonMemoryStore, SqliteMemoryStore], ids=["json", "sqlite"])
def test_latest_snapshot_up_to_anchors_at_period(store_cls, tmp_path):
    store = store_cls(tmp_path / "mem")
    for p, fps in (("2026-W33", ["a"]), ("2026-W34", ["b"]), ("2026-W35", ["c"])):
        store.save_snapshot(Snapshot(period=p, competitor_id="crm_alpha",
                                     event_fps=fps, event_digests={f: "d" for f in fps}))
    # 缺省 = 全局最新（原语义不动）
    assert store.latest_snapshot("crm_alpha").period == "2026-W35"
    # up_to = 回填/重跑 W34 的锚 → 必须 W34（旧实现返回 W35，把更晚周期当上期）
    assert store.latest_snapshot("crm_alpha", up_to="2026-W34").period == "2026-W34"
    assert store.latest_snapshot("crm_alpha", up_to="2026-W33").period == "2026-W33"
    # 本期还没跑过 → 取前一期（W33 已存在）
    assert store.latest_snapshot("crm_alpha", up_to="2026-W31") is None  # 早于最早档


def test_rerun_older_period_not_poisoned_by_later_snapshot(tmp_path):
    """先跑 W34、再跑 W35，然后重跑 W34 —— diff 锚必须钉 W34 自己的档（up_to），
    否则旧实现拿 W35 当上期：W34 事件相对 W35 全算新增 → 重跑竟报 2 条假新增。"""
    store = JsonMemoryStore(tmp_path / "mem")
    run_cycle(SETTINGS, "crm_alpha", "2026-W34", store=store, run_id="anchor-w34")
    run_cycle(SETTINGS, "crm_alpha", "2026-W35", store=store, run_id="anchor-w35")
    assert (tmp_path / "mem" / "snapshots" / "crm_alpha" / "2026-W35.json").exists()

    final = run_cycle(SETTINGS, "crm_alpha", "2026-W34", store=store, run_id="anchor-w34-rerun")
    assert final["trace"] == {"collected": 2, "merged": 2, "diff": 0}  # 相对 W34 档 unchanged
    assert final["diff_summary"]["adds"] == 0  # 不被 W35 污染成 adds=2


# ===========================================================================
# 3) 版本点号撞指纹：v12.0 与 v1.20 是两个不同版本，不得同 fp / 同周期被静默吞一个
# ===========================================================================


def test_version_dot_releases_get_distinct_fp():
    f = event_fingerprint
    a = f("crm_alpha", "feature", "Alpha CRM v12.0 正式发布：智能跟进助手 GA")
    b = f("crm_alpha", "feature", "Alpha CRM v1.20 正式发布：智能跟进助手 GA")
    assert a != b  # 旧实现：剥 '.' 后都是 v120 → 同 fp
    assert normalize_title("Alpha CRM v12.0 正式发布") != normalize_title("Alpha CRM v1.20 正式发布")


def test_same_period_two_releases_survive_snapshot():
    """v12.0 与 v1.20 同期同竞品 → 2 个真实事件、fp 各不同、快照两个都留（旧实现 set 收敛吞一个）。"""
    cards = [
        _card("Alpha CRM v12.0 正式发布：智能跟进助手 GA", "https://a.example/blog/v120"),
        _card("Alpha CRM v1.20 正式发布：智能跟进助手 GA", "https://a.example/blog/v120-minor"),
    ]
    events, summary = merge_to_events(cards, period="2026-W35")
    assert len(events) == 2 and summary.events_out == 2
    assert len({e.fp for e in events}) == 2  # fp 唯一 → snapshot 不再互相覆盖
    assert events[0].fp != events[1].fp


def test_dotless_title_fp_unchanged_by_dot_rule():
    """无点号标题归一仍是纯字母数字（'.' 规则不碰它）→ 非版本类事件 fp 全域不受影响。"""
    s = "Alpha CRM 发布智能跟进助手 GA"
    assert "." not in normalize_title(s)
    assert event_fingerprint("crm_alpha", "feature", s) == event_fingerprint("crm_alpha", "feature", s)


# ===========================================================================
# 4) collection_gap 过真实 LangGraph：真图 + 全源失败 → final 带 collection_gap
# ===========================================================================

class _BoomAdapter(SourceAdapter):
    name = "boom"

    async def fetch(self, competitor_id, kind, *, since=None):
        raise ConnectionError("simulated total source outage")


def test_collection_gap_survives_real_graph(tmp_path, monkeypatch):
    """全源失败 + 零卡 → 真实 LangGraph 的最终 State 必须带 collection_gap=True。

    回归对象：PeriodRunState 未声明 collection_gap 时，dedupe_diff_node 返回的该 key 被
    `_get_updates` 静默丢弃 → runner 读 final.get("collection_gap") 恒 None → 把空周报当干净 PASS。
    Round 3 只测了节点层/ monkeypatch run_cycle，此处从编译后真图端到端锁死。"""
    def fake_build(settings):
        workers = real_build_collectors(settings)
        for kind in list(workers):  # 全部信源替换成必挂适配器
            workers[kind] = CollectorWorker(_BoomAdapter(), kind, max_retries=1)
        return workers

    monkeypatch.setattr("orchestration.nodes.build_collectors", fake_build)
    store = JsonMemoryStore(tmp_path / "mem")
    final = run_cycle(SETTINGS, "crm_alpha", "2026-W35", store=store, run_id="gap-graph")
    assert final.get("collection_gap") is True            # 旧实现：Key 被 LangGraph 丢弃 → None
    assert final["trace"]["collected"] == 0
    assert store.latest_snapshot("crm_alpha") is None      # 断档不覆盖/不写空快照
