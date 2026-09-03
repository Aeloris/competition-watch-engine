# -*- coding: utf-8 -*-
"""MockProvider：读 fixtures 固定 JSON 返回确定性应答（无 key 全链路离线可跑）。

- 同一输入永远返回同一回复 → 测试可回归；
- 记录每次调用（self.calls）供后续 eval 数 LLM 调用量；
- 不做任何真实联网。
"""
from __future__ import annotations

import json
from pathlib import Path

from config.settings import Settings
from llm.base import LLMProvider


class MockProvider(LLMProvider):
    name = "mock"

    def __init__(self, settings: Settings) -> None:
        # 固定回复源：fixtures/llm/reply.json（离线确定性底座）
        self._reply = json.loads(
            (settings.repo_root / "fixtures" / "llm" / "reply.json").read_text(encoding="utf-8")
        )["reply"]
        self.calls: list[dict] = []  # 记录调用（role 序列），供 eval 计数

    async def chat(self, messages: list[dict], *, temperature: float | None = None) -> str:
        self.calls.append({"messages": messages})
        return self._reply
