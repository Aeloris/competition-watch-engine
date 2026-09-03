# -*- coding: utf-8 -*-
"""记忆层（README2 §3/§5.5）：核心数据对象 + 持久化 + mock 语料装载。

Phase 1 落地：三类对象 schema 定全、JSON/SQLite 双后端可存可取、mock 语料可按 ISO 周切成周期快照。
Phase 3 在此实现 Event 指纹 diff（新增/变更/消失）——项目灵魂的"跨期找变化"。
"""
from memory.corpus import build_period_cards, list_competitors
from memory.diff import (
    ChangeSet,
    DiffSummary,
    build_snapshot,
    diff_event_sets,
    event_content_digest,
    mark_removed,
)
from memory.fingerprint import event_fingerprint, normalize_title
from memory.schemas import Dimension, Event, EventKind, FactCard, Snapshot, ThreatLevel
from memory.store import JsonMemoryStore, MemoryStore, SqliteMemoryStore, get_memory_store

__all__ = [
    "Dimension",
    "Event",
    "EventKind",
    "ThreatLevel",
    "FactCard",
    "Snapshot",
    "normalize_title",
    "event_fingerprint",
    "event_content_digest",
    "build_snapshot",
    "mark_removed",
    "diff_event_sets",
    "ChangeSet",
    "DiffSummary",
    "MemoryStore",
    "JsonMemoryStore",
    "SqliteMemoryStore",
    "get_memory_store",
    "list_competitors",
    "build_period_cards",
]
