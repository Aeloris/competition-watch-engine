# -*- coding: utf-8 -*-
"""人工放行 / 发布视图（Phase 8）：把"先审后发"从只读收件箱推进到**可闭环**。

README2 §7.4 row 8"简单面板收件箱人工放行"落地，三个纯 service 函数（HTTP 层薄，路由只翻译状态码）：

1. **list_inbox_entries**：把各 run 落进 human_inbox 的问题条目摊平成"可处理视图"
   （带稳定 entry_id = `{run_id}#{seq}`），并按 resolution 推导 status：
   - `resolution` 不存在或 action=note（仅备注）→ **pending**（未销案，默认查询/面板待办看它）；
   - action ∈ {release, dismiss}（终态）→ **resolved**（已人工定论，审计留痕不删除）。

2. **resolve_inbox_entry**：人工对一条待办给出 action。
   - `release`：放行 —— 该条目对应的周报条目进入发布视图（human 复核认可）；
   - `dismiss`：驳回 —— 对应条目从发布视图剔除（人工认定是噪音/误报）；
   - `note`：备注 —— 只留批注，**不销案**（仍 pending，提醒还在）。
   幂等写回：改 `run.human_inbox[seq]["resolution"]` 后经 task_store.update_run（锁内 读-改-写，
   先重判终态再原位替换该 run，顺序不动 → entry_id 稳定可复用）落盘；并发定案同一收件箱条目，
   后到者在锁内看到先手已定案 → 409（不许静默覆盖审计决定）。

3. **published_view**：给定 Task，把每个 done run 的周报 draft 按 resolution 过滤成**发布视图**：
   - 未被任何门卫问题指向的条目 → 自动发布（自审通过，human_released=false）；
   - 被指向且全部指向条目已 release → 发布（human_released=true，经人工放行）；
   - 被指向且任一指向已 dismiss → 从发布剔除（进 dismissed 审计区，不回补）；
   - 被指向但仍有指向未终态（pending/note）→ 挂起不发布（进 held，等人工）。
   门卫指向条目的锚 = ReviewProblem 的 item_index（1 基，写入 human_inbox 即该 run 终稿条序）；
   指向落在 draft 之外的条目（理论不发生）单独收 unmapped 审计。

诚实口径：这里是"发布**视图**"（放行/驳回决策的可复核快照），不是真对外推送；
真分发（邮件/企微/飞书）未接，不假装已发。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from service.schemas import RunStatus
from service.store import TaskStore, utc_now_iso

# 人工动作：release/dismiss = 终态（销案）；note = 仅备注（不销案，仍待办）
TERMINAL_ACTIONS = ("release", "dismiss")
ALL_ACTIONS = TERMINAL_ACTIONS + ("note",)


class EntryNotFound(LookupError):
    """收件箱条目不存在（task/run/seq 定位不到）。路由映射 404。"""


class EntryAlreadyResolved(RuntimeError):
    """条目已被 release/dismiss 定案；再处理会静默覆盖审计决定 → 路由映射 409。"""

    def __init__(self, action: str, by: str, resolved_at: str) -> None:
        super().__init__(f"该条目已定案(action={action}, by={by}, at={resolved_at})；如需改判请走审计另案")
        self.action = action
        self.by = by
        self.resolved_at = resolved_at


def _resolution_status(resolution: dict | None) -> str:
    """由 resolution 推导条目状态：终态 action → resolved；缺省 / 仅 note → pending。"""
    if resolution and resolution.get("action") in TERMINAL_ACTIONS:
        return "resolved"
    return "pending"


def _entry_view(task, run, seq: int, entry: dict) -> dict:
    """一条收件箱条目的可处理视图：task/run 上下文 + 门卫问题字段 + entry_id + resolution。"""
    return {
        "entry_id": f"{run.run_id}#{seq}",  # run_id 全局唯一 + 箱内序号 → 稳定、可 POST 回来
        "task_id": task.task_id,
        "period": task.period,
        "run_id": run.run_id,
        "competitor_id": run.competitor_id,
        "item_index": entry.get("item_index"),  # 该 run 周报 draft 里 1 基条目序号（published_view 锚）
        "fp": entry.get("fp"),
        "code": entry.get("code"),
        "detail": entry.get("detail"),
        "attempts": entry.get("attempts"),
        "status": _resolution_status(entry.get("resolution")),
        "resolution": entry.get("resolution"),  # None（未处理）或 {action, by, note, resolved_at}
    }


def parse_entry_id(entry_id: str) -> tuple[str, int]:
    """把 `{run_id}#{seq}` 拆回 (run_id, seq)；格式不对抛 ValueError（路由映射 422）。"""
    if "#" not in entry_id:
        raise ValueError(f"entry_id 格式应为 `{{run_id}}#{{seq}}`，收到 {entry_id!r}")
    run_id, _, seq_s = entry_id.rpartition("#")
    if not run_id or not seq_s.isdigit():
        raise ValueError(f"entry_id 无法解析：{entry_id!r}")
    return run_id, int(seq_s)


def list_inbox_entries(task_store: TaskStore, *, status: str = "pending") -> list[dict]:
    """摊平全部 human run 的收件箱条目为处理视图；按 status(pending|all|resolved) 过滤。"""
    views: list[dict] = []
    for task in task_store.list_tasks():  # 已按创建时间倒序
        for run in task.runs:
            if run.verdict != "human":
                continue
            for seq, entry in enumerate(run.human_inbox):
                view = _entry_view(task, run, seq, entry)
                if status != "all" and view["status"] != status:
                    continue
                views.append(view)
    return views


def _task_containing_run(task_store: TaskStore, run_id: str) -> tuple[Any, Any]:
    """在全部 task 里找含该 run 的 (task, run)；找不到 → EntryNotFound。

    entry_id 以 run_id 为前缀（全局唯一），故 task 层可反向定位——调用方不必先知道 task_id。
    """
    for task in task_store.list_tasks():
        for run in task.runs:
            if run.run_id == run_id:
                return task, run
    raise EntryNotFound(f"run {run_id} 不属于任何已知 task")


def resolve_inbox_entry(
    task_store: TaskStore,
    entry_id: str,
    *,
    action: str,
    by: str,
    note: str = "",
) -> dict:
    """人工对一条待办定案：锁内写回 resolution（update_run 幂等覆盖该 run）；返回定案后的条目视图。

    Raises:
        ValueError: action 非法 / by 为空（路由通常已被 pydantic Literal/min_length 拦成 422）。
        EntryNotFound: task / run / 箱内序号定位不到。
        EntryAlreadyResolved: 已是 release/dismiss 终态（不许覆盖审计决定）。
    """
    if action not in ALL_ACTIONS:
        raise ValueError(f"action 必须是 {list(ALL_ACTIONS)} 之一，收到 {action!r}")
    if not (by or "").strip():
        raise ValueError("by（处理人）不能为空——人工定案必须可追责")

    run_id, seq = parse_entry_id(entry_id)
    task, _ = _task_containing_run(task_store, run_id)  # 只反查 task_id；权威校验放在锁内 update_run

    def _mutate(run):
        """锁内对**最新快照**重做终态判定再写 resolution——两线程并发定案同一条目时，
        后到者在锁内读到先手已落盘的 resolution → 抛 EntryAlreadyResolved（409），
        不会静默覆盖先手审计决定（旧实现在锁外先判终态 = TOCTOU 竞态）。"""
        if not (0 <= seq < len(run.human_inbox)):
            raise EntryNotFound(f"箱内序号 {seq} 不存在（run {run_id}，共 {len(run.human_inbox)} 条）")
        existing = run.human_inbox[seq].get("resolution")
        if existing and existing.get("action") in TERMINAL_ACTIONS:
            raise EntryAlreadyResolved(existing["action"], existing.get("by", ""), existing.get("resolved_at", ""))
        run.human_inbox[seq]["resolution"] = {"action": action, "by": by, "note": note or "",
                                              "resolved_at": utc_now_iso()}
        return run

    try:
        updated, run2 = task_store.update_run(task.task_id, run_id, _mutate)
    except KeyError as exc:  # 扫描后 run 被并发移除/已不在该 task → 视图层按 404 处理
        raise EntryNotFound(f"run {run_id} 不存在于 task {task.task_id}") from exc
    return _entry_view(updated, run2, seq, run2.human_inbox[seq])


# ---------------------------------------------------------------- 发布视图
def _problem_slim(entry: dict) -> dict:
    """published_view 里"指向某条目的门卫问题"的展示切片（含解析链路，可回查收件箱）。"""
    resolution = entry.get("resolution")
    return {
        "code": entry.get("code"),
        "detail": entry.get("detail"),
        "fp": entry.get("fp"),
        "status": _resolution_status(resolution),
        "resolution": resolution,
    }


def _load_run_draft(settings, run_id: str) -> tuple[list[dict] | None, str | None]:
    """从 data/pipeline/{run_id}.json 读该 run 终稿 items；缺失/损坏返回 (None, 原因)。"""
    p: Path = settings.data_path / "pipeline" / f"{run_id}.json"
    if not p.exists():
        return None, f"pipeline 落盘缺失：{p.name}（run 全量 state 未持久化，发布视图无 draft 可过滤）"
    try:
        final = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"pipeline 文件损坏：{type(exc).__name__}: {exc}"
    return (final.get("draft") or {}).get("items") or [], None


def published_view(task_store: TaskStore, settings, task_id: str) -> dict:
    """一个 Task 的发布视图：逐 done run 把 draft 按 resolution 过滤成 published/held/dismissed。"""
    task = task_store.get_task(task_id)
    if task is None:
        raise EntryNotFound(f"task {task_id} 不存在")

    run_views: list[dict] = []
    for run in task.runs:
        base = {
            "competitor_id": run.competitor_id,
            "run_id": run.run_id,
            "period": run.period,
            "status": run.status.value,
            "verdict": run.verdict,
            "trace": run.trace,
            "threat_radar": run.threat_radar,
            "published": [], "held": [], "dismissed": [], "unmapped": [],
            "published_count": 0, "held_count": 0, "dismissed_count": 0,
            "note": None,
        }
        if run.status != RunStatus.done:
            base["note"] = "该 run 未成功，无发布视图（draft 缺 / run 失败不产出可发布周报）"
            run_views.append(base)
            continue

        items, err = _load_run_draft(settings, run.run_id)
        if err is not None:
            base["note"] = err
            run_views.append(base)
            continue

        # 按 item_index（1 基）把门卫指向分组到条目
        flagged: dict[int, list[dict]] = {}
        unmapped: list[dict] = []
        for seq, entry in enumerate(run.human_inbox):
            slim = _problem_slim(entry)
            slim["entry_id"] = f"{run.run_id}#{seq}"
            idx = entry.get("item_index")
            if isinstance(idx, int) and 1 <= idx <= len(items):
                flagged.setdefault(idx, []).append(slim)
            else:
                unmapped.append(slim)  # 指向 draft 之外：理论不发生，仍审计展示

        for idx, item in enumerate(items, start=1):
            refs = flagged.get(idx, [])
            if not refs:  # 自审通过 → 自动发布
                base["published"].append({**item, "human_released": False, "resolution": None})
                continue
            if any(r["status"] == "resolved" and r["resolution"]["action"] == "dismiss" for r in refs):
                base["dismissed"].append({"item": item, "problems": refs})   # 人工否决 → 剔除
                continue
            if not all(r["status"] == "resolved" and r["resolution"]["action"] == "release" for r in refs):
                base["held"].append({"item": item, "problems": refs})        # 仍有未放行 → 挂起
                continue
            base["published"].append({**item, "human_released": True,
                                      "resolution": refs[0]["resolution"]})  # 全部放行 → 发布
        base["unmapped"] = unmapped
        base["published_count"] = len(base["published"])
        base["held_count"] = len(base["held"])
        base["dismissed_count"] = len(base["dismissed"])
        run_views.append(base)

    return {
        "task_id": task_id,
        "period": task.period,
        "task_status": task.status.value,
        "summary": task.summary,
        "runs": run_views,
        "published_total": sum(r["published_count"] for r in run_views),
        "held_total": sum(r["held_count"] for r in run_views),
        "dismissed_total": sum(r["dismissed_count"] for r in run_views),
    }
