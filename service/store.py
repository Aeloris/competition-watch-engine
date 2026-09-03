# -*- coding: utf-8 -*-
"""任务/告警持久化（Phase 7）：文件即状态，零外部依赖。

- 任务：data/service/tasks/{task_id}.json（每 Task 一个文件，TaskMeta 全量可读）；
- 告警台账：data/service/alerts.json（AlertEvent 数组，MockNotifier 写、GET /alerts 读）。

同一 (竞品, 期) 重跑 = 新 task_id + 新 run_id → 两个 Task 文件并列 = 审计留痕（不幂等覆盖，
因为服务层关心"每次调度都发生过什么"；幂等覆盖是 Phase 1 memory 写快照的职责）。
写文件一律原子化（先写 .tmp 再 os.replace），单进程内防半截 JSON。
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from service.schemas import RunMeta, TaskMeta, TaskStatus


def utc_now_iso() -> str:
    """UTC 时间戳（ISO）。服务层记录"何时创建/完成/告警"，测试只断言存在与顺序，不断言精确值。"""
    return datetime.now(timezone.utc).isoformat()


def _atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
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
        return TaskMeta.model_validate(_read_json(p, {}))

    def _save(self, meta: TaskMeta) -> None:
        _atomic_write_json(self._task_path(meta.task_id), meta.as_dict())

    def add_run(self, task_id: str, run: RunMeta) -> TaskMeta:
        """把一次 run 的结果记进 Task（顺序执行：追加即终态）。返回更新后的 Task。"""
        meta = self.get_task(task_id)
        if meta is None:
            raise KeyError(f"task {task_id} 不存在")
        # 同名 run_id 重复记则覆盖（幂等防重放），否则追加
        meta.runs = [r for r in meta.runs if r.run_id != run.run_id] + [run]
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
        """跑完全部 run 后定 Task 终态：completed / partial / failed。"""
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


# ------------------------------------------------------------ 告警台账
def alerts_path(settings) -> Path:
    return settings.data_path / "service" / "alerts.json"


def read_alerts(settings) -> list[dict]:
    return _read_json(alerts_path(settings), [])


def append_alert(settings, alert: dict) -> None:
    """追加一条告警到台账（原子写整个数组，规模小无碍；数组可读可审计）。"""
    alerts = read_alerts(settings)
    alerts.append(alert)
    _atomic_write_json(alerts_path(settings), alerts)
