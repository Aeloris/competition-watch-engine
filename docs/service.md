# 服务层：任务 / 定时 / 告警 / 健壮性（Phase 7）

> Phase 7 交付（README2 §5.9 定时与告警 + §5.10 服务层健壮性与观测 / §7.4 row 7）：
> 把 Phase 4–6 已跑通的单条流水线（`run_cycle`），包成**一次"本期巡检" = 一个 Task** 的服务层
> —— FastAPI **任务管理**（POST /tasks → 同步执行 + 返回终态）、**APScheduler 定时**（每周一 09:00
> 报上周）、**告警 webhook 适配**（Mock 台账离线可查，Webhook 壳诚实占位）、**run 级失败隔离**
> （一竞品挂不影响其它）、**trace 计数链收口**（采集 N → 去重 M → diff K → 门卫拦 X 在服务层可查）。
> 验收（README2 §7.4 row 7）：接口全套 + 定时/告警可演示 + 故障演练。**144 测试全绿**
> （123 基线 + 21 条 service：`tests/test_service_runtime.py` 13 + `tests/test_service_api.py` 8）。
> 编排与门卫内部口径见 [`docs/orchestrator.md`](orchestrator.md) / [`docs/reviewer.md`](reviewer.md)。

---

## 1. 一张图：一次"本期巡检"如何被建、跑、存、查

```mermaid
flowchart LR
  subgraph TRIG["触发"]
    CRON["APScheduler weekly_report<br/>每周一 09:00（config.schedule.cron）"]
    MAN["POST /tasks<br/>（手动/演示，period/competitor 可指）"]
  end
  TRIG --> RUN["ServiceRunner.execute_task<br/>= Task：本期 × 多竞品"]
  subgraph RUN1["逐 run 执行（run 级隔离 try/except）"]
    R1["Run: crm_alpha W35<br/>run_cycle(...)"]
    R2["Run: bi_beta W35<br/>run_cycle(...)"]
  end
  RUN1 --> FAIL["某 run 挂 → failed+error<br/>健康竞品照常 done"]
  FAIL --> TASK["TaskStore.finalize<br/>completed / partial / failed"]
  TASK --> META[("data/service/tasks/{task_id}.json<br/>TaskMeta + RunMeta 元数据")]
  R1 & R2 --> PIPE[("data/pipeline/{run_id}.json<br/>Phase 4 全量周报 state，回读用")]
  TASK --> AL["Notifier 告警"]
  AL --> LED[("data/service/alerts.json<br/>告警台账")]
  AL -.Webhook 壳.-> WH["真 HTTP 推送（企微/飞书/邮件占位）"]
  META --> API["GET /tasks · /inbox · /alerts"]
  PIPE --> API
```

- **Task = 一次被调度/手动的"本期巡检"**（period × competitors[]）；**Run = 单次 run_cycle**
  （竞品 × period）。Task 聚合 Run：`TaskStatus` = running → completed / partial / failed；
  `RunStatus` = pending / running / done / failed。
- **两层存储分开**：TaskStore 只存元数据（`data/service/tasks/{task_id}.json`，文件即状态、
  原子写 `.tmp`+`os.replace`）；每 run 的**全量周报 state** 仍在 Phase 4 的
  `data/pipeline/{run_id}.json` —— 报告路由（GET /tasks/{id}/report）从这里合并回 draft。
  不把大对象塞进 Task 文件 = 列表/详情查询轻，审计留痕与回放对象分离。

## 2. 对象与存储（service/schemas.py + service/store.py）

| 对象 | 含义 | 关键字段 |
|---|---|---|
| `TaskMeta` | 一次巡检 | task_id / period / competitors_requested / status / runs[] / created_at / finished_at / summary（聚合 done/failed/human/inbox + chains）/ error |
| `RunMeta` | 单次 run_cycle 的查询视图 | run_id / competitor_id / period / status / **verdict** / **trace** / diff_summary / **threat_radar** / gate_problems / gate_trace_count / inbox_count / human_inbox / error / started_at / finished_at |
| `AlertEvent` | 一条告警 | alert_id / event_type（high_threat · human_inbox · run_failed）/ competitor_id / period / run_id / summary / payload / ts / delivered_by（mock · webhook_placeholder） |

