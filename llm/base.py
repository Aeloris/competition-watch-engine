# -*- coding: utf-8 -*-
"""LLM Provider 抽象（同项目一约定，面向接口不面向实现）。

业务代码只写 chat(messages, ...)，换厂商只加一个实现类；Mock 使测试离线且确定。
Phase 4 起会加 response_model（pydantic 结构化输出）约束，届时仍走本抽象。
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    """统一对话接口。子类必须实现 chat；结构化输出在 Phase 4 引入时再加默认实现。"""

    name: str = "base"

    @abstractmethod
    async def chat(self, messages: list[dict], *, temperature: float | None = None) -> str:
        """messages: [{"role": "system"|"user"|"assistant", "content": str}, ...] → 文本回复。"""
        raise NotImplementedError
