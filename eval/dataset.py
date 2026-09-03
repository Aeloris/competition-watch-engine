# -*- coding: utf-8 -*-
"""评测数据集（Phase 8）：人工独立标注的"权威答案"，引擎无关。

对齐 README2 §5.11：评测集 = 人工整理"过去某段时间真实发生的竞品事件集"。
标注来源 = **fixtures/sources/*.json 的原文内容**（读那些"报道"本身归纳出"发生了什么"），
**绝不从引擎输出反推**——标注与引擎共用同一份语料，但"答案"是人读原文定的。

分两类权威：
- `DUPLICATE_GROUPS`：同一真实事件被多个信源报道的"应当并成一条"的组合
  （fixtures 预置：crm v12.0 官网博客+changelog+媒体三源重复；bi 图表库 3.0 官网+媒体双源重复）；
- `W35_GOLD_EVENTS`：2026-W35 每个竞品"真实发生了哪些变化"（独立事件：去重合并后的一条条），
  每条给 title 里的稳定锚子串（判中哪个引擎条目）+ 期望威胁级（severity）。

诚实口径：这些锚与分级是基于 fixtures 原文的人工阅读，属于"固定评测集"；
规则引擎的判定与之一致 = 回归一致（确定性），不代表真实世界的泛化能力。
"""
from __future__ import annotations

# 评测回放的周期窗（与 fixtures 语料对齐；W34=冷启动底座、W35=diff 目标期）
PERIODS = ("2026-W34", "2026-W35")
COMPETITORS = ("crm_alpha", "bi_beta")

# 每条真实报道的出处 URL（人工从 fixtures/sources/*.json 原文摘录，作"应当并入一条"的权威）
_V12_SOURCES = (
    "https://alpha-crm.example.com/blog/v12-ga",
    "https://alpha-crm.example.com/changelog.rss#v12-ga",
    "https://techwire.example.com/p/alpha-crm-v12",
)
_CHARTS3_SOURCES = (
    "https://beta-bi.example.com/blog/charts3-mobile",
    "https://datanews.example.com/beta-bi-charts3",
)

# 同事件多源 → 应并成一条（每条 = 一条独立真实事件被拆成多源的错误形态）
DUPLICATE_GROUPS = [
    {
        "competitor": "crm_alpha",
        "anchor": "Alpha CRM v12.0 智能跟进助手",
        "urls": list(_V12_SOURCES),
        "n": len(_V12_SOURCES),
    },
    {
        "competitor": "bi_beta",
        "anchor": "图表库 3.0 正式发布",
        "urls": list(_CHARTS3_SOURCES),
        "n": len(_CHARTS3_SOURCES),
    },
]

# W35 真实变化事件（人工读原文：发生了什么 + 威胁等级）。anchor 必须能在引擎条目标题里命中。
W35_GOLD_EVENTS = [
    # crm_alpha：官网博客公告 v12.0 GA（三源并入一条）、独立评测实测、v12.1 移动端预告
    {"competitor": "crm_alpha", "anchor": "v12.0", "severity": "high"},     # 正式发布/GA
    {"competitor": "crm_alpha", "anchor": "实测", "severity": "medium"},     # 独立第三方评测
    {"competitor": "crm_alpha", "anchor": "v12.1", "severity": "low"},       # 路线图预告
    # bi_beta：图表库 3.0 发布（双源并入）、看板分享权限、行业盘点、行级权限预告
    {"competitor": "bi_beta", "anchor": "图表库 3.0", "severity": "high"},   # 正式发布/GA
    {"competitor": "bi_beta", "anchor": "看板分享", "severity": "medium"},    # 常规迭代(分享/权限)
    {"competitor": "bi_beta", "anchor": "盘点", "severity": "low"},           # 行业盘点/榜单
    {"competitor": "bi_beta", "anchor": "行级权限", "severity": "low"},       # 路线图预告
]

_GOLD_COMPETITORS = {e["competitor"] for e in W35_GOLD_EVENTS}


def load_gold() -> dict:
    """加载全部权威标注（供 harness / run.py 消费）。"""
    return {"groups": list(DUPLICATE_GROUPS), "events": list(W35_GOLD_EVENTS)}


def gold_health_check() -> list[str]:
    """校验标注集自洽（非空、字段合法），返回问题清单；空 = 健康。

    注意：这只查"标注本身有没有病"，不查"引擎对不对"——引擎比对在 metrics/harness。
    """
    problems: list[str] = []
    if not DUPLICATE_GROUPS:
        problems.append("DUPLICATE_GROUPS 为空——去重评测无据")
    for g in DUPLICATE_GROUPS:
        if g["n"] != len(g["urls"]) or g["n"] < 2:
            problems.append(f"组 {g['anchor']}: n 与 urls 不一致或 <2（同事件至少两源才算重复组）")
    if not W35_GOLD_EVENTS:
        problems.append("W35_GOLD_EVENTS 为空——事件一致率评测无据")
    for e in W35_GOLD_EVENTS:
        if e["severity"] not in {"high", "medium", "low"}:
            problems.append(f"{e['competitor']} {e['anchor']}: severity 非法")
        if not e["anchor"]:
            problems.append(f"{e['competitor']}: anchor 为空")
    if _GOLD_COMPETITORS != set(COMPETITORS):
        problems.append(f"gold 竞品集合 {sorted(_GOLD_COMPETITORS)} != 评测竞品 {sorted(COMPETITORS)}")
    return problems
