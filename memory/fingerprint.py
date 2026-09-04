# -*- coding: utf-8 -*-
"""事件指纹（README2 §5.5 / §4.4）：fp = sha1(竞品, 维度, 规范化标题)。

规范化只做**书写层**统一（全半角 / 空白 / 标点 / 大小写）——让"同一句措辞的多种写法"命中同一 fp；
**语义层**近义合并（措辞完全不同却指同一事，如官网"v12.0 正式发布" vs 媒体"押注 AI 跟进助手"）
是 Phase 3 Dedupe 的语义聚类职责，本函数不承诺跨措辞命中（诚实口径，见 docs/schema.md）。

纯函数、确定性：同样的输入永远同样的输出 → diff(Phase3) 与 eval(Phase8) 的可回归基石。
"""
from __future__ import annotations

import hashlib
import unicodedata


def normalize_title(title: str) -> str:
    """书写层归一：NFKC 统一全半角 → 转小写 → 只保留字母/数字/汉字/**小数点**。

    保留 '.' 是刻意的（与 dedupe.merge.version_anchor 同款理由，见其 docstring）：v12.0 与 v1.20 是
    两个不同版本，若把 '.' 当普通标点剥掉，两者都归成 'v120' → 同 fp → 同竞品同维度同期的两个真实
    发布在快照/diff 层被当成同一事件（build_snapshot 的 set 收敛 + digest dict 覆盖 → 一个被静默吞掉）。
    含点号只让归一结果**更区分**——两个原本不同的标题永远不会因保留 '.' 而撞在一起；无点号标题的
    归一结果与旧实现逐字符一致 → 既有 fp 全不动。语义层近义合并不是本函数职责（Phase 4 语义记忆）。
    """
    s = unicodedata.normalize("NFKC", title).lower()
    return "".join(ch for ch in s if ch.isalnum() or ch == ".")


def event_fingerprint(competitor_id: str, dimension: str, title: str) -> str:
    """fp = sha1(竞品|维度|规范化标题) 取前 16 位十六进制。"""
    payload = f"{competitor_id}|{dimension}|{normalize_title(title)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
