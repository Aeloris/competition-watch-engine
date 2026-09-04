# -*- coding: utf-8 -*-
"""威胁分级 rubric（Phase 5 Analyst 核心）：可解释、可回归的规则阶梯。

为什么分级不能纯靠 LLM（README2 §5.6 面试点）：
  同一竞品每次"降价/正式发布"必须稳定判为同一级——LLM 打分会飘，规则保证稳定性。
  所以 Analyst 用**规则先命中**的阶梯：从上到下第一条命中的规则定级（同 classify.py 的
  有序命中风格），每次判定都产出**理由链**（哪条规则、命中哪些信号词、几源确认）→ 可解释。

诚实的边界（主动承认，见 docs/analyst_writer.md §局限）：
  - 这是确定性信号分级器，不是语义理解器。规则词面覆盖不到的"异词同威胁"由
    **LLM 补语义**层兜底——但本期不接（无 key 离线优先，真 provider 联调时在规则之后加一层），
    不假装已做语义兜底。
  - **只读 Event.dimension，绝不改维度**：dimension 已混进 fp 并定锚（Phase 3），
    改维度 = 把一件事 diff 成"消失+新增"假情报。Analyst 只填威胁级 + 理由。
"""
from __future__ import annotations

from dataclasses import dataclass

from memory.fingerprint import normalize_title
from memory.schemas import Dimension, Event, ThreatLevel


def build_text(event: Event) -> str:
    """事件可读文本的书写层归一（title + summary）：NFKC/小写/去空白标点——与 fp 同一套归一。"""
    return normalize_title(f"{event.title}\n{event.summary}")


@dataclass(frozen=True)
class RubricRule:
    """一条确定性规则：可选限定维度 + 命中信号词（title+summary 归一后子串匹配）。"""

    id: str
    label: str            # 中文说明（进理由链）
    level: ThreatLevel
    reason: str           # 为什么给这个级（进理由链）
    patterns: tuple[str, ...] = ()
    dimension: Dimension | None = None  # None = 不限维度

    def matches(self, event: Event, blob: str) -> bool:
        if self.dimension is not None and event.dimension != self.dimension:
            return False
        if not self.patterns:
            return True  # 维度兜底规则：该维度且无前置命中
        return any(p in blob for p in self.patterns)


@dataclass(frozen=True)
class ThreatAssessment:
    """一次分级的可解释结果：级 + 命中的规则 + 理由链（每条人话，可展示给用户）。"""

    level: ThreatLevel
    rule_id: str
    reasons: list[str]


