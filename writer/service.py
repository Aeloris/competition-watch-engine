# -*- coding: utf-8 -*-
"""Writer（Phase 5 落地）：把"已验证 + 已分级"的变更条目组装成合规结构化周报。

铁律（README2 §5.7 / §1.3 步骤⑥）：**只组装、绝不补充常识**——每条内容（title/summary/
evidence_urls）逐字来自 Analyst 转写的已验证 Event，Writer 不做任何自由发挥（那是编造入口）。
模板化 = 每期结构一致（competitor/period/sections/items/threat_radar/sources）→ 可汇总、可被下游消费。

schema 合规由 writer/report.py 的 WeeklyReport 在校验期强制（缺分级/理由/出处即 raise）：
Writer 只负责把数据摆对位置，交出去之前先让模型把一致性证明一遍。
"""
from __future__ import annotations

from writer.report import ReportItem, WeeklyReport


class Writer:
    """模板化周报生成器（Phase 5 实现：只消费已验证 Event，禁止自由补充）。"""

    def build(
        self,
        *,
        competitor_id: str,
        period: str,
        run_id: str | None = None,
        generated_at: str | None = None,
        sections: dict[str, int] | None = None,
        items: list[dict] | None = None,
    ) -> WeeklyReport:
        """把分级后的待写条目(items) + 上期 diff 计数(sections) 组装成周报。

        结构一致 + 模型自校验（headline/雷达/kind 计数对不上即 raise）——"schema 合规"在此强制。
        """
        sections = sections or {"adds": 0, "changes": 0, "removes": 0}
        validated = [ReportItem.model_validate(it) for it in (items or [])]
        return WeeklyReport(
            run_id=run_id,
            competitor_id=competitor_id,
            period=period,
            generated_at=generated_at,
            headline_count=len(validated),
            sections=sections,
            items=validated,
        )
