# -*- coding: utf-8 -*-
"""Phase 5 Analyst：rubric 分级（high/medium/low + 理由链）——确定性、可解释、只读不改维度。

真值 = 实测（docs/analyst_writer.md 的校准表，fixtures 逐条核对，绝不编占位百分比）：
- crm_alpha W35：v12.0 GA → high(release_ga)；第三方实测 → medium(third_party_review)；v12.1 预告 → low(roadmap_preview)
- bi_beta   W35：图表库 3.0 GA → high(release_ga)；2026 盘点(market) → low(market_pan)；权限预告 → low(roadmap_preview)；看板分享更新 → medium(feature_update)

锁三条性质（面试可复述的"为什么规则不用 LLM"）：
1. 稳定性：同一事件永远同分级（LLM 打分飘，规则不飘）→ 同语料重跑周报 severity 不变；
2. 可解释：每次分级产出理由链（哪条规则/命中词/几源/为何该级），不是黑盒分数；
3. 不越权：只读 dimension 打分，绝不改 dimension/fp/kind（那会把一件事 diff 成"消失+新增"假情报）。
"""
from datetime import date, datetime

import pytest

from analyst import Analyst
from analyst.rubric import RubricRule, ThreatAssessment, ThreatRubric
from memory import Dimension, Event, EventKind, ThreatLevel

SETTINGS_FETCHED = datetime(2026, 8, 31, 9, 0, 0)


def _ev(
    title: str,
    dimension: Dimension = Dimension.feature,
    summary: str = "",
    urls: tuple[str, ...] = ("https://x.example/1",),
) -> Event:
    """构造一个合法 Event（字段齐全，sev/fp 留给被测层填）。"""
    return Event(
        id="ev-" + str(abs(hash(title))),
        competitor_id="crm_alpha",
        dimension=dimension,
        kind=EventKind.add,
        title=title,
        summary=summary,
        evidence_urls=list(urls),
        first_seen=date(2026, 8, 25),
        last_seen=date(2026, 8, 29),
    )


def _rule_id(a: ThreatAssessment) -> str:
    return a.rule_id


# ---------- 性质 1：确定性 / 稳定性 ----------

def test_same_event_always_same_grade():
    rubric = ThreatRubric()
    e = _ev("Alpha CRM v12.0 正式发布：智能跟进助手 GA")
    a1, a2 = rubric.apply(e), rubric.apply(e)
    assert (a1.level, a1.rule_id, a1.reasons) == (a2.level, a2.rule_id, a2.reasons)


def test_grade_two_identical_events_yield_identical_json_items():
    evs = [_ev("看板分享新增链接权限与过期时间"), _ev("看板分享新增链接权限与过期时间")]
    out = Analyst().grade(evs)
    assert out[0] == out[1]


# ---------- 真值校准：fixtures W35 逐条（rules 锁死，文档承诺只信实测） ----------

def test_calibration_crm_w35_grades_match_fixtures():
    """crm W35 三条变更的预期分级（与 smoke 实测一致：radar {high1, med1, low1}）。"""
    rubric = ThreatRubric()
    cases = [
        # (title, dimension, 期望级, 期望规则)
        ("Alpha CRM v12.0 正式发布：智能跟进助手 GA", Dimension.feature, ThreatLevel.high, "release_ga"),
        ("实测 Alpha CRM 跟进助手：自动化与人工兜底", Dimension.feature, ThreatLevel.medium, "third_party_review"),
        ("预告：v12.1 移动端日历与待办同步", Dimension.feature, ThreatLevel.low, "roadmap_preview"),
    ]
    for title, dim, level, rule in cases:
        a = rubric.apply(_ev(title, dim))
        assert (a.level, a.rule_id) == (level, rule), f"{title} → {a.level}/{a.rule_id}"


