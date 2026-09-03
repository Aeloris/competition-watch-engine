# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 本仓库 Phase 0 为**骨架 + Mock 底座**，落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 2（采集层 Researchers 并发采集）

沿革：Phase 0 = 最小服务骨架 + Mock 底座；Phase 1 = 记忆层（memory）——给"跨期找变化"（Phase 3 diff）打留档地基；
Phase 2 = 采集层（collectors）——把"RawItem → FactCard 证据"这一段做成并发、隔离、可数的流水线：

- FastAPI `/health`：默认 `provider=mock`，报告 Mock 竞品在位。
- LLM 抽象：`llm/` 面向接口（Mock 离线 / DashScope 壳，无 key 全链路可跑）。
- 信源适配器：`sources/` 统一 `RawItem`；MockSource 读 `fixtures/sources`（2 竞品 × website/news/rss × 两周，已预置多源重复事件供后续去重验证）。
- **数据对象** `memory/schemas.py`：`FactCard`（证据）→ `Event`（事实）→ `Snapshot`（记忆），字段对齐设计稿 ER。
- **持久化** `memory/store.py`：JSON（默认）/ SQLite（stdlib）双后端，`config.pipeline.snapshot_store` 一键切，同 period 幂等覆盖。
- **事件指纹** `memory/fingerprint.py`：`fp = sha1(竞品,维度,规范化标题)`，确定性纯函数（Phase 3 diff 的锚）。
- **语料切期** `memory/corpus.py`：fixtures 两周按 ISO 周切 `2026-W34` / `2026-W35` 两期 → 可回放。
- **并发采集** `collectors/`：每源一个 `CollectorWorker`（包一个 adapter），`collect_competitor` 用 asyncio+信号量并发采
  （`concurrency_limit` 控 QPS）；`RawItem → FactCard` 归一化唯一入口 `collectors/normalize.py`；`fetched_at` 可注入 → 回放确定性。
- **失败隔离**：单源挂了经 `WorkerOutcome(ok=False)` 汇入 `CollectSummary.failures`（如 `rss/crm_alpha: ConnectionError…`），
  其它源照常产出——报告诚实标注"本信源失败"，绝不假装整期情报完整，也不崩全流程。

```bash
uv sync
uv run pytest            # 全绿（44）
uv run uvicorn app.main:app --port 8000
# http://127.0.0.1:8000/health
```

## 目录

```
app/    FastAPI 入口        config/  强类型配置(fail-fast)
llm/    Provider 抽象       sources/ 信源适配器 + MockSource
memory/ 数据对象+持久化+指纹   collectors/ 采集层：并发/归一化/失败隔离
fixtures/ 离线回放底座
docs/   schema.md 数据模型 · collectors.md 采集层 · architecture.md 骨架现状
tests/  冒烟 / mock / 数据层 / 采集层（schema·fingerprint·store·corpus·collectors）
```

业务包 `orchestration/ analyst/ writer/ reviewer/ reporter/ eval/` 现为占位类，按 Phase 计划落地；memory 层与 collectors 层已落地（见上）。
