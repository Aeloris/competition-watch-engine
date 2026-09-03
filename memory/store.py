# -*- coding: utf-8 -*-
"""MemoryStore 占位类（Phase 3 实现）。

职责：持久化周期快照（event_fps）+ 跨周期 diff（本期 vs 上期 → 新增/变更/消失），
快照存 json/sqlite（结构化、可查、可回放），语义聚类才用向量库（README2 §4.4 / §5.5）。
"""


class MemoryStore:
    """周期快照读写 + 变更检测引擎（Phase 3 实现）。"""

    # 输入：本期规范化事件 + 上期快照；输出：变更事件列表（add/change/remove）+ 写入本期快照。
    pass
