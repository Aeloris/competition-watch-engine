# -*- coding: utf-8 -*-
"""MockProvider：无 key 默认离线可跑、回复确定、记录调用数。"""
import asyncio

from config.settings import get_settings
from llm import get_provider
from llm.mock_provider import MockProvider


def test_default_provider_is_mock():
    provider = get_provider(get_settings())
    assert isinstance(provider, MockProvider)
    assert provider.name == "mock"


def test_chat_returns_fixture_reply_and_counts_calls():
    provider = get_provider(get_settings())

    async def _run():
        first = await provider.chat(
            [{"role": "system", "content": "你是采集规划器"}, {"role": "user", "content": "生成任务清单"}]
        )
        second = await provider.chat([{"role": "user", "content": "再来一次"}])
        return first, second

    first, second = asyncio.run(_run())
    # 确定性：同一固定回复源，两次返回一致
    assert first == second
    assert "竞品 Alpha" in first  # 来自 fixtures/llm/reply.json
    assert len(provider.calls) == 2  # eval 将据此数 LLM 调用量
