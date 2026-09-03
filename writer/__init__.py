"""写作层（Phase 5 Writer 落地）。

只基于"已验证 Event + Analyst 已分级"的条目组装模板化结构化周报（动态列表 + 威胁雷达 + 出处清单），
schema 合规由 WeeklyReport 在校验期强制；绝不允许自由补充常识（那是编造入口）——先审后发的前半段。
"""
from writer.report import ReportItem, ThreatRadar, WeeklyReport
from writer.service import Writer

__all__ = ["Writer", "WeeklyReport", "ReportItem", "ThreatRadar"]
