# -*- coding: utf-8 -*-
"""维度判定：确定性关键词规则（README2 §5.6 的 rule-first 半），Phase 3 Dedupe 前置。

为什么 Phase 3 就要（不能拖到 Phase 5 Analyst）：
  Event.dimension 必填，而事件指纹 fp = sha1(竞品|维度|规范化标题) 把维度混进了事件身份。
  等 Phase 5 再分类 → 事件换维度 = fp 变 = 同一件事被 diff 成"消失 + 新增"。
  所以本阶段先用规则给每张卡定维，让 fp 首次去重即定锚、**冻结**；
  Phase 5 用 rubric + LLM 完善分类时只改 dimension 注解、不改 fp（身份稳定优先）。

诚实边界：这是"关键词命中器"，不是语义分类器。命中按表内优先级取首个命中组；
全不命中回 feature（竞品语料绝大多数是产品动态——宁默认 feature，也不瞎猜别的维度）。
关键词只覆盖常见词面，真实文案的漏判/误判由 Phase 5 rubric 接管。
"""
from __future__ import annotations

from memory.fingerprint import normalize_title
from memory.schemas import Dimension

# 命中优先级：feature 最前（默认倾向），依次 price / market / org / compliance。
# 关键词在 NFKC+小写+去空白标点后的连续文本里做子串匹配；ASCII 一律小写。
# 刻意不含 GA/API/支持/安全这类泛词——它们常出现在 feature 文案里，会让维度误判成别类。
_DIMENSION_KEYWORDS: tuple[tuple[Dimension, tuple[str, ...]], ...] = (
    (
        Dimension.feature,
        (
            "上线", "发布", "更新", "新增", "预告", "版本", "功能", "集成", "连接器",
            "推出", "升级", "改进", "内测", "公测", "适配", "开发", "接口", "同步",
        ),
    ),
    (
        Dimension.price,
        (
            "价格", "定价", "涨价", "降价", "调价", "上涨", "下调", "收费", "订阅",
            "套餐", "折扣", "优惠", "报价", "年费", "月费",
        ),
    ),
    (
        Dimension.market,
        (
            "市场", "盘点", "行业", "厂商", "渠道", "融资", "收购", "投资", "上市",
            "出海", "份额", "增长", "竞争", "战略客户",
        ),
    ),
    (
        Dimension.org,
        (
            "任命", "招聘", "离职", "裁员", "团队", "总部", "战略", "重组", "人事",
            "高管", "ceo", "cto", "cfo", "合伙人",
        ),
    ),
    (
        Dimension.compliance,
        (
            "合规", "认证", "隐私", "备案", "审计", "资质", "等保", "监管", "泄露",
            "处罚", "诉讼", "数据跨境", "整改",
        ),
    ),
)


def classify_dimension(title: str, summary: str = "") -> Dimension:
    """规则维度判定：扫描 title+summary，命中维度优先级表第一个组即返回；全不命中回 feature。"""
    text = normalize_title(f"{title} {summary}")
    for dim, keywords in _DIMENSION_KEYWORDS:
        if any(k in text for k in keywords):
            return dim
    return Dimension.feature
