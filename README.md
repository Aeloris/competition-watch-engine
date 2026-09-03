# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 5（Analyst 威胁分级 + Writer 合规周报）

沿革：Phase 0 = 最小服务骨架 + Mock 底座；Phase 1 = 数据模型/持久化/指纹；Phase 2 = 采集层（并发、隔离、可数）；
Phase 3 = **去重并事件 + 跨期 diff 找"本周变了什么"**；Phase 4 = **把 P1–P3 用 LangGraph 串成一条可跑的周期流水线**；
Phase 5 = **把 analyst / writer 做实（图一条边不改）**——每条 diff 出的变更事件都有可解释的威胁分级，周报结构由模型自校验：

- **Analyst 威胁分级** `analyst/{rubric,classifier}.py`：规则阶梯**先命中先定级**（正式发布/降价/处罚 → `high`；
  功能迭代/实测/融资 → `medium`；预告/盘点/招聘 → `low`），每次判定带**理由链**（哪条规则 / 命中词 / 几源确认）。
  确定性 → 同语料重跑 severity 逐条稳定；**只读维度不改 fp**（改维度 = 把一件事 diff 成"消失+新增"假情报，见 docs）。
- **Writer 合规周报** `writer/{report,service}.py`：把已分级条目组装成 `WeeklyReport`，pydantic 在校验期强制
  headline==条数、kind 分布==diff sections、威胁雷达**重算**、sources=出处并集；**只组装、绝不补充常识**（无编造入口）。
- **编排门卫升级**：reviewer 结构性门卫多查两条——每条**已分级（severity）**、**带理由（reasons）**；
  有缺口 `REWRITE` 沿条件边打回（额度 2），用尽 → `human` 转人工收件箱、不自动放行。
- **真值 trace（实测非编造）**：`demo_two_weeks()` 两竞品 × (W34 冷启动 → W35 对上期) →
  crm **5→3→3**（v12.0 官网+rss+媒体三源并入一、evidence=3）、bi 5→4→4；且 W35 威胁雷达实测
  **crm {high1, med1, low1}、bi {high1, med1, low2}——各项之和 == diff K**（每条变更都进雷达），gate 全 PASS；重跑同周期 diff=0。
- **诚实边界**：rubric 是确定性信号词分级器（"异词同威胁"漏判 → LLM 补语义未来层）；Writer 不做自然语言正文；
  Reviewer 语义 grounding / 矛盾 / 合规 = Phase 6；语义近并（embedding/Qdrant）后置占位。细节见
  [`docs/analyst_writer.md`](docs/analyst_writer.md) 与 [`docs/orchestrator.md`](docs/orchestrator.md)。

```bash
uv sync
uv run pytest            # 全绿（110）
uv run python -c "from orchestration import demo_two_weeks; demo_two_weeks()"   # 两竞品×两期回放，打印 trace + threat_radar + gate
uv run uvicorn app.main:app --port 8000   # http://127.0.0.1:8000/health
```

## 目录

```
app/    FastAPI 入口        config/  强类型配置(fail-fast)
llm/    Provider 抽象       sources/ 信源适配器 + MockSource
memory/ 数据对象+持久化+指纹+跨期diff   collectors/ 采集层：并发/归一化/失败隔离
dedupe/ 维度规则分类 + 版本锚去重合并     orchestration/ LangGraph 状态机（Phase 4–5）
analyst/ ThreatRubric 规则阶梯分级（Phase 5）    writer/ WeeklyReport 合规周报（Phase 5）
fixtures/ 离线回放底座
docs/   architecture.md 骨架现状 · schema.md 数据模型 · collectors.md 采集层 · memory.md 去重+diff
        · orchestrator.md 编排状态机 · analyst_writer.md Analyst 分级 + Writer 周报
tests/  冒烟 / mock / 数据层 / 采集层 / 去重 / diff / 编排状态机 / analyst 分级 / writer 周报
```

`orchestration/` Phase 4–5 已落地：state（共享可序列化 State）/ planner（周期换算）/ nodes（七节点，
analyst·writer P5 做实）/ graph（StateGraph + MemorySaver + 门卫条件边）/ pipeline（run_cycle / demo_two_weeks）。
`analyst/ writer/` 已是真实包；`reviewer/` 结构性门卫仍落在 `orchestration/nodes.py` 内（语义审稿 Phase 6 拆实），
`reporter/ eval/` 分别留 Phase 7 / Phase 8。
