# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 7（服务化——一次"本期巡检"= 一个 Task，任务/定时/告警/健壮性全可演示）

沿革：Phase 0 = 最小服务骨架 + Mock 底座；Phase 1 = 数据模型/持久化/指纹；Phase 2 = 采集层（并发、隔离、可数）；
Phase 3 = **去重并事件 + 跨期 diff 找"本周变了什么"**；Phase 4 = **LangGraph 周期流水线**；Phase 5 = **Analyst 威胁分级 +
Writer 合规周报**；Phase 6 = **Reviewer 审稿门卫**（grounding 闭环自证 + 矛盾检测 + 与上期重复，注入假事件能被拦、
先审后发做实、人工收件箱）。Phase 7 把 Phase 4–6 的单条 `run_cycle` 包成服务层 `service/`：

- **任务管理（Task = 本期 × 多竞品）** `service/runner.py`：`execute_task` 逐竞品跑 `run_cycle`，**run 级失败隔离**
  （一竞品挂 → 记 failed+error + 告警，健康竞品照常出结果，Task 终态 partial）；结果进 TaskStore
  （`data/service/tasks/{task_id}.json`，文件即状态、原子写）；每 run 全量周报仍在 `data/pipeline/{run_id}.json`
  （报告路由按 run_id 回读，不把大对象塞进 Task 元数据）。
- **trace 计数链收口**：RunMeta 抄 `trace{collected,merged,diff}` + 门卫判定（verdict/gate_problems/inbox），
  summary 聚合出 `chains` —— "采集 N→去重 M→diff K→门卫拦 X"在 HTTP 层一眼可查。实测（fresh-store W35，非编造）：
  crm **5→3→3**、bi **5→4→4**；雷达 crm {high1,med1,low1} / bi {high1,med1,low2}，各项之和 == diff K。
- **告警 Notifier** `service/notifier.py`：`high_threat`（PASS 且 radar.high>0，不等周报即时提醒）/ `human_inbox`（门卫
  转人工）/ `run_failed`。默认 `MockNotifier` 落台账 `data/service/alerts.json`（离线可演示）；配 `alerts.webhook_url`
  则用 `WebhookNotifier` 壳——**空 url 构造即 ValueError（fail-fast）**、`deliver` 抛 `NotImplementedError`
  （诚实占位：真 HTTP 推送接企微/飞书/邮件属接入期，不假装已发）。
- **APScheduler 定时** `service/scheduler.py`：`config.schedule.cron`（默认 `"0 9 * * mon"` 每周一 09:00）注册成
  `weekly_report` job，job = `run_now`（调度与执行解耦，手动触发同一路径）；`last_completed_iso_week` 保证周一 09:00
  cron 恰好报"上一完整周"（2026-09-07 周一 → 2026-W36）。`schedule.enabled=false` 缺省不启（离线测试不碰调度线程）。
  口径坑（实测锁死）：APScheduler 的数值 day-of-week 是 **0=周一**（标准 cron 是 1=周一）→ 用名字 `mon` 免歧义。
- **FastAPI 0.7.0** `app/routers`：`POST /tasks`（建 + 同步执行 → 201 终态）、`GET /tasks`、`GET /tasks/{id}`、
  `GET /tasks/{id}/report`（各 done run 从 pipeline json 合并全量周报 draft）、`GET /inbox`（只有 verdict=human 的
  run 的收件箱条目，status:"pending"）、`GET /alerts`、`POST /alerts/hook`（手动投递演练；非法 event_type 422）。
  `schedule.enabled=true` 时 lifespan 自动注册巡检。
- **故障演练实测（monkeypatch 注入，非编造）**：演练 A 让 `crm_alpha` 的 run_cycle 抛错 → Task partial + crm failed+
  error + bi 照常 PASS + `run_failed` 告警；演练 B 注入"真 fp 拼编造 URL"的假 writer（复用 P6 手法）→ 门卫 human 拦下、
  落人工收件箱（`UNSOURCED_URL`）+ `human_inbox` 告警——**假事件绝不会以"零收件箱地 completed"蒙混过关**。
- **全绿 144**（123 基线无回归 + service 21 条：`tests/test_service_runtime.py` 13 + `test_service_api.py` 8）；
  数据全隔离：测试用 `build_service_context(data_dir=tmp)`，memory/pipeline/service 互不写穿。
- **诚实边界**：Webhook 仍是占位壳（不真发 HTTP）；inbox 只读"pending 待确认"，人工确认放行是 Phase 8 面板职责；
  定时只在进程活着时生效（单进程长跑定位）；POST /tasks 同步执行（演示直观，超长任务队列留 P8）。README2 里的
  目标指标（告警延迟/覆盖率等）是**设计占位、从未实测，不上简历**——本期实测口径只有上面这些测试锁死的数字。
  细节见 [`docs/service.md`](docs/service.md)。

```bash
uv sync
uv run pytest            # 全绿（144）
uv run python -c "from orchestration import demo_two_weeks; demo_two_weeks()"   # 流水线回放：trace + threat_radar + gate
uv run uvicorn app.main:app --port 8000   # http://127.0.0.1:8000/health
# 跑一次"本期巡检"并查结果（也可缺省 period=最近已结束 ISO 周 / competitor=全部竞品）：
curl -s -X POST localhost:8000/tasks -H 'Content-Type: application/json' -d '{"period":"2026-W35","competitor_id":"crm_alpha"}'
curl -s localhost:8000/tasks                  # 任务列表：trace 计数链 + 门卫判定 + 收件箱数
curl -s localhost:8000/tasks/<task_id>/report # 合规周报全量 draft（headline/sections/items/sources）
curl -s localhost:8000/inbox                  # 人工收件箱（PASS 语料为空）
curl -s localhost:8000/alerts                 # 告警台账（真实 W35 有 high → high_threat）
```

## 目录

```
app/    FastAPI 入口 + Phase 7 routers    config/  强类型配置(fail-fast) + schedule/alerts
llm/    Provider 抽象                     sources/ 信源适配器 + MockSource
memory/ 数据对象+持久化+指纹+跨期diff      collectors/ 采集层：并发/归一化/失败隔离
dedupe/ 维度规则分类 + 版本锚去重合并       orchestration/ LangGraph 状态机（Phase 4–6）
analyst/ ThreatRubric 规则阶梯分级（Phase 5）    writer/ WeeklyReport 合规周报（Phase 5）
reviewer/ 审稿门卫：grounding/矛盾/合规 typed problems（Phase 6）
service/ Task/Run/告警契约 + TaskStore 文件即状态 + execute_task(run 级隔离) + Notifier + 调度（Phase 7）
fixtures/ 离线回放底座                     reporter/ eval/ 占位（P8 面板/Eval）
docs/   architecture.md 骨架现状 · schema.md 数据模型 · collectors.md 采集层 · memory.md 去重+diff
        · orchestrator.md 编排状态机 · analyst_writer.md 分级+周报 · reviewer.md 审稿门卫 · service.md 服务层
tests/  冒烟 / mock / 数据层 / 采集层 / 去重 / diff / 编排 / 分级 / 周报 / 门卫 / service_runtime / service_api
```

`orchestration/` Phase 4–6 已落地：state / planner / nodes（七节点）/ graph（StateGraph + MemorySaver + 门卫条件边）/
pipeline（run_cycle / demo_two_weeks）。`service/` Phase 7 已落地：schemas / store / runner / notifier / scheduler /
context；HTTP 层在 `app/routers`（薄，全逻辑在 service）。`reporter/ eval/` 仍占位（告警职责已由 service/notifier
承担；周报对外推送 + Phase 8 面板/Eval 未做）。
