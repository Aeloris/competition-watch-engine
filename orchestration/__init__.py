"""编排层（Phase 4 LangGraph StateGraph 落地）。

Planner → Researchers(fan-out) → Dedupe/Diff → Analyst → Writer → Reviewer(门卫+打回边) → Reporter/Alert。
面试要点：编排 = 节点 + 条件边 + 共享可序列化 State；门卫打回是条件边，流程可循环/可中断/可人工介入。
"""
from orchestration.planner import Planner

__all__ = ["Planner"]
