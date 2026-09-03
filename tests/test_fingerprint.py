# -*- coding: utf-8 -*-
"""事件指纹：确定性纯函数 + 书写层规范化命中；跨措辞差异如实不同（语义合并属 Phase 3）。"""
from memory import event_fingerprint


def test_same_input_same_fp():
    a = event_fingerprint("crm_alpha", "feature", "Alpha CRM v12.0 正式发布：智能跟进助手 GA")
    b = event_fingerprint("crm_alpha", "feature", "Alpha CRM v12.0 正式发布：智能跟进助手 GA")
    assert a == b
    assert len(a) == 16


def test_writing_level_normalization_hits_same_fp():
    """同一句措辞的书写差异（全角/空白/标点/大小写）必须归一为同一 fp。"""
    fp = event_fingerprint("crm_alpha", "feature", "Alpha CRM v12.0 正式发布：智能跟进助手 GA")
    variants = [
        "ALPHA ＣＲＭ v12.0正式发布:智能跟进助手GA",          # 大写 + 全角
        "Alpha　CRM v12.0 正式发布、智能跟进助手ＧＡ",          # 全角空格/顿号/大写 GA
        "Alpha CRM v12.0 正式发布：智能跟进助手 GA。",          # 句尾标点
    ]
    for v in variants:
        assert event_fingerprint("crm_alpha", "feature", v) == fp


def test_different_wording_different_fp():
    """措辞真不同（官网版 vs changelog 版）→ fp 不同：说明纯指纹不做语义合并，那是 Phase 3 Dedupe 的活。"""
    assert event_fingerprint("crm_alpha", "feature", "Alpha CRM v12.0 正式发布：智能跟进助手 GA") != event_fingerprint(
        "crm_alpha", "feature", "【发布】v12.0 智能跟进助手 GA"
    )


def test_competitor_and_dimension_participate_in_fp():
    base = ("竞品A", "feature", "全线降价 30%")
    assert event_fingerprint(*base) != event_fingerprint("竞品B", *base[1:])
    assert event_fingerprint(*base) != event_fingerprint(base[0], "price", base[2])
