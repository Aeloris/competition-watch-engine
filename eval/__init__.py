"""评测层（Phase 8 EvalHarness，与项目一同款"用数据说话"）。

用历史真实事件集回放，测 事件召回率 / 来源准确率 / 编造拦截率 / 去重准确率 / 结构合规率。
简历百分比只从本 harness 实测回填，绝不编造（README2 §5.11）。
"""
from eval.harness import EvalHarness

__all__ = ["EvalHarness"]
