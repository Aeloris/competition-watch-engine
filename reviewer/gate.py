# -*- coding: utf-8 -*-
"""Reviewer 审稿门卫（Phase 6 落地）：grounding 核验 + 矛盾检测 + 口径合规。

README2 §5.8 的三个判定，落到**可回归的确定性检查**（离线无 key 全链路可跑）：

1. **grounding（来源支撑度）**——诚实拆成"闭环自证"，不做"读原文语义"：
   本期周报每条结论必须能回溯到 **本期真采集到的卡**（evidence_url 都在 state.cards 的
   URL 集合里，防编造 URL）与 **本期真 diff 出的变更事件**（fp 都在 state.changes 的
   fp 集合里，防凭空 fp）；再核 **理由里声称命中的信号词必须真实出现在条目正文**
   （防张冠李戴：不能给一条"发布"动态挂"降价"的理由）。"原文明说 vs 暗示"的深层
   语义核对需要 LLM 语义层（provider-gated），本期不接 —— 见 docs/reviewer.md 局限。

2. **矛盾检测（确定性子集）**：显式反义极性对（涨价↔降价、正式发布↔停止支持…）在
   **同竞品本期两条**上同时命中 + **共享主体锚**（同版本号 / 高共享字符二元组）→ 判互斥，
   转人工复核（不自动二选一）。只抓能确定的机械冲突；语义级委婉互斥不判，宁转人工。

3. **口径/结构合规**：P5 的 WeeklyReport schema 已强制（severity/理由/出处/雷达自洽）；
   这里补防"与上期重复"——diff 已把 unchanged drop，若本期条目里又出现 unchanged_fps
   里的 fp = 下游把旧闻再发，拦截。

独立性（"门卫不能自己审自己"）：Reviewer 独立于 Writer 实现（writer 包不依赖本模块），
判定依据是**本 run 的证据**（cards / changes / unchanged_fps），不是 Writer 的自述。

本类只产出**带 code 的问题清单**（见 problems.py）；verdict 路由（结构性走 REWRITE 阶梯、
信任问题直转 human）由 orchestration 节点决定 —— 这里保持纯"核验者"，单测锁死。
"""
from __future__ import annotations

import unicodedata

from memory.fingerprint import normalize_title
from reviewer.problems import (
    CONTRADICTION,
    DUPLICATE,
    EMPTY_REASONS,
    EMPTY_TITLE,
    MISSING_DIMENSION,
    MISSING_FP,
    MISSING_SEVERITY,
    NO_EVIDENCE,
    ReviewProblem,
    UNGROUNDED_CLAIM,
    UNGROUNDED_FP,
    UNSOURCED_URL,
)

HIT_PREFIX = "命中信号词："

# 反义极性轴：同轴、方向相反的显式词（机械矛盾的最小集合，宁窄勿宽 + 宁转人工勿误放）
POLARITY_AXES: dict[str, dict[str, tuple[str, ...]]] = {
    "价格方向": {
        "涨": ("涨价", "上涨", "上调", "提价"),
        "跌": ("降价", "下调", "让利", "优惠"),
    },
    "上线/下线状态": {
        "上线": ("正式发布", "正式上线", "全面开放", "公开发布", "上线"),
        "下线": ("停止支持", "停止服务", "停止更新", "下线", "下架", "关闭", "撤回"),
    },
}


def _clean(s: str) -> str:
    """书写层归一：NFKC → 小写 → 只留字母/数字/汉字（同 fp 的 normalize_title）。"""
    return normalize_title(s)


def _bigrams(text: str) -> set[str]:
    """长度≥2 的字符二元组集合（CJK 也按字符切，跨全/半角差异的共享词锚）。"""
    cleaned = _clean(text)
    return {cleaned[i:i + 2] for i in range(len(cleaned) - 1)}


def _versions(text: str) -> set[str]:
    """文本里的显式版本号（v12.0 / 3.0 …），矛盾双方必须同版本才算同一主体。"""
    out: set[str] = set()
    cur, prev_digit = [], False
    for ch in unicodedata.normalize("NFKC", text).lower():
        if ch.isdigit() or ch == ".":
            cur.append(ch)
        else:
            if len(cur) >= 2 and any(c.isdigit() for c in cur) and "." in cur:
                out.add("".join(cur))
            cur = []
    if len(cur) >= 2 and any(c.isdigit() for c in cur) and "." in cur:
        out.add("".join(cur))
    return out


