"""门卫层（Phase 6 Reviewer，与普通工具的分水岭）。

逐条/整份判：来源支撑度(grounding) + 矛盾检测 + 口径/结构合规；
不合格打回 Writer 改写（限次），修不好 → 低置信人工收件箱，人工确认才放行。
"""
from reviewer.gate import Reviewer

__all__ = ["Reviewer"]
