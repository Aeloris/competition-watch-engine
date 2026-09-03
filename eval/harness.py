# -*- coding: utf-8 -*-
"""Eval-Harness（Phase 8 落地）：把 P4–P7 链路当作"回放对象"，做离线确定性评测并出报告 dict。

设计（对齐 README2 §5.11 / §7.4 row 8，全 mock、离线、确定性、无 key）：
1. **真实事件集回放**：用 fixtures 固化语料跑 竞品×(W34 冷启动 → W35 diff) 全链路，
   拿 W35 最终 State 与 gold 人工标注比对（事件一致率）+ 核内部一致性（去重合并/出处/结构/trace 链）；
2. **对抗回放**：6 个编造场景（5 图级 writer 掺假 + 1 门卫级旧闻）测编造拦截率，
   断言每条假情报都被门卫转 human 且落对应 code 的收件箱——绝不进 PASS 态；
3. 产出 **EvalReport(dict)**（JSON-safe），run.py 负责写 data/eval/eval_report.{json,md} 与门禁 exit。

诚实口径：这是**固定语料的回归一致性评测**（规则确定 + fixtures 固定 → 同输入同输出），
不是对真实竞品动态的泛化能力声明；数字=该固定样本实测，不外推。LLM 语义分级 / 跨竞品对比 /
真实 Webhook 均未接入，本评测不假装覆盖这些能力。
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timezone
from pathlib import Path

from config.settings import Settings, get_settings
from eval import adversarial as adv
from eval import dataset
from eval import metrics
from memory import JsonMemoryStore
from orchestration import run_cycle

DEMO_COMPETITORS = dataset.COMPETITORS
DEMO_PERIODS = dataset.PERIODS


def _iso_settings(root: Path) -> Settings:
    """隔离 settings：data_dir 指向临时根，绝不毒化 lru_cache 的全局 get_settings()（P7 同款）。"""
    s = get_settings().model_copy(deep=True)
    s.app.data_dir = str(root)
    return s


def _gold_events_for(comp: str) -> list[dict]:
    return [e for e in dataset.W35_GOLD_EVENTS if e["competitor"] == comp]


def _gold_groups_for(comp: str) -> list[dict]:
    return [g for g in dataset.DUPLICATE_GROUPS if g["competitor"] == comp]


def replay_real_two_weeks(iso: Settings, store) -> dict:
    """真实回放：两竞品 × (W34 冷启动 → W35 diff)，返回 {comp: {period: final_state}}。"""
    out: dict = {}
    for comp in DEMO_COMPETITORS:
        out[comp] = {}
        for period in DEMO_PERIODS:
            out[comp][period] = run_cycle(iso, comp, period, store=store, persist=False)
    return out


class EvalHarness:
    """回放式评测器：run() 一次给出真实回放指标 + 对抗拦截指标 + 门禁判定输入。"""

    def run(self, settings: Settings | None = None, *, tmp_root: str | None = None) -> dict:
        s = settings or get_settings()
        root = Path(tempfile.mkdtemp(prefix="cwe-eval-")) if tmp_root is None else Path(tmp_root)
        iso = _iso_settings(root)

        # ---------- 1. 真实事件集回放 ----------
        store = JsonMemoryStore(root / "memory-real")
        real = replay_real_two_weeks(iso, store)

        gold_health = dataset.gold_health_check()
        per_comp: list[dict] = []
        matched_gold = total_gold = 0
        grounded_ok = grounded_total = 0
        struct_ok = struct_total = 0
        fp_violations: list[str] = []
        trace_fail_reasons: list[str] = []
        groups_all = count_ok_all = True

        for comp in DEMO_COMPETITORS:
            w35 = real[comp]["2026-W35"]
            w34 = real[comp]["2026-W34"]
            draft = w35.get("draft", {})
            items = draft.get("items", [])
            gold_evs = _gold_events_for(comp)

            matched, total = metrics.match_gold(items, gold_evs)
            matched_gold += matched
            total_gold += total

            merged_events = w35.get("events", [])
            groups = _gold_groups_for(comp)
            groups_all = groups_all and metrics.dedupe_groups_ok(merged_events, groups)
            count_ok_all = count_ok_all and metrics.event_count_matches(merged_events, len(gold_evs))

            card_urls = {c.get("source_url") for c in w35.get("cards", [])}
            ok, n = metrics.grounded_fraction(items, card_urls)
            grounded_ok += ok
            grounded_total += n

            # 结构合规：四个真实 run（两竞品 × 两期）都过 WeeklyReport schema
            for f in (w34, w35):
                struct_total += 1
                if metrics.structure_pass(f.get("draft", {})):
                    struct_ok += 1

            trace_ok, reasons = metrics.trace_consistent(w35)
            if not trace_ok:
                trace_fail_reasons.extend(f"{comp}: {r}" for r in reasons)

            gate = w35.get("gate", {})
            verdict = gate.get("verdict")
            inbox = w35.get("human_inbox", [])
            if verdict != "PASS" or inbox:
                fp_violations.append(f"{comp} W35 真实语料 verdict={verdict} inbox={len(inbox)}——不应误拦")

            per_comp.append({
                "competitor": comp,
                "trace_w34": w34.get("trace", {}),
                "trace_w35": w35.get("trace", {}),
                "items_w35": len(items),
                "gold_events": len(gold_evs),
                "event_matched": matched,
                "merged_events": len(merged_events),
                "radar_w35": draft.get("threat_radar", {}),
                "verdict_w35": verdict,
                "inbox_w35": len(inbox),
            })

        # ---------- 2. 对抗回放（编造拦截率） ----------
        adv_rows: list[dict] = []
        caught = 0
        for case in adv.ADVERSARIAL_CASES:
            name, level = case["name"], case["level"]
            astore = JsonMemoryStore(root / f"adv-{name}" / "memory")
            seed = run_cycle(iso, adv.ADV_COMP, adv.SEED_PERIOD, store=astore, run_id=f"adv-{name}-seed")
            if level == "graph":
                final = adv.run_graph_case(iso, astore, case["poison"], run_id=f"adv-{name}")
            else:  # gate 级旧闻重发：old_event 来自真实 W34 回放
                old_event = seed["events"][0]
                final = adv.run_duplicate_gate(old_event)
            ok_case, codes, inbox_codes = metrics.summarize_case(final, case["expected"])
            caught += int(ok_case)
            adv_rows.append({
                "name": name,
                "level": level,
                "expected": sorted(case["expected"]),
                "verdict": (final.get("gate") or {}).get("verdict"),
                "caught": ok_case,
                "gate_codes": sorted(codes),
                "inbox_codes": sorted(inbox_codes),
            })

        # ---------- 3. 指标汇总 ----------
        metrics_out = {
            "event_agreement": {
                "matched": matched_gold, "total": total_gold,
                "rate": (matched_gold / total_gold) if total_gold else 0.0,
            },
            "dedupe": {
                "groups_merged_ok": bool(groups_all and len(dataset.DUPLICATE_GROUPS) > 0),
                "event_count_ok": bool(count_ok_all),
            },
            "source_accuracy": {
                "grounded_items": grounded_ok, "total_items": grounded_total,
                "rate": (grounded_ok / grounded_total) if grounded_total else 0.0,
            },
            "structure_compliance": {
                "ok_runs": struct_ok, "total_runs": struct_total,
                "rate": (struct_ok / struct_total) if struct_total else 0.0,
            },
            "trace_consistent": not trace_fail_reasons,
            "trace_fail_reasons": trace_fail_reasons,
            "fabrication_interception": {
                "caught": caught, "total": len(adv.ADVERSARIAL_CASES),
                "rate": metrics.detection_rate(caught, len(adv.ADVERSARIAL_CASES)),
            },
            "real_false_positive": len(fp_violations),
            "fp_violations": fp_violations,
            "gold_health_ok": not gold_health,
            "gold_health_problems": gold_health,
        }

        return {
            "kind": "cwe-eval-report",
            "run_at": datetime.now(timezone.utc).isoformat(),
            "scope": {"competitors": list(DEMO_COMPETITORS), "periods": list(DEMO_PERIODS),
                      "adversarial_cases": len(adv.ADVERSARIAL_CASES)},
            "metrics": metrics_out,
            "per_competitor": per_comp,
            "adversarial_cases": adv_rows,
        }


# ---------------------------------------------------------------- markdown 渲染
def render_markdown(report: dict) -> str:
    m = report["metrics"]
    g = m["event_agreement"]
    d = m["dedupe"]
    src = m["source_accuracy"]
    st = m["structure_compliance"]
    fi = m["fabrication_interception"]

    lines: list[str] = []
    lines.append("# competition-watch-engine · Eval-Harness 回放评测报告")
    lines.append("")
    lines.append(f"> 生成于 {report['run_at']} · 范围 {report['scope']['competitors']} × "
                 f"{report['scope']['periods']} · 对抗场景 {report['scope']['adversarial_cases']} 个")
    lines.append(">")
    lines.append("> ⚠️ **口径声明**：这是**固定语料（fixtures）的回归一致性评测**——规则确定性 + 语料固定，"
                 "同输入同输出。下列百分比=该固定样本上的实测，**不代表对真实竞品动态的泛化能力**；"
                 "LLM 语义补分级 / 跨竞品横向对比 / 真实 Webhook 推送未接入，本评测不假装覆盖。")
    lines.append("")

    lines.append("## 一、六项指标（实测，确定性）")
    lines.append("")
    lines.append("| 指标 | 数值 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 事件一致率 | {g['rate']:.3f} ({g['matched']}/{g['total']}) | gold 标注的真实 W35 事件，"
                 "引擎条目标题命中锚且 severity 一致 |")
    lines.append(f"| 去重合并一致 | {'✅' if d['groups_merged_ok'] else '❌'} 组全并 / "
                 f"{'✅' if d['event_count_ok'] else '❌'} 事件数一致 | 多源同事件并入一条（crm 3 源、bi 2 源），不过并/欠并 |")
    lines.append(f"| 来源/grounding 准确率 | {src['rate']:.3f} ({src['grounded_items']}/{src['total_items']}) | "
                 "每条 evidence_url ⊆ 本期采集卡（闭环自证，0 编造） |")
    lines.append(f"| 结构合规率 | {st['rate']:.3f} ({st['ok_runs']}/{st['total_runs']}) | 周报 draft 通过 WeeklyReport schema |")
    lines.append(f"| trace 计数链一致 | {'✅' if m['trace_consistent'] else '❌'} | "
                 "collected→merged→diff 与 cards/events/changes/雷达/items 逐项自洽 |")
    lines.append(f"| 编造拦截率 | {fi['rate']:.3f} ({fi['caught']}/{fi['total']}) | 对抗注入场景被门卫转 human 且落对应 code |")
    lines.append(f"| 真实语料误拦 | {m['real_false_positive']} 条 | 真实 W35 全 PASS、收件箱空（0 误拦） |")
    lines.append("")

    lines.append("## 二、真实回放明细（按竞品）")
    lines.append("")
    lines.append("| 竞品 | W34 trace | W35 trace | items | gold | 命中 | radar | verdict | inbox |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in report["per_competitor"]:
        t34 = r["trace_w34"]
        t35 = r["trace_w35"]
        lines.append(
            f"| {r['competitor']} | {t34.get('collected')}→{t34.get('merged')}→{t34.get('diff')} | "
            f"{t35.get('collected')}→{t35.get('merged')}→{t35.get('diff')} | {r['items_w35']} | "
            f"{r['gold_events']} | {r['event_matched']} | {r['radar_w35']} | {r['verdict_w35']} | "
            f"{r['inbox_w35']} |"
        )
    lines.append("")

    lines.append("## 三、对抗场景（编造拦截逐例）")
    lines.append("")
    lines.append("| 场景 | 级别 | 期望 code | verdict | 拦截 | 门卫判定 codes |")
    lines.append("|---|---|---|---|---|---|")
    for c in report["adversarial_cases"]:
        lines.append(f"| {c['name']} | {c['level']} | {','.join(c['expected'])} | {c['verdict']} | "
                     f"{'✅' if c['caught'] else '❌'} | {','.join(c['gate_codes']) or '-'} |")
    lines.append("")

    lines.append("## 四、局限与边界")
    lines.append("")
    lines.append("- 语料 = fixtures 两周固定样例；gold = 人工读原文独立标注（不反推引擎输出）；")
    lines.append("- 全 mock 确定性回放；Analyst 威胁分级 / Writer 周报均为规则实现，无 LLM 调用 → 无 token 成本项；")
    lines.append("- DUPLICATE（旧闻重发）真实两期话题不重叠、unchanged 为空，以门卫级注入复现同类漏洞（级别列已标）；")
    lines.append("- 门禁阈值 = CI 回归基线（固定样本上本应恒过），非对真实世界能力的宣称。")
    lines.append("")
    return "\n".join(lines)