class ThreatRubric:
    """有序规则阶梯（先命中先定级，命中即停）——README2 §5.6『规则先命中』的落地。

    默认阶梯的口径（文档化，便于面试复述与测试锁定）：
      降价/折扣/免费        → high（"即将降价"这类价格战预告也是当下威胁，直接冲击定价/留客）；
      处罚/诉讼/监管        → high（合规风险信号）；
      预告/路线图（含"即将/将于/预计 X 正式发布"式预告）→ low：判定顺序在 release_ga 之前——
        尚未交付是观察信号、不是已交付 HIGH；真已交付的 GA 标题（无 预告/即将/将于 词）走
        release_ga → high（用户可见的产品级变化）；
      融资/收购 → medium；第三方实测/评测 → medium；常规功能迭代 → medium；
      价格条款常规调整 → medium；行业盘点/招聘/内容 → low。
    """

    def __init__(self, rules: list[RubricRule] | None = None) -> None:
        self._rules = rules if rules is not None else self.default_rules()

    @staticmethod
    def default_rules() -> list[RubricRule]:
        # 顺序铁律（先命中先定级，命中即停）：
        # ① 已是即时威胁的高信号（降价/监管）**整体最前**：价格战预告（"即将降价 30%"）是当下威胁，
        #    不能被 roadmap_preview 的 LOW 抢走；
        # ② roadmap_preview 提前于 release_ga——"即将/将于/预计 X 正式发布·全面开放"是**尚未交付**的
        #    预告，应 LOW 进观察；真已交付的 GA 标题（"v12.0 正式发布"）不含 预告/即将/将于 前置词，
        #    落在 release_ga → HIGH，互不干扰；
        # ③ 其余跨维度/低置信的定性信号规则（第三方、盘点、招聘）排在各自维度"兜底规则"
        #   （feature_update/market_other/org_other/…）之前——否则 feature 的盘点会被 feature 兜底判 medium。
        return [
            RubricRule("price_cut", "降价/让利", ThreatLevel.high,
                       "价格条款直接冲击我方定价与留客（价格战信号）",
                       patterns=("降价", "折扣", "优惠", "免费", "下调"), dimension=Dimension.price),
            RubricRule("penalty", "合规/监管风险", ThreatLevel.high,
                       "竞品触碰合规/监管/法务红线的信号，可能牵动行业口径",
                       patterns=("处罚", "诉讼", "监管", "违规", "整改", "罚款", "泄露"), dimension=Dimension.compliance),
            RubricRule("roadmap_preview", "预告/路线图", ThreatLevel.low,
                       "尚未上线，属路线图/预告——进观察，暂不构成即时威胁",
                       patterns=("预告", "即将", "预计", "路线图", "计划于", "下月", "将于")),
            RubricRule("release_ga", "正式发布/GA 上线", ThreatLevel.high,
                       "竞品产品级变化已向用户交付（正式发布/GA/全面开放）——直接挤占我方客群注意与需求",
                       patterns=("正式发布", "正式上线", "全面开放", "公开发布")),
            RubricRule("third_party_review", "第三方实测/评测", ThreatLevel.medium,
                       "第三方深度实测放大竞品功能的市场认知（非竞品自宣，但影响决策者心智）",
                       patterns=("实测", "评测", "测评", "上手体验")),
            RubricRule("funding_move", "资本/战略动作", ThreatLevel.medium,
                       "融资/收购等资本动作 = 竞品将加大投入的先行信号",
                       patterns=("融资", "收购", "投资", "上市", "出海"), dimension=Dimension.market),
            RubricRule("market_pan", "第三方行业盘点", ThreatLevel.low,
                       "第三方行业盘点/观点内容，非竞品自身动作",
                       patterns=("盘点", "榜单", "白皮书", "趋势", "象限", "行业报告"), dimension=Dimension.market),
            RubricRule("org_hiring", "招聘/内容运营", ThreatLevel.low,
                       "招聘/内容运营类动态（低威胁信号）",
                       patterns=("招聘", "招募", "入职", "实习", "开放职位"), dimension=Dimension.org),
            RubricRule("price_other", "价格/商务条款常规调整", ThreatLevel.medium,
                       "价格/商务条款出现结构性调整（不含降价让利）",
                       patterns=("涨价", "调价", "收费", "订阅", "定价", "套餐", "年费", "月费"), dimension=Dimension.price),
            RubricRule("feature_update", "功能/产品迭代", ThreatLevel.medium,
                       "常规功能/产品迭代上线（feature 维度基线）",
                       patterns=(), dimension=Dimension.feature),
            RubricRule("org_other", "组织/战略动作", ThreatLevel.medium,
                       "组织/人事结构性动作（非纯招聘/内容运营）",
                       patterns=(), dimension=Dimension.org),
            RubricRule("market_other", "市场/渠道动作", ThreatLevel.medium,
                       "市场/渠道层面的动作",
                       patterns=(), dimension=Dimension.market),
            RubricRule("compliance_other", "合规/舆情常规", ThreatLevel.low,
                       "合规/舆情维度但未命中红线级信号",
                       patterns=(), dimension=Dimension.compliance),
            RubricRule("default_low", "低威胁（兜底）", ThreatLevel.low,
                       "未命中任何明显威胁信号——常规信息，按低处理",
                       patterns=()),
        ]

    def apply(self, event: Event) -> ThreatAssessment:
        blob = build_text(event)
        for rule in self._rules:
            if not rule.matches(event, blob):
                continue
            return self._assessment(event, rule, blob)
        raise AssertionError("rubric 必有 default_low 兜底，不可能落到这里")  # 逻辑死路保护

    @staticmethod
    def _assessment(event: Event, rule: RubricRule, blob: str) -> ThreatAssessment:
        reasons = [f"规则[{rule.id}] {rule.label}：{rule.reason}"]
        if rule.patterns:
            hits = sorted({p for p in rule.patterns if p in blob})
            if hits:
                reasons.append(f"命中信号词：{'、'.join(hits)}")
        reasons.append(f"维度={event.dimension.value}；{len(event.evidence_urls)} 条独立来源")
        if len(event.evidence_urls) >= 2:
            reasons.append(f"{len(event.evidence_urls)} 源交叉确认（非孤证）")
        if rule.level is ThreatLevel.high:
            reasons.append("高威胁 → 建议本周进雷达头条并即时关注（Phase 7 触发即时告警）")
        return ThreatAssessment(level=rule.level, rule_id=rule.id, reasons=reasons)
