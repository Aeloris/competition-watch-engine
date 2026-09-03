"""分析层（Phase 5 Analyst 落地）。

威胁分级（高/中/低 + 理由链）用 **rubric 规则先命中**（价格/上线/融资等强信号保稳定），
输出带理由、可解释；LLM 补语义（覆盖规则外异词同威胁）等真 provider 联调再接，本期不假装。
只读 Event.dimension（Phase 3 已冻结进 fp），绝不改维度。
"""
from analyst.classifier import Analyst
from analyst.rubric import RubricRule, ThreatAssessment, ThreatRubric

__all__ = ["Analyst", "ThreatRubric", "ThreatAssessment", "RubricRule"]
