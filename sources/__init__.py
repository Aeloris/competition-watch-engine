# -*- coding: utf-8 -*-
"""信源层门面：adapter 抽象 + MockSource（默认离线）。

后续 build_source(settings, source_type) 在此按 config.sources.enabled 分派真实 adapter（Phase 2）。
"""
from __future__ import annotations

from sources.base import RawItem, SourceAdapter
from sources.mock import MockSource

__all__ = ["RawItem", "SourceAdapter", "MockSource"]
