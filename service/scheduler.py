# -*- coding: utf-8 -*-
"""调度适配层（Phase 7，README2 §5.9）：把"每周一 09:00 巡检"接进 APScheduler。

设计：
- schedule_weekly_report(ctx)：把 config.schedule.cron（默认 "0 9 * * mon"）注册成 BackgroundScheduler
  的 weekly_report job，job 内容 = run_now(ctx)（period 缺省 = 最近已结束 ISO 周 → 正好报"上周"）。
- 手动触发（demo/测试用）：run_now(ctx, period=...) 直接执行，不真等周一 —— APScheduler 只做"到点叫你"，
  真正跑的是 service.runner（调度与执行解耦，可各自单测）。

诚实口径：定时只在进程活着时生效（单进程长跑服务定位）；本模块不做任何"假睡等时间"，
调度注册与手动触发分别有测试锁（不真等 cron 到点）。
"""
from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from service.runner import ServiceRunner, run_now


def schedule_weekly_report(ctx, *, scheduler: BackgroundScheduler | None = None) -> BackgroundScheduler:
    """把每周巡检 job 注册进（新造或注入的）BackgroundScheduler。返回该 scheduler（未 start）。"""
    sched = scheduler or BackgroundScheduler()
    cron = ctx.settings.schedule.cron
    sched.add_job(
        run_now,
        CronTrigger.from_crontab(cron, timezone=sched.timezone),
        args=[ctx.runner],
        id="weekly_report",
        name=f"weekly_report({cron})",
        replace_existing=True,
    )
    return sched


def build_scheduler(ctx) -> BackgroundScheduler:
    """创建并注册巡检 job 的 scheduler（main 在 schedule.enabled 时 start；测试只查注册不 start）。"""
    return schedule_weekly_report(ctx)
