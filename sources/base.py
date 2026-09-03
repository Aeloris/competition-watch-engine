# -*- coding: utf-8 -*-
"""信源适配器抽象：不同来源（官网 HTTP / 新闻搜索 / RSS / Mock）→ 统一 RawItem。

adapter 模式屏蔽来源差异（README2 §5.3）：上层只消费 RawItem（带 url + 原文时间），
新增一个信源 = 新增一个 SourceAdapter，不加业务代码。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from pydantic import BaseModel, Field


class RawItem(BaseModel):
    """一条"原始抓取条目"（尚未归一化/去重）。必须可溯源到具体 URL。"""

    kind: str = Field(..., description="信源类型：website | news | rss（对齐 config.sources.enabled）")
    url: str = Field(..., description="可点回的原始链接（溯源锚点）")
    published_at: datetime = Field(..., description="原文发布时间（原文时间，非抓取时间）")
    title: str = Field(default="", description="标题")
    summary: str = Field(default="", description="正文摘要/首段")


class SourceAdapter(ABC):
    """一个信源的适配器：按 竞品×信源类型 拉取，since=None 全量 / 给定则增量。"""

    name: str = "base"

    @abstractmethod
    async def fetch(
        self, competitor_id: str, kind: str, *, since: datetime | None = None
    ) -> list[RawItem]:
        """返回该源可读到的条目（按 published_at 升序）。单源失败由调用方隔离，本接口不抛业务错误。"""
        raise NotImplementedError
