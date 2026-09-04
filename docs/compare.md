# 跨竞品横向对比（compare）

> 增量「跨竞品横向对比」（在 Phase 8 发布视图之上的"横着看"层）。README2 设计路线止于 Phase 8（row 8）；
> 本条是路线图之后的自主增量，不改变 P0–P8 任何既有语义——离线 Mock、确定性、可测、无 API key 约束不变。
> 代码：`compare/{model,builder}.py`（纯函数）+ `service/compare.py`（接入）+ `app/routers/{tasks,panel}.py`（HTTP/面板）。

## 1. 要解决的问题

单竞品各跑各的 run，各自交一份"本期变更 + 威胁雷达"周报。但产品经理真正想问的是**横着**的：
本周 feature 这条线上两竞品谁先动手？price 上谁动了谁没动？两家的雷达摆在一起谁的动作面更大？
Phase 8 的发布视图已把"单竞品先审后发 + 可复核"做全，本增量在它之上加一个确定性对齐视图。

## 2. 为什么长在 Task 层、且只消费已放行条目

- **Task = 同一 period × 多竞品的 Run 集合**（`service/schemas.py` TaskMeta）——两竞品 W35 本来就在同一个 Task
  里，对比输入天然齐全，不需要跨 Task 拼。
- **只消费 published_view 的 published（已放行）条目**：先审后发纪律延续到对比层——门卫转人工、仍挂起（held）
  或人工驳回（dismissed）的条目不进横向视图，**不会拿"审后剔除的假货"去误导横向判断**。
- published 条目已带 `dimension / severity / kind / title / first_seen / evidence_urls` → 纯确定性对齐，
  零 LLM、零 API key。

## 3. 对齐语义与诚实边界（本模块的灵魂）

**维度对齐 ≠ 实体对齐。** `dimension` 是共享枚举（feature/price/market/org/compliance），行 = dimension 轴；
本增量**不做**"A 的 v12 智能跟进助手 == B 的图表库 3.0"这类**实体级等价**——那需要语义实体解析 /
embedding 近并（更后续）。因此措辞是"feature 线谁先动"，绝不宣称"同一场竞赛"。

| 能诚实断言（本增量做） | 不承诺（写进边界，不做） |
|---|---|
| 谁在本期动了哪些维度、谁没动 → **单边动作维度 = 事实陈述**（不称对方"盲点"） | 实体级等价 / 语义对齐 |
| 同维度多竞品都动 → 谁**先动**（比各竞品该维度 items 最早 `first_seen`；**同日并列**；单边独占即它） | 跨实体时间先后 |
| **无日期证据**（该维度 items 全无 first_seen）→ `orderable=False`、`first_mover` 空——**宁可不说，不编先后** | 无证据猜先后 |
| 同维度 high 威胁条数 / 雷达**并列呈现**（同轴可比） | 跨竞品排"谁更危险"的序（severity 是各竞品语境下 Analyst 打的，直接比=过度解读） |
| `max_severity`（high>medium>low）只在**单竞品格内**做（事实汇总） | 用严重度当跨竞品比较依据 |

## 4. 输出结构（CrossCompareReport）

```
task_id / period / generated_at(None，纯派生不带时间戳 → 确定性)
competitors[]:  { competitor_id, run_id, headline_count, radar{high,medium,low}, dims_moved[] }
rows[]:         { dimension, moved_by[], all_moved, orderable, first_mover[],
                  cells[{competitor_id, item_count, earliest, max_severity, high_count, items[]}] }
both_moved_dims[]    全员动作维度（moved_by 覆盖全部输入竞品）
single_moved_dims[]  只有一家动的维度 {dimension, mover}
first_move_counts{}  各竞品"率先(含独占)动作维度数"
```

- 行序 = `Dimension` 声明序（feature→price→market→org→compliance），只含真实出现的维度；
- 竞品按 `competitor_id` 规范化 → 输入乱序不影响输出；格内条目按 `(first_seen, fp, title)` 稳定排序；
- 雷达/条数一律由 items **重算**，不信任调用方（与 WeeklyReport 同一哲学）。

## 5. 服务化与 HTTP

- `service/compare.py::compare_view(task_store, settings, task_id)`：published_view → 过滤出有 ≥1 条已放行条目的
  run → 映射 CompetitorView → `compare.builder.build`。**<2 家有已放行条目 → CompareNotPossible**（单竞品 / 全挂起 /
  全剔除则没有"横向"可言）。
- `GET /tasks/{task_id}/compare`：200 返回报告；task 不存在 404；`CompareNotPossible` → 422 + reason。
- 面板 `GET /panel/task/{task_id}` 新增「跨竞品横向对比」段：维度行 × 竞品列，列头给 radar/条数/覆盖维度，
  格内 `count · high · 最早 · max`，**★ 先行**标记 first_mover；底部 derived（全员 / 单边 / 率先计数）；
  单竞品任务明说"无可比（需 ≥2 家有已放行条目）"。

## 6. 实测（真实 W35 批量 Task，crm_alpha + bi_beta 双竞品 PASS 全自动发布）

| 观察 | 值 |
|---|---|
| feature 行 | 双竞品都动（crm v12 发布系 + bi 图表库/看板系）→ `moved_by=[bi_beta,crm_alpha]`、`all_moved=true` |
| feature 先行方 | **bi_beta**（其最早 first_seen 2026-08-24 < crm 2026-08-25） |
| market 行 | 仅 bi_beta（盘点文，Analyst keyword 归 market）→ **单边动作维度**，crm 本周未动（事实陈述） |
| 列头雷达 | bi_beta {high1, med1, low2}（5→4→4）· crm_alpha {high1, med1, low1}（5→3→3）——与既有实测一致 |
| derived | both_moved_dims=[feature]；single_moved_dims=[market←bi_beta]；first_move_counts={bi_beta: 2} |

## 7. 运行与验证

```bash
uv run pytest tests/test_compare_builder.py   # M0 纯函数 9 条（确定性/边界/格内汇总/入参防御）
uv run pytest tests/test_compare_api.py       # M1 端到端 6 条（真实批量 W35 + 422/404 + 面板 ★）

# 手动：跑一次双竞品 W35 巡检再看横向对比
# POST /tasks   {"period":"2026-W35"}
# GET  /tasks/{task_id}/compare   → 见 §6 实测表；重复请求逐字节一致
# GET  /panel/task/{task_id}      → 横向对比段（★ 先行方）
# POST /tasks   {"period":"2026-W35","competitor_id":"crm_alpha"}  单竞品
# GET  /tasks/{task_id}/compare   → 422 detail=需 ≥2 ...
```

## 8. 局限与后续

- **实体级等价不做**：feature 线"谁先动"是维度动作先后，不宣称 A 的某功能对应 B 的某功能；要实体对齐需
  embedding 近并 / LLM 语义判同（provider-gated，本期不接）。
- severity 只并列计数，不做跨竞品危险度归一。
- compare 消费的是**发布视图（查询态快照）**；真"对外推送横向周报"仍在 reporter 接入期（与 P8 发布闭环同边界）。
- 二维（>2 竞品）时 `all_moved` 要求覆盖全部；既非全员又非单边的维度只在 rows 逐格可比、不硬造 derived。

## 更新日志

2026-09-04 建（增量横向对比交付：compare/ M0 + service/compare + 路由/面板 M1 + 文档 M2，pytest 165→180，
main 0.9.0）。
