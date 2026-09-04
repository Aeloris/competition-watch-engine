# -*- coding: utf-8 -*-
"""去重合并（README2 §5.4）：FactCards → Events（同事件多源并一条，evidence_urls 聚合）。

去重两层（本阶段落地前一层半，诚实口径）：
1. **版本锚（release identity）**：同一竞品同维度下，标题带同一"版本号"（v12.0/3.0/v2 这类
   `\\d+(.\\d+)+`）的卡片 → 同一发布事件。对发布/上线类新闻，版本号 ≈ 事件身份证，
   比表面措辞可靠得多（fixtures 的 v12.0 三源措辞各不相同仍同锚）。
2. **近原文重复（词法）**：无版本锚的卡片只并"近原文重复"——竞品别名剔除后标题相同、
   或一个几乎完整包含另一个（长短比 ≥0.9）。**异词异义的近义合并是语义聚类（embedding）的活，
   本阶段不实现、不硬凑**（Phase 4 接 Qdrant 语义后再补那半层）。

事件合并规则：canonical 标题 = 簇内 published_at 最早那张卡的标题（并列取 url 升序，确定性）；
summary 取簇内最长（"内容取最全"）；evidence_urls = 全部成员 url 去重排序（证据取最多，可溯源）。
维度用规则分类（dedupe/classify.py）；`fp` 在此首次定锚并冻结，后续 diff 以此为准。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from dedupe.classify import classify_dimension
from memory.fingerprint import event_fingerprint, normalize_title
from memory.schemas import Dimension, Event, EventKind, FactCard

# 版本号：v12.0 / 3.0 / v1.2.3……（要求有小数点，排除年份"2026"与"v2"这类非发布锚）
_VERSION_RE = re.compile(r"[vV]?\d+(?:\.\d+)+")


def _sha1_hex(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()


def version_anchor(title: str) -> str | None:
    """从原文标题抽"版本锚"：v12.0 / 3.0 → '12.0' / '3.0'（去 v、小写，**保留小数点结构**）。

    保留点号是刻意的：'1.20' 与 '12.0' 是不同版本，一旦像旧实现那样 `re.sub(r"\\D", "", …)`
    把非数字全剥掉，两者都变 '120' → 撞锚误并成同一发布事件。锚只做同竞品同维度簇内相等比较，
    含点不影响匹配，只消除歧义。
    """
    m = _VERSION_RE.search(title)
    if not m:
        return None
    return m.group(0).lower().lstrip("v")


def _strip_aliases(norm: str, aliases: list[str]) -> str:
    """从规范化标题里剔除竞品别名子串（'alphacrm' 等），避免产品名干扰近原文比较。"""
    out = norm
    for alias in aliases:
        na = normalize_title(alias)
        if na:
            out = out.replace(na, "")
    return out


def _near_verbatim(a: str, b: str, aliases: list[str]) -> bool:
    """近原文重复：别名剔除后标题相同，或一个几乎完整包含另一个（长短比 ≥ 0.9）。"""
    x = _strip_aliases(a, aliases)
    y = _strip_aliases(b, aliases)
    if not x and not y:
        return True
    if x == y:
        return True
    short, long_ = (x, y) if len(x) <= len(y) else (y, x)
    return len(short) >= 0 and short in long_ and len(short) / len(long_) >= 0.9


@dataclass
class _Cluster:
    """一次去重合并的中间簇。rep = 簇内最早发布那张卡（canonical 出处）。"""

    competitor_id: str
    dimension: Dimension
    anchor: str | None
    norm_title: str
    rep: FactCard
    members: list[FactCard] = field(default_factory=list)


class DedupeSummary(BaseModel):
    """trace 计数：去重剩 M（§5.10 采集 N → 去重剩 M → diff 出 K 的第二节）。"""

    competitor_id: str
    period: str
    cards_in: int = 0
    events_out: int = 0
    merges: int = 0            # cards_in - events_out - skipped_empty_title = 被合并吸收掉的重发
    skipped_empty_title: int = 0  # 无标题卡（Event.title ≥1 无法成事件）：丢卡不崩、单独计数可见


def _cluster_to_event(cl: _Cluster) -> Event:
    comp = cl.competitor_id
    dim = cl.dimension
    title = cl.rep.title                      # canonical = 最早发布卡
    summary = max(cl.members, key=lambda m: len(m.summary)).summary or cl.rep.summary  # 内容取最全
    urls = sorted({m.source_url for m in cl.members})  # 证据取最多，可溯源
    fp = event_fingerprint(comp, dim.value, title)
    dates = [m.published_at.date() for m in cl.members]
    return Event(
        id=_sha1_hex(f"{comp}|{fp}")[:16],
        competitor_id=comp,
        dimension=dim,
        kind=EventKind.add,  # 本周期新观测；diff 阶段按与上期比对重分类
        title=title,
        summary=summary,
        evidence_urls=urls,
        severity=None,
        first_seen=min(dates),
        last_seen=max(dates),
        fp=fp,
    )


def merge_to_events(
    cards: list[FactCard],
    *,
    period: str,
    aliases: dict[str, list[str]] | None = None,
) -> tuple[list[Event], DedupeSummary]:
    """同竞品（单竞品输入）的一张卡组 → 去重合并 → Events + 计数。

    确定性：卡片先按 (published_at, url) 升序，贪心并入**第一个**同维度同锚/近原文的簇；
    输出 Events 按 fp 升序。同输入永远同输出（可回放）。
    """
    aliases = aliases or {}
    clusters: list[_Cluster] = []
    skipped_empty_title = 0
    for card in sorted(cards, key=lambda c: (c.published_at.isoformat(), c.source_url)):
        # 无标题卡（normalize 后一个字母数字都不剩）：成不了可报告事件——Event.title 强制 ≥1，
        # 且指纹/版本锚/近原文聚类都依赖标题。丢卡并计数，绝不把 Event(title="") 抛给下游
        # 让整条 run 因 pydantic ValidationError 崩掉（BUG-2 回归）。
        if not normalize_title(card.title):
            skipped_empty_title += 1
            continue
        dim = classify_dimension(card.title, card.summary)
        anchor = version_anchor(card.title)
        norm = normalize_title(card.title)
        aliases_for = aliases.get(card.competitor_id, [])
        target: _Cluster | None = None
        for cl in clusters:
            if cl.competitor_id != card.competitor_id or cl.dimension != dim:
                continue
            if anchor is not None:
                if cl.anchor == anchor:      # 发布事件：版本锚一致即并入
                    target = cl
                    break
            elif cl.anchor is None and _near_verbatim(norm, cl.norm_title, aliases_for):
                target = cl                  # 无锚卡只并近原文重复
                break
        if target is None:
            clusters.append(_Cluster(card.competitor_id, dim, anchor, norm, rep=card, members=[card]))
        else:
            target.members.append(card)

    events = sorted((_cluster_to_event(cl) for cl in clusters), key=lambda e: e.fp)
    competitor_id = cards[0].competitor_id if cards else ""
    summary = DedupeSummary(
        competitor_id=competitor_id,
        period=period,
        cards_in=len(cards),
        events_out=len(events),
        merges=len(cards) - len(events) - skipped_empty_title,
        skipped_empty_title=skipped_empty_title,
    )
    return events, summary


def read_competitor_aliases(mock_dir: str | Path) -> dict[str, list[str]]:
    """读 fixtures 里每个竞品的 aliases（供 merge_to_events 剔除产品名干扰）；目录缺失返回 {}。"""
    d = Path(mock_dir)
    out: dict[str, list[str]] = {}
    if not d.exists():
        return out
    for p in sorted(d.glob("*.json")):
        data = json.loads(p.read_text(encoding="utf-8"))
        out[data["id"]] = list(data.get("aliases", []))
    return out
