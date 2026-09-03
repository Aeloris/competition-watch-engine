"""门卫层（Phase 6 Reviewer，与普通工具的分水岭）。

逐条/整份判：grounding（来源闭环自证）+ 矛盾检测 + 口径/结构合规；
结构性缺口 → 打回 Writer 改写（限次）；信任类问题 → 低置信转人工收件箱，
人工确认才放行（README2 §5.8 / §7.4 row 6）。判定细节见 docs/reviewer.md。
"""
from reviewer.gate import POLARITY_AXES, Reviewer
from reviewer.problems import (
    CONTRADICTION,
    DUPLICATE,
    EMPTY_REASONS,
    EMPTY_TITLE,
    MISSING_DIMENSION,
    MISSING_FP,
    MISSING_SEVERITY,
    NO_EVIDENCE,
    STRUCTURAL_CODES,
    TRUST_CODES,
    UNGROUNDED_CLAIM,
    UNGROUNDED_FP,
    UNSOURCED_URL,
)

__all__ = [
    "Reviewer",
    "POLARITY_AXES",
    "STRUCTURAL_CODES",
    "TRUST_CODES",
    "NO_EVIDENCE",
    "EMPTY_TITLE",
    "MISSING_DIMENSION",
    "MISSING_SEVERITY",
    "EMPTY_REASONS",
    "MISSING_FP",
    "UNSOURCED_URL",
    "UNGROUNDED_FP",
    "UNGROUNDED_CLAIM",
    "CONTRADICTION",
    "DUPLICATE",
]
