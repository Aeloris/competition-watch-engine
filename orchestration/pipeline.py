# -*- coding: utf-8 -*-
"""编排运行入口（Phase 4）：run_cycle 跑一个竞品一个周期；demo_two_weeks 跨两期回放。

- run_cycle：初始化 State → graph.invoke → 返回最终 state（含 trace N→M→K 与 gate 判定）。
  依赖可注入（store 传临时后端 = 测试隔离；fetch_at/run_id 注入 = 回放确定性）。
  persist=True 时把最终 state 落 data/pipeline/{run_id}.json（文件即状态，P7/P8 审计/回放对象）。
- demo_two_weeks：对两竞品先跑 W34（冷启动 → 快照）再 W35（真 diff），是"跨期找变化"的可演示样本：
  第二期是 diff 不是全量（trace.merged / diff 与 P3 fixtures 真值一致），gate 全 PASS。

诚实口径：单次 run_cycle 若上期无快照 → 冷启动（本期事件全 add）；要看到"新增/变更/消失"的
跨期语义，必须先跑过上期（写档）。这正是记忆（Snapshot）存在的意义。
"""
from __future__ import annotations

import json
from datetime import datetime

from config.settings import Settings, get_settings
from memory import MemoryStore
from orchestration.graph import build_graph
from orchestration.planner import Planner

# 两竞品两周：W34 = 冷启动底座，W35 = diff 目标期（fixtures 话题刻意不重叠 → W35 全新增）
DEMO_COMPETITORS = ("crm_alpha", "bi_beta")
DEMO_PERIODS = ("2026-W34", "2026-W35")


def run_cycle(
    settings: Settings | None = None,
    competitor_id: str = "crm_alpha",
    period: str = "2026-W35",
    *,
    store: MemoryStore | None = None,
    run_id: str | None = None,
    fetched_at: datetime | None = None,
    persist: bool = False,
) -> dict:
    """跑一个竞品的一个周期：plan → 采集 → 去重/diff → analyst → writer → reviewer → 最终 State。"""
    settings = settings or get_settings()
    app = build_graph(settings, store=store)

    plan = Planner(settings).plan(competitor_id, period, fetched_at=fetched_at, run_id=run_id)
    init_state = {
        "competitor_id": competitor_id,
        "period": period,
        "fetched_at": plan["fetched_at"],
        "run_id": plan["run_id"],
    }
    final = app.invoke(init_state, config={"configurable": {"thread_id": plan["run_id"]}})

    if persist:
        out_dir = settings.data_path / "pipeline"
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{plan['run_id']}.json").write_text(
            json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return final


def demo_two_weeks(settings: Settings | None = None, *, store: MemoryStore | None = None) -> list[dict]:
    """两竞品 × (W34 冷启动 → W35 diff)：打印 trace 计数链与门卫判定，返回每期结果供断言/展示。"""
    settings = settings or get_settings()
    results: list[dict] = []
    for comp in DEMO_COMPETITORS:
        for period in DEMO_PERIODS:
            final = run_cycle(settings, comp, period, store=store, persist=True)
            results.append(final)
            trace, gate = final.get("trace", {}), (final.get("gate") or {})
            draft = final.get("draft", {})
            radar = draft.get("threat_radar", {})
            print(
                f"{comp} {period}: "
                f"trace(collected={trace.get('collected')} → merged={trace.get('merged')} "
                f"→ diff={trace.get('diff')}) · "
                f"draft.sections={draft.get('sections')} · "
                f"threat_radar={radar} · "
                f"gate={gate.get('verdict')} · "
                f"gate_trace={len(final.get('gate_trace') or [])} · human_inbox={len(final.get('human_inbox') or [])}"
            )
    return results
