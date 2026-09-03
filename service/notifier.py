# -*- coding: utf-8 -*-
"""告警抽象（Phase 7，README2 §5.9/§5.10）：接口 + 默认 Mock + Webhook 占位。

- Notifier：deliver(event_type, *, competitor_id/period/run_id/summary/payload) 的协议；
- MockNotifier（默认）：把 AlertEvent 追加进 data/service/alerts.json 台账（离线可查、GET /alerts 读）；
- WebhookNotifier（占位壳）：**url 为空构造即 ValueError**（fail-fast，同 DashScope 壳哲学）；
  deliver 不真发 HTTP——记一条 delivered_by="webhook_placeholder" 并提示 README（企微/飞书/邮件待接）。

诚实口径：真 HTTP 推送未实现、不假装已发；本期"告警可演示"= 台账可查 + 壳的 fail-fast 有测试锁。
"""
from __future__ import annotations

import uuid

from config.settings import Settings
from service import store
from service.schemas import AlertEvent

store_utc_now_iso = store.utc_now_iso  # 单一时钟来源（service/store.utc_now_iso）


class Notifier:
    """告警投递协议。子类实现 deliver（记录到台账 / 将来真 webhook）。"""

    def deliver(self, event: AlertEvent) -> dict:
        raise NotImplementedError

    def list_log(self) -> list[dict]:
        raise NotImplementedError


class MockNotifier(Notifier):
    """默认告警器：写入 data/service/alerts.json（台账，GET /alerts 可读）。"""

    def __init__(self, settings: Settings, *, verbose: bool = False) -> None:
        self._settings = settings
        self._verbose = verbose

    def deliver(self, event: AlertEvent) -> dict:
        store.append_alert(self._settings, event.as_dict())
        if self._verbose:
            print(f"[alert] {event.event_type} {event.competitor_id} {event.period}: {event.summary}")
        return {"alert_id": event.alert_id, "delivered_by": event.delivered_by, "ts": event.ts}

    def list_log(self) -> list[dict]:
        return store.read_alerts(self._settings)


class WebhookNotifier(Notifier):
    """webhook 告警占位壳。url 为空构造即失败（fail fast）；deliver 只记台账、不真发 HTTP。

    将来接真实渠道：实现 deliver 内 `httpx.post(self.url, json=event)` + 超时/重试，
    README 写"可对接企微/飞书/邮件"即此壳的扩展点。
    """

    def __init__(self, url: str, *, verbose: bool = False) -> None:
        if not url:
            raise ValueError("WebhookNotifier 需要非空 url；未配置告警渠道请用 MockNotifier（默认离线）")
        self.url = url
        self._verbose = verbose
        self._settings = None  # 未接真渠道，不落台账（构造即需 url 已是 fail-fast）

    def deliver(self, event: AlertEvent) -> dict:
        raise NotImplementedError(
            "WebhookNotifier 是真 HTTP 推送占位：尚未实现 POST 发送（企微/飞书/邮件待接）。"
            "默认请用 MockNotifier 离线记台账。"
        )

    def list_log(self) -> list[dict]:
        raise NotImplementedError


def build_notifier(settings: Settings) -> Notifier:
    """按配置分派：alerts.webhook_url 空 → Mock（默认离线可查）；非空 → Webhook 壳（需 url 已校验）。"""
    url = settings.alerts.webhook_url
    if url:
        return WebhookNotifier(url)
    return MockNotifier(settings)


def make_alert(
    event_type: str,
    *,
    summary: str,
    competitor_id: str | None = None,
    period: str | None = None,
    run_id: str | None = None,
    payload: dict | None = None,
    delivered_by: str = "mock",
) -> AlertEvent:
    """构造一条告警事件（ts 用 UTC now；测试只断言类型/竞品/期，不断言精确时间）。"""
    return AlertEvent(
        alert_id=uuid.uuid4().hex[:8],
        event_type=event_type,
        competitor_id=competitor_id,
        period=period,
        run_id=run_id,
        summary=summary,
        payload=payload or {},
        ts=store_utc_now_iso(),
        delivered_by=delivered_by,
    )
