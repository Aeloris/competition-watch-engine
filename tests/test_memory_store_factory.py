# -*- coding: utf-8 -*-
"""get_memory_store 工厂：按 config.pipeline.snapshot_store 分派后端；未知值启动即报错（fail fast）。"""
import pytest

from config.settings import AppConfig, PipelineConfig, Settings
from memory import JsonMemoryStore, SqliteMemoryStore, get_memory_store


def _settings(tmp_path, backend: str) -> Settings:
    return Settings(
        app=AppConfig(data_dir=str(tmp_path)),  # data_dir 指向 tmp → 不写仓库真 data/
        pipeline=PipelineConfig(snapshot_store=backend),
    )


def test_factory_dispatches_json(tmp_path):
    assert isinstance(get_memory_store(_settings(tmp_path, "json")), JsonMemoryStore)


def test_factory_dispatches_sqlite(tmp_path):
    assert isinstance(get_memory_store(_settings(tmp_path, "sqlite")), SqliteMemoryStore)


def test_factory_rejects_unknown_backend(tmp_path):
    with pytest.raises(ValueError, match="snapshot_store"):
        get_memory_store(_settings(tmp_path, "redis"))
