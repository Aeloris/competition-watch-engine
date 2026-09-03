# -*- coding: utf-8 -*-
"""DashScopeProvider（骨架）。

真实接入（OpenAI 兼容 /v1 + DASHSCOPE_API_KEY）在后续 Phase 联调真实模型时补；
当前 chat 明确 raise，避免"看起来能跑其实没接"的半成品状态。
"""
from __future__ import annotations

from llm.base import LLMProvider


class DashScopeProvider(LLMProvider):
    name = "dashscope"

    async def chat(self, messages: list[dict], *, temperature: float | None = None) -> str:
        raise NotImplementedError(
            "DashScopeProvider 为骨架：真实 Qwen 接入留待后续 Phase 联调（配 DASHSCOPE_API_KEY）。"
            "离线请保持 llm.provider=mock。"
        )