def test_calibration_bi_w35_grades_match_fixtures():
    """bi W35 四条变更（radar {high1, med1, low2}）——看板分享无信号词 → 维度兜底 medium。"""
    rubric = ThreatRubric()
    cases = [
        ("图表库 3.0 + 移动端适配正式发布", Dimension.feature, ThreatLevel.high, "release_ga"),
        ("2026 商业智能工具盘点：自助式分析成主线", Dimension.market, ThreatLevel.low, "market_pan"),
        ("预告：目录级与行级权限管控下月开放", Dimension.feature, ThreatLevel.low, "roadmap_preview"),
        ("更新：看板分享新增链接权限与过期时间", Dimension.feature, ThreatLevel.medium, "feature_update"),
    ]
    for title, dim, level, rule in cases:
        a = rubric.apply(_ev(title, dim))
        assert (a.level, a.rule_id) == (level, rule), f"{title} → {a.level}/{a.rule_id}"


# ---------- 性质 2：可解释（理由链结构） ----------

def test_high_grade_reason_chain_explains_why():
    a = ThreatRubric().apply(_ev("Alpha CRM v12.0 正式发布", urls=("https://a/1", "https://a/2", "https://a/3")))
    assert a.reasons[0].startswith("规则[release_ga]")       # 哪条规则 + 人话解释
    assert any("正式发布" in r for r in a.reasons)            # 命中哪些信号词
    assert any("3 条独立来源" in r for r in a.reasons)        # 证据强度
    assert any("高威胁" in r for r in a.reasons)              # 该级的业务含义
    assert ThreatRubric().apply(_ev("普通更新")).reasons[0].startswith("规则[feature_update]")


# ---------- 命中信号词/维度约束 ----------

def test_price_cut_and_penalty_are_high_when_dimension_matches():
    rubric = ThreatRubric()
    a = rubric.apply(_ev("AI 助手套餐整体下调 30% 并开放免费档", Dimension.price))
    assert (a.level, a.rule_id) == (ThreatLevel.high, "price_cut")
    b = rubric.apply(_ev("数据泄露被监管点名并要求整改", Dimension.compliance))
    assert (b.level, b.rule_id) == (ThreatLevel.high, "penalty")


def test_price_signal_ignored_outside_price_dimension():
    """降价信号词只在 price 维度生效（feature 里的"免费"不被误判成 price_cut）。"""
    a = ThreatRubric().apply(_ev("新增免费试用入口", Dimension.feature))
    assert a.rule_id != "price_cut"  # feature 维度不触发价格规则


def test_funding_is_medium_market_move():
    a = ThreatRubric().apply(_ev("完成 1 亿元融资，加速企业版研发", Dimension.market))
    assert (a.level, a.rule_id) == (ThreatLevel.medium, "funding_move")


def test_plain_dimension_content_hits_catchall_not_default():
    rubric = ThreatRubric()
    assert rubric.apply(_ev("更新：SQL 编辑器支持 AI 生成查询", Dimension.feature)).rule_id == "feature_update"
    assert rubric.apply(_ev("启动渠道合作伙伴认证计划", Dimension.market)).rule_id == "market_other"
    assert rubric.apply(_ev("新设数据安全部门", Dimension.org)).rule_id == "org_other"
    assert rubric.apply(_ev("组织数据安全与合规月会", Dimension.compliance)).rule_id == "compliance_other"


def test_org_recruiting_is_low_not_org_catchall():
    a = ThreatRubric().apply(_ev("招聘 30 名销售代表并开放实习岗位", Dimension.org))
    assert (a.level, a.rule_id) == (ThreatLevel.low, "org_hiring")


# ---------- 顺序铁律：跨维度低置信"定性信号"必须先于维度兜底 ----------