`TaskStore.create_task → add_run（同 run_id 幂等覆盖）→ finalize` 一次跑完一条链路；
`list_tasks()` 按 created_at 倒序，**损坏/半截文件读取层容忍跳过**（审计台账以可读为准）。
告警台账 `data/service/alerts.json` 数组 + 原子写。同 (竞品, 期) 重跑 = 新 task_id + 新 run_id →
两个 Task 文件并列 = 审计留痕（服务层关心"每次调度都发生过什么"，不幂等覆盖；
幂等覆盖是 Phase 1 memory 写快照的职责）。

## 3. trace 计数链收口（README2 §5.10 "采集 N → 去重 M → diff K → 门卫拦 X"）

每个 done 的 Run 把最终 State 里的计数**抄进 RunMeta**，一次巡检的 summary 再聚合出 `chains`：

```jsonc
// GET /tasks 返回的 summary.chains[0]
{
  "competitor_id": "crm_alpha",
  "trace": {"collected": 5, "merged": 3, "diff": 3},   // N → M → K（Phase 2/3 埋，服务层收口）
  "verdict": "PASS",
  "gate_problems": 0,                                    // 门卫拦的问题条数
  "inbox": 0                                             // 人工收件箱条数
}
```

于是"采集多少 → 并掉多少 → diff 出几条变更 → 门卫拦了几条 / 转人工几条"在 HTTP 层一眼可查，
不再需要翻 pipeline json。实测口径（fresh-store W35，非编造，测试锁死）：crm **5→3→3**、
bi **5→4→4**；威胁雷达 crm {high1, med1, low1}、bi {high1, med1, low2}，各项之和 == diff K。

## 4. 告警语义 + Notifier 抽象（service/notifier.py）

| event_type | 何时触发 | 说明 |
|---|---|---|
| `high_threat` | 门卫 **PASS** 且威胁雷达 `high>0`（`schedule.alert_on_high_threat=True` 缺省） | "高威胁不等周报即时提醒"：真实 W35 有 high → 每竞品一条 |
| `human_inbox` | 门卫 **verdict=human** | 转人工收件箱（先审后发的告警出口） |
| `run_failed` | 该 run `run_cycle` 抛异常 | 失败告警（run 级隔离的可见信号） |

`Notifier` 是接口：`MockNotifier`（**默认**，`make_alert → append_alert` 落台账，离线可演示）；
`WebhookNotifier`（配置 `alerts.webhook_url` 非空时启用）——**空 url 构造即 ValueError（fail-fast，
不许静默退化不发）**，`deliver` 抛 `NotImplementedError`（诚实占位：真 HTTP 推送接企微/飞书/邮件时
在这实现；服务端收到 NotImplemented 回 501，不假装已发）。告警失败绝不拖垮巡检（runner 捕获跳过）。

## 5. 定时口径（service/scheduler.py + runner.last_completed_iso_week）

- cron 由 `config.schedule.cron` 给出，注册成 APScheduler `weekly_report` job（不 start 不真等）；
  job 内容 = `run_now(ctx)` —— **调度与执行解耦**：APScheduler 只负责"到点叫你"，真正跑的是 runner。
- `last_completed_iso_week(today)`：报"**最近一个已结束的 ISO 周**"——`今天 − (weekday+7) 天` 到那周的
  周一，取其 ISO 年/周号。周一 09:00 cron 触发时正好报上周（例：2026-09-04 周五 → **2026-W35**；
  2026-09-07 周一 → **2026-W36**）。与 Phase 4 `Planner.default_fetched_at`（周期结束后的周一 09:00）
  对齐 → 同 period 同输入即同结果，回放确定性。
