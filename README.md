# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 本仓库 Phase 0 为**骨架 + Mock 底座**，落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 0（骨架 + Mock 底座，本地）

能跑的最小服务骨架 + 离线确定性底座：

- FastAPI `/health`：默认 `provider=mock`，报告 Mock 竞品在位。
- LLM 抽象：`llm/` 面向接口（Mock 离线 / DashScope 壳，无 key 全链路可跑）。
- 信源适配器：`sources/` 统一 `RawItem`；MockSource 读 `fixtures/sources`（2 竞品 × website/news/rss × 两周，已预置多源重复事件供后续去重验证）。

```bash
uv sync
uv run pytest            # 全绿
uv run uvicorn app.main:app --port 8000
# http://127.0.0.1:8000/health
```

## 目录

```
app/    FastAPI 入口        config/  强类型配置(fail-fast)
llm/    Provider 抽象       sources/ 信源适配器 + MockSource
fixtures/ 离线回放底座        docs/   架构/骨架现状
tests/  冒烟与 Mock 测试
```

业务包 `orchestration/ collectors/ memory/ analyst/ writer/ reviewer/ reporter/ eval/` 现为占位类，按 Phase 计划落地。
