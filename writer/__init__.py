"""写作层（Phase 5 Writer）。

只基于已入库、已验证的 Event 写模板化结构化周报（动态列表/对比表/威胁雷达），
绝不允许自由补充常识（那是编造入口）——先审后发的前半段。
"""
from writer.service import Writer

__all__ = ["Writer"]