- `schedule.enabled` 缺省 `false`（main lifespan 只在 true 时 start，离线测试不碰调度线程）。

> **口径坑（实测锁死，别改回去）**：APScheduler 的 `CronTrigger` 数值 day-of-week 是 **0=周一、最大 6**
> —— 与标准 cron（1=周一、0=周日）不同。故 config 用名字 `"0 9 * * mon"` 免歧义；若改成 `"0 9 * * 1"`
> 会真触发到**周二**。`test_weekly_cron_fires_monday_0900` 从 config 读值 + 走真注册路径断言下个触发点
> = 每周一 09:00，防静默漂移。

## 6. HTTP 接口（app/routers + main 0.7.0）

| 方法/路径 | 作用 | 返回 |
|---|---|---|
| `POST /tasks` | 建 Task 并**同步执行**（period 缺省 = 最近已结束 ISO 周；competitor_id 可单竞品调试；未知竞品 404） | 201 + TaskMeta（计数链 + 门卫判定） |
| `GET /tasks` | Task 列表（created_at 倒序） | count + tasks[] |
| `GET /tasks/{id}` | Task 详情（404 = 不存在） | TaskMeta |
| `GET /tasks/{id}/report` | 合规周报：各 done run 从 pipeline json 合并全量 draft（headline/sections/items/sources + threat_radar + trace） | report[] |
| `GET /inbox` | 人工收件箱：**只有 verdict=human 的 run** 的 human_inbox 条目（status:"pending"，Phase 8 面板消费） | count + entries[] |
| `GET /alerts` | 告警台账倒序（最新在前） | count + alerts[] |
| `POST /alerts/hook` | 手动投递演练（body=event_type/summary/…；非法 event_type 422） | accepted 回执 |

`main.py` 挂 `app.state.service = build_service_context()`（settings + TaskStore + Notifier + Runner
捏成一个依赖）；routers 经 `get_service_ctx` 读。`schedule.enabled=true` 时 lifespan 启动 scheduler。
版本 `0.7.0`。健康检查 `/health` 保持 Phase 0 契约不变。

## 7. 健壮性：run 级失败隔离 + 故障演练

`execute_task` 对每个竞品**各自 try/except**：某竞品 `run_cycle` 抛错 → 该 Run 记 `failed + error` +
`run_failed` 告警，**其余竞品照常出结果**（Task 终态 partial）——这是 Phase 2 采集层"源级隔离"
在服务层的升级（README2 §5.10 服务层健壮性）。

两条故障演练测试（monkeypatch 注入，非真实故障）：
- **演练 A（run 级隔离）**：patch `service.runner.run_cycle` 对 `crm_alpha` 抛 RuntimeError →
  crm failed+error、bi 照常 done+PASS、Task=partial、`run_failed` 告警 1 条
  （`test_fault_drill_run_level_isolation`）。
- **演练 B（注入假事件延伸到服务层）**：patch `graph.NODE_IMPLS["writer"]` 返回"真 fp 拼编造 URL"的
  条目（复用 Phase 6 `test_graph_injected_fake_item` 手法）→ 门卫 human 拦下、Task 正常执行完但
  `inbox_count=1`、`UNSOURCED_URL` 落人工收件箱、`human_inbox` 告警 1 条 —— **假事件绝不会以
  "零收件箱地 completed"蒙混过关**（`test_fault_drill_bad_writer_human_inbox_and_alert`）。

## 8. 决策 why（面试讲这几点）

1. **为什么 Task/Run 两层，且 TaskStore 只存元数据**：一次巡检挂了半个竞品，你要能问
   "谁成功了、谁失败了、计数链怎样"，而不是整单作废或翻大 JSON；全量 draft 留 pipeline json，
   报告路由按 run_id 回读 —— 列表/详情查询保持 O(小)。
2. **为什么同一 (竞品,期) 重跑不幂等覆盖**：服务层是**调度审计**视角——每次 cron 触发都应该留痕；
   "同一份数据别重复存"是 Phase 1 memory 写快照的职责。两层语义不同，接口天然不同。
