"""服务层（Phase 7）：把 run_cycle 包成"可调度、可查询、会告警、扛得住单点失败"的长跑服务。

- TaskStore（文件即状态）+ ServiceRunner（逐竞品 run 级失败隔离 + trace 计数链收口）；
- Notifier 告警抽象（默认 Mock 落台账离线可查；Webhook 占位壳，README 写可对接企微/飞书/邮件）；
- APScheduler 适配（每周一 09:00 巡检 cron，手动触发 run_now 演示）。

对外入口：build_service_context / ServiceContext / ServiceRunner / run_now / schedule_weekly_report /
last_completed_iso_week / MockNotifier / WebhookNotifier。HTTP 层在 app/routers（薄，全逻辑在 service）。
文档：docs/service.md。
"""
from service.context import ServiceContext, build_service_context
from service.notifier import MockNotifier, Notifier, WebhookNotifier, build_notifier
from service.runner import ServiceRunner, available_competitors, last_completed_iso_week, run_now
from service.scheduler import build_scheduler, schedule_weekly_report
from service.schemas import AlertEvent, RunMeta, RunStatus, TaskMeta, TaskStatus

__all__ = [
    "ServiceContext",
    "build_service_context",
    "ServiceRunner",
    "run_now",
    "Notifier",
    "MockNotifier",
    "WebhookNotifier",
    "build_notifier",
    "available_competitors",
    "last_completed_iso_week",
    "schedule_weekly_report",
    "build_scheduler",
    "TaskMeta",
    "RunMeta",
    "TaskStatus",
    "RunStatus",
    "AlertEvent",
]
