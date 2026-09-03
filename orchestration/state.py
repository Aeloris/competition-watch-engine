# -*- coding: utf-8 -*-
"""编排共享 State（Phase 4 LangGraph 落地）：一张可序列化字典在节点间流转。

LangGraph 心智模型：节点 = 函数 (state) -> 部分更新；边决定谁接谁；**共享 State 传数据**。
这里用 TypedDict(total=False) 定义字段，LangGraph 默认按 key 覆盖合并（last-write-wins）。
值全部是 JSON-safe 的 dict/list/str/int —— 状态"可序列化"是字面成立的：
最终 state 可直接 json.dumps 落盘（data/pipeline/{run_id}.json，文件即状态），也便于 checkpoint。

字段分三组（每个节点只读自己需要的、只写自己的）：
- 计划/周期：run_id / competitor_id / period / since / fetched_at / plan
- 流水线产物：collect_summary / cards / events / changes / diff_summary / removed_fps / unchanged_fps
- 分析写作与门卫：write_items（analyst 投影） / draft（writer 初稿） / gate（reviewer 判定）
- 门卫审计（Phase 6）：gate_trace（每次门卫判定逐次追加 = 打回有 trace） /
  human_inbox（转人工收件箱 = 低置信/信任问题落箱，人工确认才放行）
- 旁路计数：rewrites（已打回改写次数） / trace（采集N → 去重M → diff出K）
"""
from __future__ import annotations

from typing import TypedDict


class PeriodRunState(TypedDict, total=False):
    # —— 计划 / 周期 ——
    run_id: str                     # 本次运行的 thread/审计 id（可注入 → 确定性）
    competitor_id: str              # 盯哪个竞品（crm_alpha / bi_beta）
    period: str                     # 跑哪个周期 "YYYY-Www"（2026-W34 / 2026-W35）
    since: str                      # 增量采集锚：该周期周一起日 "YYYY-MM-DD"
    fetched_at: str                 # 注入的抓取时间（ISO）→ 整条流水线回放确定性
    plan: dict                      # planner 输出：run_id/since/fetched_at/sources_enabled

    # —— 流水线产物 ——
    collect_summary: dict           # {by_source:{kind:{fetched,cards,failed}}, cards, failures[]}
    cards: list[dict]               # 目标期 FactCards（JSON-safe；researcher 全量产出，dedupe 过滤到目标期）
    events: list[dict]              # 本期去重合并后的 Event（审计用）
    changes: list[dict]             # diff 出的 新增+变更 事件（kind=add/change，本期要下发的变更）
    diff_summary: dict              # {adds, changes, removes, unchanged, total}
    removed_fps: list[str]          # 显式撤回的 fp（本期默认空 = 绝不自动消失）
    unchanged_fps: list[str]        # 与上期相同 → 零重复计算（不下发）

    # —— 分析 / 写作 / 门卫 ——
    write_items: list[dict]         # analyst 投影："本周值得写"的变更（P5 rubric 已分级：severity + reasons[]）
    draft: dict                     # writer 初稿：周报结构（headline + sections + items）
    gate: dict                      # reviewer 判定：{verdict: PASS|REWRITE|human, reasons[], attempts, problems[]}
    rewrites: int                   # 已打回 Writer 改写次数（条件边的计数器，≤ review_max_rewrites）
    gate_trace: list[dict]          # P6：门卫判定审计迹，逐次追加 {attempt, verdict, problems[]}（打回有 trace）
    human_inbox: list[dict]         # P6：人工收件箱，{competitor/period/item_index/fp/code/detail}（转人工落箱）

    # —— 旁路计数 ——
    trace: dict                     # {collected: N, merged: M, diff: K}（README2 §5.10 计数链）
