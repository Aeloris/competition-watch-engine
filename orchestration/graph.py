# -*- coding: utf-8 -*-
"""编排状态机（Phase 4 主菜）：用 LangGraph StateGraph 把七个节点接成一张有向图。

图结构（对齐 README2 §4.2/§4.3 主流程，落地版）：
    planner → researchers → dedupe_diff → analyst → writer → reviewer
    reviewer →(条件边)→ writer（REWRITE，额度内打回改写） 或 → END（PASS / human）

三个"为什么选状态机而非脚本"（面试讲这三点）：
1. **共享可序列化 State 传数据**：节点函数 (state)->部分更新，state 是 JSON-safe 字典 →
   可落盘（文件即状态）、可 checkpoint（MemorySaver 按 thread_id 查/续跑）；
2. **条件边让流程可循环、可中断、可人工介入**：门卫打回 Writer 是一条边、额度用尽是"转人工"
   不是"硬放行"——这就是"先审后发"在编排层的形状；
3. **节点边界 = 可插桩**：每个节点独立可测、可替换（P5/P6 把薄节点内部做实，图不动）。

注意：LangGraph 是图运行时不是 LLM——七个节点大多是纯 Python（复用 P1–P3 已测库），
只有 P5/P6 节点内部才需要接 Mock LLM。因此全 mock 下编排层也能被确定性测死。
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from orchestration.nodes import (
    NodeContext,
    analyst_node,
    dedupe_diff_node,
    planner_node,
    researchers_node,
    reviewer_node,
    writer_node,
)
from orchestration.state import PeriodRunState

NODE_IMPLS = {
    "planner": planner_node,
    "researchers": researchers_node,
    "dedupe_diff": dedupe_diff_node,
    "analyst": analyst_node,
    "writer": writer_node,
    "reviewer": reviewer_node,
}


def route_after_review(state: dict) -> str:
    """reviewer 后的条件边：REWRITE → 回 writer（额度内打回改写）；PASS / human → END。"""
    gate = state.get("gate") or {}
    return "writer" if gate.get("verdict") == "REWRITE" else END


def build_graph(settings, *, store=None):
    """组装并编译编排状态机。store 可注入（测试传临时目录后端，默认 data/memory）。"""
    ctx = NodeContext.build(settings, store=store)

    def bind(node):
        def wrapped(state: dict) -> dict:
            return node(state, ctx)

        return wrapped

    g = StateGraph(PeriodRunState)
    for name, impl in NODE_IMPLS.items():
        g.add_node(name, bind(impl))
    g.set_entry_point("planner")
    g.add_edge("planner", "researchers")
    g.add_edge("researchers", "dedupe_diff")
    g.add_edge("dedupe_diff", "analyst")
    g.add_edge("analyst", "writer")
    g.add_edge("writer", "reviewer")
    g.add_conditional_edges("reviewer", route_after_review)  # 无显式 path_map → 返回值即节点名/END
    return g.compile(checkpointer=MemorySaver())
