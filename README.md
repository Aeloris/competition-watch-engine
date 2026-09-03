# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 6（Reviewer 审稿门卫——注入假事件能被拦，先审后发做实）

沿革：Phase 0 = 最小服务骨架 + Mock 底座；Phase 1 = 数据模型/持久化/指纹；Phase 2 = 采集层（并发、隔离、可数）；
Phase 3 = **去重并事件 + 跨期 diff 找"本周变了什么"**；Phase 4 = **把 P1–P3 用 LangGraph 串成一条可跑的周期流水线**；
Phase 5 = **把 analyst / writer 做实**（每条 diff 出的变更事件都有可解释的威胁分级，周报结构由模型自校验）；
Phase 6 = **把 reviewer 从"结构门卫"做实为"审稿门卫"**（图仍一条边不改）——评审逻辑拆进 `reviewer/` 包：

- **Analyst 威胁分级** `analyst/{rubric,classifier}.py`：规则阶梯**先命中先定级**（正式发布/降价/处罚 → `high`；
  功能迭代/实测/融资 → `medium`；预告/盘点/招聘 → `low`），每次判定带**理由链**（哪条规则 / 命中词 / 几源确认）。
  确定性 → 同语料重跑 severity 逐条稳定；**只读维度不改 fp**（改维度 = 把一件事 diff 成"消失+新增"假情报，见 docs）。
- **Writer 合规周报** `writer/{report,service}.py`：把已分级条目组装成 `WeeklyReport`，pydantic 在校验期强制
  headline==条数、kind 分布==diff sections、威胁雷达**重算**、sources=出处并集；**只组装、绝不补充常识**（无编造入口）。
- **Reviewer 审稿门卫** `reviewer/{gate,problems}.py`：用**本 run 证据**（不是 Writer 自述）做三条判定——
  **grounding 闭环自证**：`evidence_url` 必在本期采集卡、`fp` 必在本期 diff 变更集、理由"命中信号词"必在正文
  （编造 URL / 凭空 fp / 张冠李戴各拦截）；**显式反义极性矛盾**（同轴反词 + 共享主体锚 → 转人工不自动二选一）；
  **与上期重复防御**。产出**带 code 的问题清单**；路由铁律：结构缺口 `REWRITE` 限次（额度 2），
  **信任类问题直转 `human` 人工收件箱、不消耗额度**（让 Writer 改写没出处的结论 = 逼它编造）。
- **先审后发在编排层是结构**：`human` → END = 不发布；每次判定 append `gate_trace`（打回有 trace），
  human 落 `human_inbox`（人工收件箱，Phase 7 面板直接读）。
- **拦截实测（注入式、非编造）**：注入 6 类假事件（编造 URL / 凭空 fp / 理由张冠李戴 / 同版本反义矛盾 /
  旧闻重发 / 图级注入假 URL）**全被拦（6/6）**，真实 fixtures W35（crm 3 + bi 4 = 7 条目）**0 误拦**；
  `demo_two_weeks()` 两竞品 gate 全 PASS、收件箱空。
- **真值 trace（实测非编造）**：crm W35 **5→3→3**（v12.0 官网+rss+媒体三源并入一、evidence=3）、bi 5→4→4；
  威胁雷达 **crm {high1, med1, low1}、bi {high1, med1, low2}——各项之和 == diff K**；重跑同周期 diff=0。
- **诚实边界**：rubric 是确定性信号词分级器；Writer 不做自然语言正文；Reviewer grounding 是"来源可回溯 +
  结论可复核"的自证，**不做"读原文语义"**（LLM 语义审稿 provider-gated、未接）、矛盾只抓显式极性对
  （语义级互斥转人工兜底）；README2 里"编造拦截率 92%"是**设计目标占位、从未实测，不上简历**；
  语义近并（embedding/Qdrant）后置占位。细节见
  [`docs/reviewer.md`](docs/reviewer.md)、[`docs/analyst_writer.md`](docs/analyst_writer.md) 与
  [`docs/orchestrator.md`](docs/orchestrator.md)。

```bash
uv sync
uv run pytest            # 全绿（123）
uv run python -c "from orchestration import demo_two_weeks; demo_two_weeks()"   # 两竞品×两期回放：trace + threat_radar + gate + gate_trace/human_inbox
uv run uvicorn app.main:app --port 8000   # http://127.0.0.1:8000/health
```

## 目录

```
app/    FastAPI 入口        config/  强类型配置(fail-fast)
llm/    Provider 抽象       sources/ 信源适配器 + MockSource
memory/ 数据对象+持久化+指纹+跨期diff   collectors/ 采集层：并发/归一化/失败隔离
dedupe/ 维度规则分类 + 版本锚去重合并     orchestration/ LangGraph 状态机（Phase 4–6）
analyst/ ThreatRubric 规则阶梯分级（Phase 5）    writer/ WeeklyReport 合规周报（Phase 5）
reviewer/ 审稿门卫：grounding/矛盾/合规 typed problems（Phase 6）
fixtures/ 离线回放底座
docs/   architecture.md 骨架现状 · schema.md 数据模型 · collectors.md 采集层 · memory.md 去重+diff
        · orchestrator.md 编排状态机 · analyst_writer.md Analyst 分级 + Writer 周报 · reviewer.md Reviewer 审稿门卫
tests/  冒烟 / mock / 数据层 / 采集层 / 去重 / diff / 编排状态机 / analyst 分级 / writer 周报 / reviewer 门卫
```

`orchestration/` Phase 4–6 已落地：state（共享可序列化 State + gate_trace/human_inbox）/ planner（周期换算）/
nodes（七节点，analyst·writer P5、reviewer_node P6 路由做实）/ graph（StateGraph + MemorySaver + 门卫条件边）/
pipeline（run_cycle / demo_two_weeks）。`analyst/ writer/ reviewer/` 已是真实包；`reporter/ eval/` 分别留 Phase 7 / Phase 8。