class Reviewer:
    """质量门卫（Phase 6）：对周报初稿做 grounding/矛盾/合规核验，产出结构化问题清单。

    review() 是纯核验者：不给 verdict。路由（REWRITE/human）是编排节点的事。
    """

    def review(
        self,
        draft: dict,
        *,
        cards: list[dict] | None = None,
        changes: list[dict] | None = None,
        unchanged_fps: list[str] | None = None,
    ) -> list[ReviewProblem]:
        """逐条核验周报初稿；cards/changes/unchanged_fps 是"本 run 证据"。

        证据缺席（None）时退化为纯结构门卫（对应无 run 上下文的单测/纯格式审）。
        """
        problems: list[ReviewProblem] = []
        items = draft.get("items", []) if isinstance(draft, dict) else []
        card_urls = {c.get("source_url") for c in cards} if cards else None
        change_fps = {c.get("fp") for c in changes} if changes else None
        unchanged = set(unchanged_fps) if unchanged_fps else set()

        for i, item in enumerate(items, start=1):
            problems.extend(self._structural(item, i))
            # grounding 需要本期证据；有 URL 集合才查"编造 URL"，有 fp 集合才查"凭空 fp"
            if card_urls is not None and item.get("evidence_urls"):
                problems.extend(self._unsourced_urls(item, i, card_urls))
            if change_fps is not None and item.get("fp"):
                problems.extend(self._ungrounded_fp(item, i, change_fps))
            problems.extend(self._claim_words(item, i))
            problems.extend(self._duplicate_vs_unchanged(item, i, unchanged))

        problems.extend(self._contradictions(items))
        return problems

    # ---------------- 结构性缺口（格式病 → REWRITE 阶梯）----------------
    @staticmethod
    def _label(item: dict, i: int) -> str:
        t = str(item.get("title") or "")
        return f"#{i} {t[:24] or '(无标题)'}"

    @staticmethod
    def _structural(item: dict, i: int) -> list[ReviewProblem]:
        out: list[ReviewProblem] = []
        fp = item.get("fp")
        if not item.get("evidence_urls"):
            out.append(ReviewProblem(NO_EVIDENCE, i, fp,
                                     f"{Reviewer._label(item, i)}: 无出处(evidence_urls 空)——先审后发要求每条可溯源"))
        if not str(item.get("title") or "").strip():
            out.append(ReviewProblem(EMPTY_TITLE, i, fp, f"#{i}: 标题为空"))
        if not item.get("dimension"):
            out.append(ReviewProblem(MISSING_DIMENSION, i, fp,
                                     f"#{i} {Reviewer._label(item, i)}: 缺观察维度"))
        if not item.get("severity"):
            out.append(ReviewProblem(MISSING_SEVERITY, i, fp,
                                     f"{Reviewer._label(item, i)}: 未分级(缺 severity)——Analyst 未给出威胁判定"))
        if not item.get("reasons"):
            out.append(ReviewProblem(EMPTY_REASONS, i, fp,
                                     f"{Reviewer._label(item, i)}: 无威胁判定理由(reasons 空)——分级不可解释"))
        if not fp:
            out.append(ReviewProblem(MISSING_FP, i, None,
                                     f"{Reviewer._label(item, i)}: 缺身份 fp——无法回溯到本期 diff 事件(grounding 前置)"))
        return out

    # ---------------- grounding：编造 URL / 凭空 fp ----------------
    @staticmethod
    def _unsourced_urls(item: dict, i: int, card_urls: set[str]) -> list[ReviewProblem]:
        bad = [u for u in item.get("evidence_urls", []) if u not in card_urls]
        if not bad:
            return []
        return [ReviewProblem(
            UNSOURCED_URL, i, item.get("fp"),
            f"{Reviewer._label(item, i)}: 出处不在本期采集卡内(evidence_urls 含未采集 URL：{'、'.join(bad)})"
            f"——疑似编造来源，grounding 拦截")]

    @staticmethod
    def _ungrounded_fp(item: dict, i: int, change_fps: set[str]) -> list[ReviewProblem]:
        fp = item.get("fp")
        if fp in change_fps:
            return []
        return [ReviewProblem(
            UNGROUNDED_FP, i, fp,
            f"{Reviewer._label(item, i)}: fp={fp} 不在本期 diff 变更集——条目不是本期真变更，"
            f"疑似编造(grounding 拦截)")]

    # ---------------- grounding：理由声称命中的词必须真在正文 ----------------
    @staticmethod
    def _claim_words(item: dict, i: int) -> list[ReviewProblem]:
        """理由里的"命中信号词：X、Y"必须真实出现在条目正文（title+summary）。

        这是防张冠李戴的最小核验：不能给一条"正式发布"的动态挂"降价"的理由。
        只解析 rubric 写的 `命中信号词：` 行（其输出契约，见 analyst/rubric.py）；
        规则无信号词（如 feature_update 兜底）则没有该行 → 无可核验，跳过。
        """
        fp = item.get("fp")
        body = f"{item.get('title', '')}\n{item.get('summary', '')}".lower()
        claimed: list[str] = []
        for reason in item.get("reasons") or []:
            if isinstance(reason, str) and reason.startswith(HIT_PREFIX):
                claimed.extend(w for w in reason[len(HIT_PREFIX):].split("、") if w)
        missing = [w for w in claimed if w not in body]
        if not missing:
            return []
        return [ReviewProblem(
            UNGROUNDED_CLAIM, i, fp,
            f"{Reviewer._label(item, i)}: 理由声称命中「{'、'.join(missing)}」但正文没有该词——"
            f"理由与条目文本不符，疑似张冠李戴(grounding 拦截)")]

    # ---------------- 口径合规：与上期重复 ----------------
    @staticmethod
    def _duplicate_vs_unchanged(item: dict, i: int, unchanged: set[str]) -> list[ReviewProblem]:
        fp = item.get("fp")
        if not fp or fp not in unchanged:
            return []
        return [ReviewProblem(
            DUPLICATE, i, fp,
            f"{Reviewer._label(item, i)}: fp={fp} 属上期 unchanged——本期把旧闻又发出来"
            f"(与上期重复，合规拦截)")]

    # ---------------- 矛盾检测：同竞品两条反义 + 同主体 ----------------
    @staticmethod
    def _sides(text: str) -> set[tuple[str, str]]:
        """命中极性词的 (轴, 方向) 集合；同轴两向都中 → 两个方向都在集合里。"""
        out: set[tuple[str, str]] = set()
        for axis, sides in POLARITY_AXES.items():
            for direction, words in sides.items():
                if any(w in text for w in words):
                    out.add((axis, direction))
        return out

    @staticmethod
    def _word(text: str, axis: str, direction: str) -> str:
        for w in POLARITY_AXES[axis][direction]:
            if w in text:
                return w
        return direction

    @classmethod
    def _contradictions(cls, items: list[dict]) -> list[ReviewProblem]:
        """两两比：同轴、方向相反且都命中 + 共享版本号或 ≥3 个共享二元组 → 互斥存疑。"""
        out: list[ReviewProblem] = []
        for a in range(len(items)):
            for b in range(a + 1, len(items)):
                ia, ib = items[a], items[b]
                text_a = f"{ia.get('title', '')}\n{ia.get('summary', '')}".lower()
                text_b = f"{ib.get('title', '')}\n{ib.get('summary', '')}".lower()
                sa, sb = cls._sides(text_a), cls._sides(text_b)
                axes = sorted({axis for axis, _ in sa} | {axis for axis, _ in sb})
                conflict: tuple[str, str, str] | None = None
                for axis in axes:
                    da = sorted({d for ax, d in sa if ax == axis})
                    db = sorted({d for ax, d in sb if ax == axis})
                    if da and db and not (set(da) & set(db)):  # 同轴且方向完全相反
                        conflict = (axis, da[0], db[0])
                        break
                if conflict is None:
                    continue
                anchored = bool(_versions(text_a) & _versions(text_b)) or \
                    len(_bigrams(text_a) & _bigrams(text_b)) >= 3
                if not anchored:
                    continue
                axis, da_dir, db_dir = conflict
                out.append(ReviewProblem(
                    CONTRADICTION, a + 1, ia.get("fp"),
                    f"同竞品条目 #{a + 1}「{ia.get('title')}」({cls._word(text_a, axis, da_dir)}) ↔ "
                    f"#{b + 1}「{ib.get('title')}」({cls._word(text_b, axis, db_dir)})：{axis}相反"
                    f"且共享主体锚——两条互斥存疑，转人工复核(不自动二选一)"))
        return out
