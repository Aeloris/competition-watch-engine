# -*- coding: utf-8 -*-
"""ServiceContext：把 settings / TaskStore / Notifier / ServiceRunner 捏成一个 FastAPI 依赖。

- build_service_context()：main 启动时建一次挂 app.state（routers 经依赖注入读取）；
- 测试换临时目录：build_service_context(data_dir=tmp) 注入同一数据根 → TaskStore / 记忆库 /
  pipeline json / 告警台账全部隔离在 tmp（离线确定性；也可 dependency_overrides 换掉整个 ctx）。

注意：settings 一律 model_copy 后使用（绝不改 lru_cache 的全局 get_settings() 实例，
否则测试的 data_dir 覆盖会毒化其它导入方）。
"""
from __future__ import annotations

from dataclasses import dataclass

from config.settings import Settings, get_settings
from service.notifier import Notifier, build_notifier
from service.runner import ServiceRunner
from service.store import TaskStore


@dataclass
class ServiceContext:
    settings: Settings
    task_store: TaskStore
    notifier: Notifier
    runner: ServiceRunner

    def list_alerts(self) -> list[dict]:
        return self.notifier.list_log()


def build_service_context(
    settings: Settings | None = None,
    *,
    data_dir: str | None = None,
    notifier: Notifier | None = None,
) -> ServiceContext:
    """构造服务上下文。data_dir 给定时把它当运行时数据根（测试隔离用）。"""
    settings = (settings or get_settings()).model_copy(deep=True)
    if data_dir:
        settings.app.data_dir = data_dir
    store = TaskStore(settings)
    notifier = notifier or build_notifier(settings)
    runner = ServiceRunner(settings=settings, task_store=store, notifier=notifier)
    return ServiceContext(settings=settings, task_store=store, notifier=notifier, runner=runner)