def test_default_rule_order_signal_before_catchall():
    ids = [r.id for r in ThreatRubric.default_rules()]
    # 三种维度兜底之前，必须能先被定性规则接走（先命中先定级，命中即停）
    assert ids.index("roadmap_preview") < ids.index("feature_update")
    assert ids.index("third_party_review") < ids.index("feature_update")
    assert ids.index("market_pan") < ids.index("market_other")
    assert ids.index("org_hiring") < ids.index("org_other")
    # 即时威胁（降价/监管）必须整体最前，不被任何 medium/low 抢先（"即将降价"也算当下威胁）
    for high_rule in ("price_cut", "penalty"):
        for low_rule in ("roadmap_preview", "market_pan", "org_hiring"):
            assert ids.index(high_rule) < ids.index(low_rule)
    for low_rule in ("market_pan", "org_hiring"):
        assert ids.index("release_ga") < ids.index(low_rule)
    # roadmap_preview 是唯一排进红线区"前面"的 LOW：它必须先于 release_ga 接走"即将/将于 X 正式发布"
    # 这类**尚未交付**的预告（→LOW），但不允许比降价/监管更前（那是即时威胁）。
    assert ids.index("roadmap_preview") < ids.index("release_ga")
    assert ids.index("price_cut") < ids.index("roadmap_preview")
    assert ids.index("penalty") < ids.index("roadmap_preview")


def test_imminent_release_preview_is_low_but_delivered_ga_is_high():
    """A11 回归：词面同含"即将"与"正式发布"的标题是未交付预告 → LOW；
    真已交付的 GA（标题无 预告/即将/将于 前置词）→ HIGH；"即将降价"仍是当下威胁 → HIGH。"""
    r = ThreatRubric()
    a = r.apply(_ev("v12.1 即将正式发布：目录级权限管理", Dimension.feature))
    assert (a.level, a.rule_id) == (ThreatLevel.low, "roadmap_preview")
    b = r.apply(_ev("Alpha CRM v12.0 正式发布：智能跟进助手 GA", Dimension.feature))
    assert (b.level, b.rule_id) == (ThreatLevel.high, "release_ga")
    c = r.apply(_ev("Beta BI 即将下调全系订阅价 30%", Dimension.price))
    assert (c.level, c.rule_id) == (ThreatLevel.high, "price_cut")


# ---------- 性质 3：不越权 —— Analyst 只分级，不改维度/fp/kind ----------

def test_grade_never_rewrites_dimension_fp_kind():
    src = _ev("更新：看板分享新增链接权限", Dimension.feature)
    src.fp = "0aa08ae4f389825f"
    src.kind = EventKind.change
    items = Analyst().grade([src])
    assert len(items) == 1
    out = items[0]
    assert out["dimension"] == "feature"      # 原维度原样进周报
    assert out["fp"] == src.fp                # fp 不变 → 下期 diff 锚点不破坏
    assert out["kind"] == "change"            # add/change 语义不被 analyst 改写
    assert out["severity"] in {"high", "medium", "low"}
    assert isinstance(out["reasons"], list) and len(out["reasons"]) >= 1


def test_assess_and_grade_agree_on_level():
    e = _ev("Alpha CRM v12.0 正式发布：智能跟进助手 GA")
    rubric, analyst = ThreatRubric(), Analyst()
    assert rubric.apply(e).level.value == analyst.grade([e])[0]["severity"]


# ---------- 评分语义：多源交叉确认进理由（非孤证） ----------

def test_single_source_marks_isolated_not_crosschecked():
    a = ThreatRubric().apply(_ev("Alpha CRM v12.0 正式发布"))
    assert not any("交叉确认" in r for r in a.reasons)


def test_custom_rules_injectable_for_experiments():
    """rubric 可注入自定义规则（先命中先定级）——便于按行业替换默认口径。"""
    custom = ThreatRubric(rules=[RubricRule("x", "自定义", ThreatLevel.high, "r", patterns=("哨兵词",))] +
                                [r for r in ThreatRubric.default_rules() if r.id == "default_low"])
    a = custom.apply(_ev("出现哨兵词的自定义信号"))
    assert (a.level, a.rule_id) == (ThreatLevel.high, "x")
