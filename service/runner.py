# -*- coding: utf-8 -*-
"""服务运行器（Phase 7）：把一个 Task（本期 × 多竞品）跑完并记进 TaskStore。

职责（README2 §5.10"服务层、健壮性与观测"落地）：
- **逐竞品 run_cycle**：同一 Task 内每个竞品一个独立 Run，**各自 try/except** —— 单个竞品挂了
  记 failed + error、其余竞品照常出结果 = 采集层"源级隔离"升级到服务层"run 级隔离"；
- **trace 计数链收口**：把每 run 的 trace{collected,merged,diff} + 门卫判定（verdict/gate_problems/
  gate_trace_count/inbox）抄进 RunMeta —— README2 §5.10 的"采集 N→去重 M→diff K→门卫拦 X"在服务层可查；
- **告警**：门卫 PASS 且威胁雷达有 high（且 schedule.alert_on_high_threat=True）→ 高威胁即时告警；
  门卫 human → 人工收件箱告警；run 异常 → 失败告警。全交 notifier（默认 Mock 落台账，离线可演示）。

运行确定性：period 缺省 = 最近一个已结束 ISO 周（对齐 cron 周一 09:00 出上周周报的口径）；
fetched_at 缺省由 Planner.default_fetched_at 给出（周期结束后周一 09:00）→ 同 period 同输入即同结果。
数据落点：每 run 全量 state → data/pipeline/{run_id}.json（Phase 4 persist 复用，可回放）；
Task 元数据 → TaskStore（data/service/tasks/{task_id}.json）。两层分开，不重复存大对象。
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from config.settings import Settings
from orchestration.pipeline import run_cycle
from service.notifier import Notifier, make_alert
from service.schemas import RunMeta, TaskMeta, RunStatus
from service.store import TaskStore, utc_now_iso


def last_completed_iso_week(today: date) -> str:
    """今天为止"最近一个已结束的 ISO 周"（"YYYY-Www"）。

    定义：上上周一之前那个完整过去的周 = 本周往回调 (weekday+7) 天到它所在周的周一，
    取其 ISO 年/周号。cron "0 9 * * mon" 周一 09:00 触发时它恰好报"上周"。
    例：2026-09-04(周五) → "2026-W35"；2026-09-07(周一) → "2026-W36"。
    """
    monday_of_completed = today - timedelta(days=today.weekday() + 7)
    iso = monday_of_completed.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def available_competitors(settings: Settings) -> list[str]:
    """可用竞品 = fixtures mock 目录里实际存在的信源 json 的竞品名（离线底座，确定性）。"""
    mock_dir = settings.repo_root / settings.sources.mock_dir
    return sorted(p.stem for p in mock_dir.glob("*.json") if p.is_file())


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class ServiceRunner:
    """一次调度/手动触发"本期巡检"的执行器。构造时注入 settings/task_store/notifier（可测试替换）。"""

    def __init__(
        self,
        settings: Settings,
        task_store: TaskStore,
        notifier: Notifier,
    ) -> None:
        self.settings = settings
        self.task_store = task_store
        self.notifier = notifier

    def execute_task(
        self,
        task_id: str,
        *,
        period: str | None = None,
        competitors: list[str] | None = None,
        fetched_at: datetime | None = None,
    ) -> TaskMeta:
        """跑一个 Task：period 缺省 = 最近已结束 ISO 周；competitors 缺省 = 全部可用竞品。

        返回 TaskStore 里的终态 TaskMeta（顺序执行 + 同步返回终态 = curl 就能演示）。
        """
        period = period or last_completed_iso_week(date.today())
        comps = list(competitors) if competitors is not None else available_competitors(self.settings)
        created_at = utc_now_iso()

        self.task_store.create_task(
            task_id, period=period, competitors_requested=comps, created_at=created_at
        )

        if not comps:
            return self.task_store.finalize(
                task_id,
                finished_at=created_at,
                summary={"runs": 0, "done": 0, "failed": 0, "human": 0, "inbox_total": 0, "chains": []},
                error="无可用竞品（fixtures/sources 为空或目录不存在）",
            )

        for comp in comps:
            self._run_one(task_id, comp, period, fetched_at=fetched_at)

        return self._finalize(task_id)

    # ------------------------------------------------------------ 单竞品 run
    def _run_one(
        self,
        task_id: str,
        competitor_id: str,
        period: str,
        *,
        fetched_at: datetime | None,
    ) -> RunMeta:
        run_id = _new_run_id()
        started = utc_now_iso()
        run = RunMeta(
            run_id=run_id,
            competitor_id=competitor_id,
            period=period,
            status=RunStatus.running,
            started_at=started,
        )
        try:
            final = run_cycle(
                self.settings,
                competitor_id,
                period,
                run_id=run_id,
                fetched_at=fetched_at,
                persist=True,
            )
            if final.get("collection_gap"):
                # 全源失败 + 零卡 = 本轮"没看到世界"，不是"世界没变化"：上游已跳过覆盖上期快照
                # （防把 diff 锚冲空、下期把旧闻再当新增）。这里把 run 记 failed——别让空周报冒充
                # 干净 PASS（采集坏了 ≠ 无事发生），错误信息把"为什么没数据"留给审计。
                failures = (final.get("collect_summary") or {}).get("failures") or []
                run.status = RunStatus.failed
                run.error = ("本期采集全源失败（零卡）——未观测到数据，不产出空周报；"
                             "上期快照已保留。failures=" + "; ".join(failures))
                run.finished_at = utc_now_iso()
                self._alert(
                    "run_failed",
                    summary=f"竞品 {competitor_id} {period} 采集失败（零数据，无周报）",
                    competitor_id=competitor_id,
                    period=period,
                    run_id=run_id,
                    payload={"error": run.error, "failures": failures},
                )
            else:
                run.status = RunStatus.done
                run = self._fill_run_meta(run, final)
                self._alert_for(run, final)
        except Exception as exc:  # run 级失败隔离：记 failed + error，不中断同一 Task 的其它竞品
            run.status = RunStatus.failed
            run.error = f"{type(exc).__name__}: {exc}"
            run.finished_at = utc_now_iso()
            self._alert(
                "run_failed",
                summary=f"竞品 {competitor_id} {period} 运行失败",
                competitor_id=competitor_id,
                period=period,
                run_id=run_id,
                payload={"error": run.error},
            )
        finally:
            self.task_store.add_run(task_id, run)
        return run

    def _fill_run_meta(self, run: RunMeta, final: dict) -> RunMeta:
        """从最终 State 抄 trace / 门卫判定 / 雷达 / 收件箱（计数链 + 先审后发结果）。"""
        run.trace = final.get("trace") or {}
        run.diff_summary = final.get("diff_summary") or {}
        gate = final.get("gate") or {}
        run.verdict = gate.get("verdict")
        run.gate_problems = len(gate.get("problems") or [])
        run.gate_trace_count = len(final.get("gate_trace") or [])
        inbox = final.get("human_inbox") or []
        run.human_inbox = list(inbox)
        run.inbox_count = len(inbox)
        draft = final.get("draft") or {}
        run.threat_radar = dict(draft.get("threat_radar") or {})
        # 采集期有信源失败但仍有数据产出（部分失败）：如实记录失败源数，别把"缺数据"当"没问题"
        collect = final.get("collect_summary") or {}
        run.collect_failures = len(collect.get("failures") or [])
        run.finished_at = utc_now_iso()
        return run

    # ------------------------------------------------------------ 告警
    def _alert_for(self, run: RunMeta, final: dict) -> None:
        if run.verdict == "human":
            self._alert(
                "human_inbox",
                summary=f"门卫转人工：{run.competitor_id} {run.period} 有 {run.inbox_count} 条需人工确认",
                competitor_id=run.competitor_id,
                period=run.period,
                run_id=run.run_id,
                payload={"problems": final.get("gate", {}).get("problems") or []},
            )
            return
        high = (run.threat_radar or {}).get("high", 0) or 0
        if high > 0 and self.settings.schedule.alert_on_high_threat:
            self._alert(
                "high_threat",
                summary=f"本期高威胁 {high} 条（不等周报即时提醒）",
                competitor_id=run.competitor_id,
                period=run.period,
                run_id=run.run_id,
                payload={"threat_radar": run.threat_radar},
            )

    def _alert(
        self,
        event_type: str,
        *,
        summary: str,
        competitor_id: str,
        period: str,
        run_id: str,
        payload: dict | None = None,
    ) -> None:
        event = make_alert(
            event_type,
            summary=summary,
            competitor_id=competitor_id,
            period=period,
            run_id=run_id,
            payload=payload,
        )
        try:
            self.notifier.deliver(event)
        except Exception as exc:  # 告警尽力而为：占位壳(NotImplemented)/台账 IO 等任何投递失败
            print(f"[alert-skip] {event_type}（{type(exc).__name__}: {exc}）")  # 都不拖垮巡检 / 翻案 run

    # ------------------------------------------------------------ 收口
    def _finalize(self, task_id: str) -> TaskMeta:
        meta = self.task_store.get_task(task_id)
        if meta is None:
            raise KeyError(f"task {task_id} 不存在")
        done = [r for r in meta.runs if r.status == RunStatus.done]
        failed = [r for r in meta.runs if r.status == RunStatus.failed]
        human = [r for r in done if r.verdict == "human"]
        inbox_total = sum(r.inbox_count for r in done)
        chains = [
            {
                "competitor_id": r.competitor_id,
                "trace": r.trace,
                "verdict": r.verdict,
                "gate_problems": r.gate_problems,
                "inbox": r.inbox_count,
            }
            for r in meta.runs
        ]
        summary = {
            "runs": len(meta.runs),
            "done": len(done),
            "failed": len(failed),
            "human": len(human),
            "inbox_total": inbox_total,
            "chains": chains,
        }
        return self.task_store.finalize(task_id, finished_at=utc_now_iso(), summary=summary)


def run_now(runner: ServiceRunner, *, period: str | None = None) -> TaskMeta:
    """"跑一次"便捷入口：自建 task_id 立即执行（手动触发 / 调度 job / 演示用）。"""
    return runner.execute_task(_new_run_id(), period=period)
