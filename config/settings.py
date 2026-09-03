# -*- coding: utf-8 -*-
"""配置加载：config/config.yaml（业务参数） + .env / 环境变量（密钥）。

用法：
    from config.settings import get_settings
    s = get_settings()          # 进程内只加载一次（lru_cache）
    s.llm.provider             # -> "mock"
    s.sources.concurrency_limit  # -> 4

设计要点（同项目一约定）：
- 所有未来阶段会用到的参数已在 config.yaml 占位，这里定义强类型模型，拼错字段启动即报错；
- 密钥（DASHSCOPE_API_KEY）只从环境变量 / .env 读取，绝不出现在 yaml；
- llm.provider=dashscope 但缺少 key 时，启动即抛清晰错误（fail fast）。
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator

# 仓库根目录 = config/ 的上一级；所有相对路径都从这里解析
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "config.yaml"

# 把仓库根 .env 里的键注入 os.environ（若存在）；不存在则静默跳过
load_dotenv(REPO_ROOT / ".env")


class AppConfig(BaseModel):
    name: str = "competition-watch-agent"
    log_level: str = "INFO"
    data_dir: str = "./data"
    fixtures_dir: str = "./fixtures"


class LLMConfig(BaseModel):
    provider: str = "mock"  # mock | dashscope
    model: str = "qwen-plus"
    timeout_sec: float = 60.0
    temperature: float = 0.2
    max_retries: int = 3
    api_key_env: str = "DASHSCOPE_API_KEY"

    @property
    def api_key(self) -> str | None:
        return os.getenv(self.api_key_env)

    @model_validator(mode="after")
    def _fail_fast_when_dashscope_without_key(self) -> "LLMConfig":
        if self.provider == "dashscope" and not self.api_key:
            raise ValueError(
                f"llm.provider=dashscope 但环境变量 {self.api_key_env} 未设置。\n"
                "解决办法：复制 .env.example 为 .env 并填入真实 key；"
                "或把 config.yaml 的 llm.provider 保持为 mock 离线运行。"
            )
        return self


class SourcesConfig(BaseModel):
    mock_dir: str = "./fixtures/sources"
    concurrency_limit: int = 4
    fetch_timeout_sec: float = 15.0
    max_retries: int = 2
    enabled: list[str] = Field(default_factory=lambda: ["website", "news", "rss"])


class ScheduleConfig(BaseModel):
    cron: str = "0 9 * * 1"
    alert_on_high_threat: bool = True


class PipelineConfig(BaseModel):
    review_max_rewrites: int = 2
    diff_period: str = "week"
    snapshot_store: str = "json"  # json | sqlite


class VectorDBConfig(BaseModel):
    provider: str = "qdrant_local"
    path: str = "./data/qdrant"
    collection: str = "fact_cards"


class Settings(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    pipeline: PipelineConfig = Field(default_factory=PipelineConfig)
    vector_db: VectorDBConfig = Field(default_factory=VectorDBConfig)

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> "Settings":
        p = Path(path) if path else DEFAULT_CONFIG_PATH
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        return cls(**data)

    @property
    def repo_root(self) -> Path:
        return REPO_ROOT

    @property
    def data_path(self) -> Path:
        """运行产物目录（自动创建）。"""
        p = self.repo_root / self.app.data_dir
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def fixtures_path(self) -> Path:
        """fixtures 目录（不存在则创建）。"""
        p = self.repo_root / self.app.fixtures_dir
        p.mkdir(parents=True, exist_ok=True)
        return p


@lru_cache(maxsize=1)
def get_settings(path: str | Path | None = None) -> Settings:
    """进程内共享同一份配置；测试里想用别的 yaml 可传 path。"""
    return Settings.from_yaml(path)
