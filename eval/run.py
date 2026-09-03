# -*- coding: utf-8 -*-
"""Eval 运行入口（Phase 8）：跑一次回放评测，写报告 + 门禁判退出码。

用法：
    uv run python -m eval.run
产出（data/ 已 gitignore）：
    data/eval/eval_report.json   —— 全量指标/明细（机器可读、可 diff）
    data/eval/eval_report.md     —— 人读报告（README/docs 引用其中的实测数字）
退出码：0 = 门禁全过；非 0 = 有门禁不过（列出失败项）。阈值见 config.yaml `eval:` 块。

诚实口径：门禁 = **固定语料上的回归基线**（规则确定 + 语料固定 → 本应恒过），
是"防回退"的 CI 式检查，**不是**对真实世界能力的宣称；报告 md 已把该口径写死。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from config.settings import Settings, get_settings
from eval.harness import EvalHarness, render_markdown

_EPS = 1e-9


def check_gates(report: dict, settings: Settings) -> list[str]:
    """按 config eval 阈值核门禁，返回失败项清单（空 = 全过）。"""
    m = report["metrics"]
    cfg = settings.eval
    fails: list[str] = []

    det = m["fabrication_interception"]
    if det["rate"] + _EPS < cfg.detection_threshold:
        fails.append(f"编造拦截率 {det['rate']:.3f} < 阈值 {cfg.detection_threshold}（对抗注入必须全拦）")
    src = m["source_accuracy"]
    if src["rate"] + _EPS < cfg.precision_threshold:
        fails.append(f"来源/grounding 准确率 {src['rate']:.3f} < 阈值 {cfg.precision_threshold}（真实语料 0 编造）")
    st = m["structure_compliance"]
    if st["rate"] + _EPS < cfg.structure_threshold:
        fails.append(f"结构合规率 {st['rate']:.3f} < 阈值 {cfg.structure_threshold}")
    if m["trace_consistent"] is not cfg.require_trace_consistent:
        fails.append(f"trace 计数链一致性 = {m['trace_consistent']}（要求 {cfg.require_trace_consistent}）")
    if m["real_false_positive"] != 0:
        fails.append(f"真实语料误拦 {m['real_false_positive']} 条——真实 W35 必须全 PASS、收件箱空（安全红线）")
    if not m["gold_health_ok"]:
        fails.append(f"gold 标注不健康：{m['gold_health_problems']}")
    return fails


def _fmt_metrics(m: dict) -> str:
    g, fi, src, st = (m["event_agreement"], m["fabrication_interception"],
                      m["source_accuracy"], m["structure_compliance"])
    return (
        f"事件一致率 {g['matched']}/{g['total']} · 编造拦截 {fi['caught']}/{fi['total']} · "
        f"来源准确率 {src['grounded_items']}/{src['total_items']} · 结构合规 {st['ok_runs']}/{st['total_runs']} · "
        f"trace 一致 {'✅' if m['trace_consistent'] else '❌'} · 真实误拦 {m['real_false_positive']}"
    )


def main(settings: Settings | None = None, *, out_dir: str | Path | None = None) -> int:
    try:  # Windows GBK 控制台打印 ✅/中文会崩 → 重配 stdout 为 UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    s = settings or get_settings()
    report = EvalHarness().run()
    out = Path(out_dir) if out_dir else (s.repo_root / s.eval.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "eval_report.md").write_text(render_markdown(report), encoding="utf-8")

    fails = check_gates(report, s)
    print("=" * 70)
    print("competition-watch-engine · Eval-Harness 回放评测")
    print(_fmt_metrics(report["metrics"]))
    print("报告已写入:", out / "eval_report.{json,md}")
    if fails:
        print("门禁失败：")
        for f in fails:
            print("  ❌", f)
        print("=" * 70)
        return 1
    print("门禁全过 ✅（固定语料回归基线，非真实世界能力宣称）")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
