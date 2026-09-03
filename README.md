# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 4（LangGraph 端到端编排）

沿革：Phase 0 = 最小服务骨架 + Mock 底座；Phase 1 = 数据模型/持久化/指纹；Phase 2 = 采集层（并发、隔离、可数）；
Phase 3 = **去重并事件 + 跨期 diff 找"本周变了什么"**；Phase 4 = **把 P1–P3 用 LangGraph 串成一条可跑的周期流水线**，
一条命令出「周报初稿 + 门卫判定」：

- **编排状态机** `orchestration/{state,planner,nodes,graph,pipeline}.py`：
  `Planner → Researchers(并行) → Dedupe/Diff → Analyst → Writer → Reviewer`。
  planner / researchers / dedupe_diff 是**真节点**（复用 collectors 并发 + 单源失败隔离、dedupe 去重合并、
  memory.diff 跨期对比 + 快照写档）；analyst / writer / reviewer 本期是**"结构真、逻辑薄"节点**（severity 占位、模板初稿、
  结构性门卫）——威胁分级 rubric / 语义审稿分别留 P5 / P6。
- **共享可序列化 State + checkpoint**：节点契约 `(state) → 部分更新`，`PeriodRunState` 全 JSON-safe →
  `persist=True` 整体落 `data/pipeline/{run_id}.json`（文件即状态），MemorySaver 按 thread_id(=run_id) 可取回某轮状态。
- **门卫 + 条件边（先审后发）**：reviewer 查每条**必有出处**(evidence_urls) / 标题 / 观察维度；有缺口 → `REWRITE`
  沿条件边回 Writer 重写（额度 `review_max_rewrites=2`），额度用尽 → `human` 转人工收件箱、**不自动放行**；无缺口 → `PASS`。
  测试里注入坏 writer（无出处条目）锁死"打回 2 次 → human"这条回路。
- **真值 trace（实测非编造）**：`demo_two_weeks()` 两竞品 × (W34 冷启动 → W35 对上期) →
  crm 2→2→2 / **5→3→3**（v12.0 官网+rss+媒体三源并入一、evidence=3），bi 2→2→2 / 5→4→4，gate 全 PASS；重跑同周期 diff=0。
- **诚实边界**：Analyst 威胁分级 + Writer 威胁雷达 = Phase 5；Reviewer 语义 grounding / 矛盾 / 合规 = Phase 6；
  语义近并（embedding/Qdrant）后置占位；本期 Analyst/Writer/Reviewer 只做结构不做假深度。细节见 [`docs/orchestrator.md`](docs/orchestrator.md)。

```bash
uv sync
uv run pytest            # 全绿（81）
uv run python -c "from orchestration import demo_two_weeks; demo_two_weeks()"   # 两竞品×两期回放，打印 trace N→M→K + gate
uv run uvicorn app.main:app --port 8000   # http://127.0.0.1:8000/health
```

## 目录

```
app/    FastAPI 入口        config/  强类型配置(fail-fast)
llm/    Provider 抽象       sources/ 信源适配器 + MockSource
memory/ 数据对象+持久化+指纹+跨期diff   collectors/ 采集层：并发/归一化/失败隔离
dedupe/ 维度规则分类 + 版本锚去重合并     orchestration/ LangGraph 状态机（Phase 4）
fixtures/ 离线回放底座
docs/   architecture.md 骨架现状 · schema.md 数据模型 · collectors.md 采集层 · memory.md 去重+diff · orchestrator.md 编排
tests/  冒烟 / mock / 数据层 / 采集层 / 去重 / diff / 编排状态机
```

`orchestration/` Phase 4 已落地：state（共享可序列化 State）/ planner（周期换算）/ nodes（七节点，analyst·writer·reviewer 薄）/
graph（StateGraph + MemorySaver + 门卫条件边）/ pipeline（run_cycle / demo_two_weeks）。
`analyst/ writer/ reviewer/ reporter/ eval/` 独立包仍占位——前三个本期以薄节点落在 `orchestration/nodes.py` 内，按 Phase 5/6/8 计划视需要拆出。
