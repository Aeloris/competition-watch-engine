# -*- coding: utf-8 -*-
"""任务/告警持久化（Phase 7）：文件即状态，零外部依赖。

- 任务：data/service/tasks/{task_id}.json（每 Task 一个文件，TaskMeta 全量可读）；
- 告警台账：data/service/alerts.json（AlertEvent 数组，MockNotifier 写、GET /alerts 读）。

同一 (竞品, 期) 重跑 = 新 task_id + 新 run_id → 两个 Task 文件并列 = 审计留痕（不幂等覆盖，
因为服务层关心"每次调度都发生过什么"；幂等覆盖是 Phase 1 memory 写快照的职责）。
写文件一律原子化（先写 .tmp 再 os.replace），单进程内防半截 JSON。
"""
from __future__ import annotations

import itertools
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

# 单进程内写文件锁：append_alert 的"读-改-写"与并发 deliver 互斥（FastAPI 线程池并发下防丢写）
_alert_lock = threading.Lock()
# task 文件（add_run/finalize/update_run）的"读-改-写"整段互斥：并发下防两个线程各读旧快照、
# 各自写回 → 后写覆盖先写（丢 run / 丢终态 / 静默覆盖审计决定）。单全局锁：本存储规模小，
# 正确性优先，不同 task 间串行也可接受（与 _alert_lock 各管各的文件，互不交叉）。
_task_lock = threading.Lock()
_tmp_seq = itertools.count()

from service.schemas import RunMeta, TaskMeta, TaskStatus


def utc_now_iso() -> str:
    """UTC 时间戳（ISO）。服务层记录"何时创建/完成/告警"，测试只断言存在与顺序，不断言精确值。"""
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # 唯一 tmp 名（pid + 自增序号）：固定 ".tmp" 会让上次异常残留的 tmp 被本调用直接吞掉，
    # 多进程/并发下更可能互相 rename 到对方半成品。唯一名保证 os.replace 目标只有最终文件。
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{next(_tmp_seq)}.tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


