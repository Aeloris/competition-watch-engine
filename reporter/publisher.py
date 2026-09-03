# -*- coding: utf-8 -*-
"""Reporter 占位类（Phase 7 之后）。

沿革：Phase 7 服务化把"告警"职责实现在 `service/notifier.py`（high_threat / human_inbox / run_failed →
Mock 台账默认 / Webhook 壳占位），HTTP 层经 GET /alerts + POST /alerts/hook 暴露（见 docs/service.md）。
本类原计划的"周报对外推送"仍未落（现在周报经 GET /tasks/{id}/report 以查询态访问），
对外发布/推送 + Phase 8 面板的收件箱人工确认放行，留后续。
"""


class Reporter:
    """周报对外发布/推送器（Phase 8 实现：把已 PASS 周报推给真实渠道/面板）。"""

    pass
