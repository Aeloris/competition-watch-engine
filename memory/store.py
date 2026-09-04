# -*- coding: utf-8 -*-
"""记忆持久化：MemoryStore 接口 + JSON/SQLite 双后端 + 工厂（README2 §1.5 / §5.5）。

- 操作粒度按 (competitor_id, period)：save_fact_cards 幂等覆盖、list 同序回读、latest_snapshot 供 diff(Phase3)。
- 为什么 JSON 与 SQLite 都实现：上层零感知换存储 = "存储可替换"的工程证据；JSON 人可读、可 diff、默认；
  SQLite 用标准库 sqlite3（零新依赖）、可事务可查询。config.pipeline.snapshot_store 一键切（json|sqlite），
  未知值启动即 raise（fail fast）。存 data/memory/ 下（gitignored 的运行产物）。
"""
from __future__ import annotations

import itertools
import json
import os
import sqlite3
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path

from config.settings import Settings
from memory.schemas import FactCard, Snapshot

# 唯一 tmp 名（pid + 自增序号）→ os.replace 原子落盘：崩溃/被杀也只留 .tmp 半成品，
# 目标文件要么旧要么新，绝无中间态。固定 ".tmp" 会让上次异常残留被本调用吞掉（见 service/store 同款）。
_tmp_seq = itertools.count()


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{next(_tmp_seq)}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_json_tolerant(path: Path):
    """读整份 JSON；缺失返回 None，损坏（半截/非法 JSON）返回 None —— 调用方按"文件不存在"降级，
    不把单个坏文件炸成全竞品后续 run（写已是原子，坏文件只可能来自外部手改）。"""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


class MemoryStore(ABC):
    """记忆存储接口：FactCard 存档 + 周期快照。写同一 (竞品, 期) 必须幂等覆盖（重跑不翻倍）。"""

    name: str = "base"

    @abstractmethod
    def save_fact_cards(self, competitor_id: str, period: str, cards: list[FactCard]) -> None:
        """整期覆盖写入该竞品该期的 FactCard 存档。"""
        raise NotImplementedError

    @abstractmethod
    def list_fact_cards(self, competitor_id: str, period: str) -> list[FactCard]:
        """按写入顺序回读（空期返回 []）。"""
        raise NotImplementedError

    @abstractmethod
    def save_snapshot(self, snapshot: Snapshot) -> None:
        """写入/覆盖一条周期快照。"""
        raise NotImplementedError

    @abstractmethod
    def latest_snapshot(self, competitor_id: str) -> Snapshot | None:
        """该竞品最近一期快照（无则 None）。period 字典序即时间序（YYYY-Www）。"""
        raise NotImplementedError


class JsonMemoryStore(MemoryStore):
    """JSON 后端：data/memory/fact_cards/{竞品}/{period}.json、snapshots/... 人工可读。"""

    name = "json"

    def __init__(self, root_dir: str | Path) -> None:
        self._root = Path(root_dir)
        self._fc_dir = self._root / "fact_cards"
        self._snap_dir = self._root / "snapshots"

    # ---- 路径 ----
    def _fc_path(self, competitor_id: str, period: str) -> Path:
        return self._fc_dir / competitor_id / f"{period}.json"

    def _snap_path(self, competitor_id: str, period: str) -> Path:
        return self._snap_dir / competitor_id / f"{period}.json"

    # ---- FactCards ----
    def save_fact_cards(self, competitor_id: str, period: str, cards: list[FactCard]) -> None:
        ordered = sorted(cards, key=lambda c: (c.published_at.isoformat(), c.source_url))
        p = self._fc_path(competitor_id, period)
        _atomic_write_text(
            p,
            json.dumps(
                [c.model_dump(mode="json") for c in ordered],
                ensure_ascii=False,
                indent=2,
            ),
        )

    def list_fact_cards(self, competitor_id: str, period: str) -> list[FactCard]:
        p = self._fc_path(competitor_id, period)
        data = _read_json_tolerant(p)
        if data is None:
            return []  # 缺失/损坏都按"该期无存档"读，不抛（防坏文件炸调用方）
        try:
            return [FactCard.model_validate(item) for item in data]
        except Exception:
            return []

    # ---- Snapshots ----
    def save_snapshot(self, snapshot: Snapshot) -> None:
        p = self._snap_path(snapshot.competitor_id, snapshot.period)
        _atomic_write_text(
            p,
            json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, indent=2),
        )

    def latest_snapshot(self, competitor_id: str) -> Snapshot | None:
        d = self._snap_dir / competitor_id
        if not d.exists():
            return None
        files = [f for f in d.glob("*.json") if f.is_file()]
        if not files:
            return None
        newest = max(files, key=lambda f: f.stem)  # period 字典序 = 时间序
        data = _read_json_tolerant(newest)
        if data is None:
            return None  # 最新快照损坏 → 按"无快照"冷启动（下期重写自愈），不把坏文件炸给 diff
        try:
            return Snapshot.model_validate(data)
        except Exception:
            return None