3. **为什么 APScheduler 只"到点叫你"**：job 主体就是 runner.run_now，调度与执行解耦 → 手动触发
   （demo/测试）与定时走同一条 execute_task 路径，不用真等周一也能验证口径。
4. **为什么 Webhook 空 url 要 fail-fast、deliver 要抛 NotImplemented**：告警是最容易"静默坏掉"的
   环节 —— 配了 url 却不发 = 假安全感。宁可构造期报错、投递期 501，也不假装已推送给老板。
5. **为什么 run 级隔离而不是"整单原子"**：竞品独立巡检，一个源挂了整个 Task failed 会掩盖
   健康竞品的高威胁信号（那正是要告警的东西）。partial + 逐 run 状态是最小诚实呈现。

## 9. 局限与边界（诚实口径，别把 P7 说成生产级平台）

- **Webhook 仍是占位壳**：企微/飞书/邮件推送未接真 HTTP（需要真实 endpoint/密钥，属接入期）；
  本期保证的是**适配层 + fail-fast + 不假装已发**。Mock 台账是离线的诚实默认。
- **定时只在进程活着时生效**：单进程长跑服务定位；无持久任务队列/错过补偿（README2 未承诺）。
- **同步执行**：POST /tasks 在请求内跑完多竞品才返回（演示直观、curl 就能看终态）；
  超长任务 / 并发限流未做（Phase 8 面板/任务队列可接）。
- **收件箱 resolution 未做**：inbox 只读"pending 待确认"；人工确认放行/关闭是 Phase 8 面板职责
  （本期先审后发 = 拦截到"等人工"，人工动作是下一步）。
- **README2 里的目标指标（如告警延迟、覆盖率）仍是设计目标占位**；本期给的实测口径只有：
  接口/定时/告警/隔离/故障演练全部有测试锁死（21 条），trace/radar 数字为实测（非编造）。

## 10. 运行与验证

```bash
uv sync
uv run pytest                       # 全绿（Phase 7 = 144；service 21 条）
uv run pytest tests/test_service_runtime.py tests/test_service_api.py -v
uv run uvicorn app.main:app --port 8000   # 服务化 HTTP
# 跑一次"本期巡检"（单竞品调试 / 缺省 = 全部竞品 + 最近已结束 ISO 周）：
curl -s -X POST localhost:8000/tasks -H 'Content-Type: application/json' -d '{"period":"2026-W35","competitor_id":"crm_alpha"}'
curl -s localhost:8000/tasks                 # 任务列表（含 trace 计数链 + 门卫判定）
curl -s localhost:8000/tasks/<task_id>/report # 合规周报全量 draft
curl -s localhost:8000/inbox                 # 人工收件箱（PASS 时为空）
curl -s localhost:8000/alerts                # 告警台账（真实 W35 有 high → high_threat）
curl -s -X POST localhost:8000/alerts/hook -H 'Content-Type: application/json' \
     -d '{"event_type":"high_threat","summary":"手动投递演练"}'
```

实测口径（fresh-store W35，测试锁死，非编造）：crm **5→3→3**、bi **5→4→4**；雷达 crm {1,1,1} /
bi {1,1,2}；全 PASS、收件箱空、gate_trace=1；每竞品触发 1 条 high_threat 告警。
演练 A（run 挂）→ Task partial + run_failed 告警；演练 B（注入假事件）→ human + inbox + human_inbox 告警。
数据全隔离在 `data_dir`：测试用 `build_service_context(data_dir=tmp)`，两个 ctx 互不写穿。

---

_更新日志_：2026-09-04 建（Phase 7 交付：service/{schemas,store,runner,notifier,scheduler,context} +
config schedule/alerts + app/routers{tasks,inbox,alerts} + main 0.7.0；run 级失败隔离、trace 计数链收口、
Mock/Webhook 告警、每周一 09:00 cron（名字 mon 免 APScheduler 数值歧义）；144 测试）。
