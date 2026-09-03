# -*- coding: utf-8 -*-
"""HTTP 路由（Phase 7）：薄层，业务逻辑全在 service/。

- tasks.py   POST/GET /tasks、GET /tasks/{id}/report（调度/查询/周报）
- inbox.py   GET /inbox（人工收件箱：P6 human_inbox 的服务化出口）
- alerts.py  GET /alerts、POST /alerts/hook（告警台账 + 手动投递演练）
"""