class SqliteMemoryStore(MemoryStore):
    """SQLite 后端：data/memory/memory.db。单表存整期 JSON blob（JSON1 可查），写 = INSERT OR REPLACE。"""

    name = "sqlite"

    def __init__(self, root_dir: str | Path) -> None:
        root = Path(root_dir)
        root.mkdir(parents=True, exist_ok=True)
        self._db_path = root / "memory.db"
        self._init_schema()

    @contextmanager
    def _session(self):
        """连接 + 事务上下文：with conn 负责 commit/rollback，finally 确保 close（防 Windows 句柄泄漏）。"""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fact_cards (
                    competitor_id TEXT NOT NULL,
                    period        TEXT NOT NULL,
                    payload       TEXT NOT NULL,
                    PRIMARY KEY (competitor_id, period)
                );
                CREATE TABLE IF NOT EXISTS snapshots (
                    competitor_id TEXT NOT NULL,
                    period        TEXT NOT NULL,
                    payload       TEXT NOT NULL,
                    PRIMARY KEY (competitor_id, period)
                );
                """
            )

    def save_fact_cards(self, competitor_id: str, period: str, cards: list[FactCard]) -> None:
        ordered = sorted(cards, key=lambda c: (c.published_at.isoformat(), c.source_url))
        payload = json.dumps([c.model_dump(mode="json") for c in ordered], ensure_ascii=False)
        with self._session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fact_cards (competitor_id, period, payload) VALUES (?, ?, ?)",
                (competitor_id, period, payload),
            )

    def list_fact_cards(self, competitor_id: str, period: str) -> list[FactCard]:
        with self._session() as conn:
            row = conn.execute(
                "SELECT payload FROM fact_cards WHERE competitor_id = ? AND period = ?",
                (competitor_id, period),
            ).fetchone()
        if row is None:
            return []
        return [FactCard.model_validate(item) for item in json.loads(row["payload"])]

    def save_snapshot(self, snapshot: Snapshot) -> None:
        payload = json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
        with self._session() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots (competitor_id, period, payload) VALUES (?, ?, ?)",
                (snapshot.competitor_id, snapshot.period, payload),
            )

    def latest_snapshot(self, competitor_id: str) -> Snapshot | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT payload FROM snapshots WHERE competitor_id = ? ORDER BY period DESC LIMIT 1",
                (competitor_id,),
            ).fetchone()
        if row is None:
            return None
        return Snapshot.model_validate(json.loads(row["payload"]))


def get_memory_store(settings: Settings) -> MemoryStore:
    """按 config.pipeline.snapshot_store（json|sqlite）返回对应后端，根目录 data/memory。"""
    backend = settings.pipeline.snapshot_store
    root = settings.data_path / "memory"
    if backend == "json":
        return JsonMemoryStore(root)
    if backend == "sqlite":
        return SqliteMemoryStore(root)
    raise ValueError(f"config.pipeline.snapshot_store 未知: {backend!r}（支持 json|sqlite）")
