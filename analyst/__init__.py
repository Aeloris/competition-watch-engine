"""分析层（Phase 5 Analyst）。

维度归类 + 威胁分级（高/中/低）用 rubric：规则先命中（价格/上线/融资关键词保稳定），
LLM 补语义（覆盖规则外）；输出带理由、可解释（README2 §5.6）。
"""
from analyst.classifier import Analyst

__all__ = ["Analyst"]
