"""编排层（Phase 4 LangGraph StateGraph 落地）。

Planner → Researchers(复用 collectors 并发) → Dedupe/Diff → Analyst(薄) → Writer(初稿) → Reviewer(门卫+条件边)。
面试要点：编排 = 节点 + 条件边 + 共享可序列化 State；门卫打回是条件边，流程可循环/可中断/可人工介入。
Analyst/Writer/Reviewer 本期是"结构真、逻辑薄"的节点，P5/P6 把内部做实、图不动。

对外入口：Planner / PeriodRunState / build_graph / run_cycle / demo_two_weeks
"""
from orchestration.graph import build_graph
from orchestration.pipeline import demo_two_weeks, run_cycle
from orchestration.planner import Planner
from orchestration.state import PeriodRunState

__all__ = ["Planner", "PeriodRunState", "build_graph", "run_cycle", "demo_two_weeks"]