class TaskStore:
    """TaskMeta 的文件存储：create/get/patch/add_run/list。settings 决定 data/service/ 根目录。"""

    def __init__(self, settings) -> None:
        self._settings = settings
        self._tasks_dir: Path = settings.data_path / "service" / "tasks"

    # ------------------------------------------------------------ task 文件
    def _task_path(self, task_id: str) -> Path:
        return self._tasks_dir / f"{task_id}.json"

    def create_task(
        self,
        task_id: str,
        *,
        period: str,
        competitors_requested: list[str],
        created_at: str,
    ) -> TaskMeta:
        meta = TaskMeta(
            task_id=task_id,
            period=period,
            competitors_requested=list(competitors_requested),
            status=TaskStatus.running,
            created_at=created_at,
        )
        _atomic_write_json(self._task_path(task_id), meta.as_dict())
        return meta

    def get_task(self, task_id: str) -> TaskMeta | None:
        p = self._task_path(task_id)
        if not p.exists():
            return None
        try:
            return TaskMeta.model_validate(_read_json(p, {}))
        except Exception:
            # 损坏/半截任务文件按"不存在"读（与 list_tasks 跳过损坏文件同口径）：单条坏文件
            # 不应把 GET /tasks/{id} /published /compare、面板任务页打成 500。
            return None

    def _save(self, meta: TaskMeta) -> None:
        _atomic_write_json(self._task_path(meta.task_id), meta.as_dict())

    def add_run(self, task_id: str, run: RunMeta) -> TaskMeta:
        """把一次 run 的结果记进 Task（顺序执行：追加即终态）。返回更新后的 Task。

        锁内 读-改-写：与 finalize/update_run 互斥，FastAPI 线程池并发下防丢 run（见 _task_lock）。
        """
        with _task_lock:
            meta = self.get_task(task_id)
            if meta is None:
                raise KeyError(f"task {task_id} 不存在")
            # 同名 run_id 重复记则**原位覆盖**（幂等防重放）；否则追加。
            # 注意不能写成"去重后追加到尾部"——那会把重跑记录挪到末尾，打乱 runs 与真实执行顺序的对应
            runs = list(meta.runs)
            for i, r in enumerate(runs):
                if r.run_id == run.run_id:
                    runs[i] = run
                    break
            else:
                runs.append(run)
            meta.runs = runs
            self._save(meta)
            return meta

    def finalize(
        self,
        task_id: str,
        *,
        finished_at: str,
        summary: dict,
        error: str | None = None,
    ) -> TaskMeta:
        """跑完全部 run 后定 Task 终态：completed / partial / failed。

        锁内 读-改-写：与并发 add_run（追加新 run）互斥，防 finalize 读旧快照把刚加的 run 覆盖丢。
        """
        with _task_lock:
            meta = self.get_task(task_id)
            if meta is None:
                raise KeyError(f"task {task_id} 不存在")
            done = [r for r in meta.runs if r.status.value == "done"]
            failed = [r for r in meta.runs if r.status.value == "failed"]
            if not meta.runs or len(failed) == len(meta.runs):
                status = TaskStatus.failed
            elif failed:
                status = TaskStatus.partial
            else:
                status = TaskStatus.completed
            meta.status = status
            meta.finished_at = finished_at
            meta.summary = summary
            meta.error = error
            self._save(meta)
            return meta

    def list_tasks(self) -> list[TaskMeta]:
        if not self._tasks_dir.exists():
            return []
        metas = []
        for p in self._tasks_dir.glob("*.json"):
            try:
                metas.append(TaskMeta.model_validate(_read_json(p, {})))
            except Exception:  # 半截/损坏文件：跳过并在读取层容忍（审计日志以可读为准）
                continue
        metas.sort(key=lambda m: m.created_at, reverse=True)
        return metas

    def update_run(self, task_id: str, run_id: str, mutator) -> tuple[TaskMeta, RunMeta]:
        """对 task 内某个 run 做**锁内 读-改-写**：读最新快照 → mutator(run) → 原位替换保存。

        场景：人工定案（publish.resolve_inbox_entry）需"读到最新 resolution → 判是否已终态 →
        写回"整段原子；否则两线程并发定案同一收件箱条目会 TOCTOU——双双读到 pending →
        后写覆盖前写的审计决定（先手 release 被后手 dismiss 静默改掉）。

        mutator 对传入的 run 就地改并返回 run；抛异常则**不改盘**（EntryNotFound /
        EntryAlreadyResolved 从锁内直接透传）。run 不在 task → KeyError。
        """
        with _task_lock:
            meta = self.get_task(task_id)
            if meta is None:
                raise KeyError(f"task {task_id} 不存在")
            runs = list(meta.runs)
            for i, r in enumerate(runs):
                if r.run_id == run_id:
                    new_run = mutator(r)
                    runs[i] = new_run
                    meta.runs = runs
                    self._save(meta)
                    return meta, new_run
            raise KeyError(f"run {run_id} 不存在于 task {task_id}")


# ------------------------------------------------------------ 告警台账
def alerts_path(settings) -> Path:
    return settings.data_path / "service" / "alerts.json"


def read_alerts(settings) -> list[dict]:
    p = alerts_path(settings)
    if not p.exists():
        return []
    try:
        return _read_json(p, [])
    except (ValueError, OSError):
        # 台账损坏（外部手改/半截）按"空台账"读：GET /alerts 不 500；下次 append_alert
        # 读到损坏仍由本函数兜底为空后整体重写 → 自愈。
        return []


def append_alert(settings, alert: dict) -> None:
    """追加一条告警到台账（原子写整个数组，规模小无碍；数组可读可审计）。"""
    with _alert_lock:  # 读-改-写整段持锁：并发 deliver 不丢写（FastAPI 线程池多请求并发下）
        alerts = read_alerts(settings)
        alerts.append(alert)
        _atomic_write_json(alerts_path(settings), alerts)
