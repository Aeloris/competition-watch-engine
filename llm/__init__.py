# -*- coding: utf-8 -*-
"""LLM 门面：get_provider(settings) 按 config.yaml 返回具体 Provider。

provider=dashscope 时构造 DashScopeProvider（chat 会 raise 提示未接入）；
provider=mock（默认）→ MockProvider，离线确定性。
"""
from __future__ import annotations

from config.settings import Settings
from llm.base import LLMProvider
from llm.dashscope_provider import DashScopeProvider
from llm.mock_provider import MockProvider


def get_provider(settings: Settings) -> LLMProvider:
    if settings.llm.provider == "dashscope":
        return DashScopeProvider()
    return MockProvider(settings)


__all__ = ["LLMProvider", "MockProvider", "DashScopeProvider", "get_provider"]
