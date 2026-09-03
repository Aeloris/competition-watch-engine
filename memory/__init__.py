"""记忆层（Phase 3 Memory/Diff，项目灵魂）。

事件指纹 fp = hash(竞品, 维度, 规范化标题) 沉淀为周期快照；跨期 diff → 新增/变更/消失。
价值：能回答"本周到底变了什么" + 相同事件不重复分析（省 token）。
"""
from memory.store import MemoryStore

__all__ = ["MemoryStore"]
