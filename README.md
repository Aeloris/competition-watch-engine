# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 8 + 增量「跨竞品横向对比」（README2 路线图已全部交付；横向对比为其后自主增量）

沿革：Phase 0 = 最小服务骨架 + Mock 底座；Phase 1 = 数据模型/持久化/指纹；Phase 2 = 采集层（并发、隔离、可数）；
Phase 3 = **去重并事件 + 跨期 diff 找"本周变了什么"**；Phase 4 = **LangGraph 周期流水线**；Phase 5 = **Analyst 威胁分级 +
Writer 合规周报**；Phase 6 = **Reviewer 审稿门卫**（grounding 闭环自证 + 矛盾检测 + 与上期重复，注入假事件能被拦、
先审后发做实、人工收件箱）；Phase 7 = **服务化**（一次"本期巡检"= 一个 Task：任务/定时/告警/健壮性/trace 计数链收口）。
Phase 8 把 P6/P7 停在"拦到等人工"的先审后发推到底——**人工定案才发布**，并给流水线配一套**离线回放评测 + 门禁**：

- **人工放行闭环** `service/publish.py`：收件箱条目带稳定 `entry_id = {run_id}#{seq}`；`POST /inbox/resolve` 定案语义
  —— **release/dismiss = 销案终态**（再定案 409，不许覆盖审计决定）/**note = 只备注不销案**；`{action,by,note,resolved_at}`
  审计原样落回 run.human_inbox（谁、何时、为何定案可追）。
- **发布视图** `GET /tasks/{id}/published`：把周报 draft 按 resolution 过滤成 **published / held / dismissed** ——
  自审通过自动发布、放行条目带 human_released + resolution 原文、未定案/只备注挂 held、驳回进 dismissed 审计剔除区。
- **简单面板（零构建 HTML）** `app/routers/panel.py`：`/panel` 总览（待办收件箱 + 最近任务 + 最近告警）+
  `/panel/task/{id}`（放行/驳回/备注 按钮 + 发布视图），原生 JS 调同款 JSON API，逻辑全在 service/publish + TaskStore。
- **Eval-Harness 回放评测 + 门禁** `eval/`（README2 §5.11 的可测子集）：三面六指标，锁"造的固定场景里流水线
  稳定做到设计该做的"——**回归一致性**（真实 W34/W35 冻结回放：trace crm 5→3→3 / bi 5→4→4、radar、verdict 全 PASS
  规范数字锁死）、**gold 真值集**（从 fixture 原文手标 7 事件 → 事件一致率 7/7 + 去重重复组全并 + 每周报来源都真采集到）、
  **对抗注入**（unsourced_url / ungrounded_fp / ungrounded_claim / contradiction 各单码纯 + mixed 双码 + duplicate 门卫级
  → 6 场景全拦、编造拦截率 1.0、真实语料 0 误拦）。config `eval:` 门禁阈值全 1.0；
  `uv run python -m eval.run` 写 `data/eval/eval_report.{json,md}`、门禁失败退出码 1。细节见 [`docs/eval.md`](docs/eval.md)。

增量（README2 路线图 row 8 之后，自主扩展，离线 Mock/确定性/无 key 约束不变）——**跨竞品横向对比** `compare/`
+ `service/compare.py`：在 Phase 8 发布视图之上加一层"横着看"——把同 Task 各竞品**已放行**条目按共享
dimension 轴对齐（**维度对齐 ≠ 实体对齐**：feature 线谁先动、谁动了谁没动、单边动作=事实陈述不称盲点、
同维度高威胁并列计数不跨竞品排危险序；无 first_seen 日期证据宁可不说先后）。`GET /tasks/{id}/compare`
返回报告（task 缺 404、**<2 家已放行 422** 明说无可比——先审后发后才见真章）；面板 task 页加横向对比表
（★ 先行方）。实测（真实 W35 双竞品批量 Task，PASS 全自动发布）：feature 双竞品都动、**bi 先动**
（最早 first_seen 08-24 < crm 08-25）；market 仅 bi（盘点=单边动作维度）；雷达并列如实；重复 GET 逐字节一致。
细节见 [`docs/compare.md`](docs/compare.md)。

服务化底座（Phase 7）在下面，作为 P8 闭环的载体：

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
- **FastAPI 0.9.0** `app/routers`：`POST /tasks`（建 + 同步执行 → 201 终态）、`GET /tasks`、`GET /tasks/{id}`、
  `GET /tasks/{id}/report`（各 done run 从 pipeline json 合并全量周报 draft）、`GET /tasks/{id}/published`（Phase 8 发布视图）、
  `GET /tasks/{id}/compare`（增量：跨竞品横向对比）、`GET /inbox`（`?status=pending|all|resolved`，缺省 pending = P7 语义）、
  `POST /inbox/resolve`（Phase 8 人工定案）、`GET /alerts`、`POST /alerts/hook`（手动投递演练；非法 event_type 422）、
  `GET /panel` + `GET /panel/task/{id}`（Phase 8 面板，task 页含横向对比段）。
  `schedule.enabled=true` 时 lifespan 自动注册巡检。
- **故障演练实测（monkeypatch 注入，非编造）**：演练 A 让 `crm_alpha` 的 run_cycle 抛错 → Task partial + crm failed+
  error + bi 照常 PASS + `run_failed` 告警；演练 B 注入"真 fp 拼编造 URL"的假 writer（复用 P6 手法）→ 门卫 human 拦下、
  落人工收件箱（`UNSOURCED_URL`）+ `human_inbox` 告警，随后 `POST /inbox/resolve` release → 该条目从 held 进发布视图
  （human_released=true）、dismiss → 进 dismissed 审计区——**假事件绝不会以"零收件箱地 completed"蒙混过关**。
- **全绿 190**（126 基线 + service 23 + eval 15 + panel 7 + compare 16 + 审计回归 3：`tests/test_service_runtime.py`
  并发锁 ×2 + `tests/test_dedupe.py` 空标题丢卡 ×1）；数据全隔离：测试用 `build_service_context(data_dir=tmp)`，
  memory/pipeline/service 互不写穿。
- **诚实边界**：Webhook 仍是占位壳（不真发 HTTP），周报**对外真推送无渠道**——发布闭环止于 published_view（HTTP 查询态
  可复核快照）；Eval 数字是**固定语料回归基线**（gold 7 事件 + 6 对抗场景是"编得动的口径"，不是真实世界命中率；duplicate
  为门卫级非图级）；横向对比是**维度对齐不是实体对齐**（first_mover 是该维度动作先后，不宣称 A 的某功能 == B 的某功能，
  entity 级等价需语义解析/embedding；severity 并列不排危险序）；定时只在进程活着时生效（单进程长跑定位）；POST /tasks 同步
  执行（演示直观，超长任务队列留待接）。README2 里的目标指标（告警延迟/覆盖率等）是**设计占位、从未实测，不上简历**——
  本期实测口径只有上面测试锁死的数字。细节见 [`docs/service.md`](docs/service.md) + [`docs/eval.md`](docs/eval.md) +
  [`docs/compare.md`](docs/compare.md)。

```bash
uv sync
uv run pytest            # 全绿（190）
uv run python -m eval.run   # Eval-Harness 回放评测 → data/eval/eval_report.{json,md}；门禁失败退出码 1
uv run python -c "from orchestration import demo_two_weeks; demo_two_weeks()"   # 流水线回放：trace + threat_radar + gate
uv run uvicorn app.main:app --port 8000   # http://127.0.0.1:8000/health · http://127.0.0.1:8000/panel
# 跑一次"本期巡检"并查结果（也可缺省 period=最近已结束 ISO 周 / competitor=全部竞品）：
curl -s -X POST localhost:8000/tasks -H 'Content-Type: application/json' -d '{"period":"2026-W35","competitor_id":"crm_alpha"}'
curl -s localhost:8000/tasks                  # 任务列表：trace 计数链 + 门卫判定 + 收件箱数
curl -s localhost:8000/tasks/<task_id>/report # 合规周报全量 draft（headline/sections/items/sources）
curl -s localhost:8000/tasks/<task_id>/published   # 发布视图：published/held/dismissed（PASS run = 全发布）
curl -s localhost:8000/tasks/<task_id>/compare     # 跨竞品横向对比：feature 双动+bi 先动 / market 单边；单竞品 422
curl -s localhost:8000/inbox                  # 人工收件箱（PASS 语料为空；?status=all|resolved 可切）
curl -s -X POST localhost:8000/inbox/resolve -H 'Content-Type: application/json' \
     -d '{"entry_id":"<run_id>#<seq>","action":"release","by":"演示员","note":"人工核对无误"}'   # 人工放行
curl -s localhost:8000/alerts                 # 告警台账（真实 W35 有 high → high_threat）
curl -s localhost:8000/panel                  # 简单面板总览（浏览器打开更好看）
curl -s localhost:8000/panel/task/<task_id>   # 单任务面板：放行/驳回/备注按钮 + 发布视图
```

## 目录

```
app/    FastAPI 入口 + Phase 7+8 routers（tasks/inbox/alerts/panel）   config/  强类型配置(fail-fast) + schedule/alerts + eval 门禁
llm/    Provider 抽象                     sources/ 信源适配器 + MockSource
memory/ 数据对象+持久化+指纹+跨期diff      collectors/ 采集层：并发/归一化/失败隔离
dedupe/ 维度规则分类 + 版本锚去重合并       orchestration/ LangGraph 状态机（Phase 4–6）
analyst/ ThreatRubric 规则阶梯分级（Phase 5）    writer/ WeeklyReport 合规周报（Phase 5）
reviewer/ 审稿门卫：grounding/矛盾/合规 typed problems（Phase 6）
compare/ 跨竞品横向对比：model + builder 纯函数（增量：维度对齐≠实体对齐）
service/ Task/Run/告警契约 + TaskStore 文件即状态 + execute_task(run 级隔离) + Notifier + 调度
        + publish.py：resolution 语义 + published_view 发布视图（Phase 8）
        + compare.py：compare_view 消费发布视图 → 横向对比（增量）
eval/   Phase 8 回放评测：dataset(gold) / adversarial(6 对抗场景) / metrics(六指标) / harness / run(门禁 CLI)
fixtures/ 离线回放底座                     reporter/publisher.py 占位（对外真推送留接入期）
docs/   architecture.md 骨架现状 · schema.md · collectors.md · memory.md · orchestrator.md
        · analyst_writer.md · reviewer.md · service.md · eval.md（Phase 8）· compare.md（增量）
tests/  冒烟 / mock / 数据层 / 采集层 / 去重 / diff / 编排 / 分级 / 周报 / 门卫
        / service_runtime / service_api / eval / panel_api / compare_builder / compare_api
```

`orchestration/` Phase 4–6 已落地：state / planner / nodes（七节点）/ graph（StateGraph + MemorySaver + 门卫条件边）/
pipeline（run_cycle / demo_two_weeks）。`service/` Phase 7 已落地：schemas / store / runner / notifier / scheduler /
context，Phase 8 加 publish（resolution + published_view）。`eval/` Phase 8 已落地：dataset / adversarial / metrics /
harness / run。增量「跨竞品横向对比」已落地：`compare/`（model + builder 纯函数）+ `service/compare.py`（compare_view 消费
发布视图）+ HTTP `GET /tasks/{id}/compare` + 面板 task 页横向对比段。HTTP 层在 `app/routers`（薄，全逻辑在 service/eval/
compare）。`reporter/publisher.py` 仍占位（周报**对外**真推送属接入期；发布闭环的查询态已由 published_view 承担）。
`LICENSE` = MIT（Copyright (c) 2026 qiaosheng）。
