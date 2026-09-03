# 采集层 Researchers（collectors）

> Phase 2 交付：**并发采 3 类信源 → 归一化 FactCard；单源失败不影响全局**（README2 §5.3 落地）。
> 设计要义：每源一个 Worker = 并发/隔离单元；asyncio + 信号量限流；失败走"诚实标注"不进异常；trace 计数链第一节在此成形。

---

## 1. 一次采集怎么跑

```mermaid
flowchart LR
  subgraph IN["config.sources.enabled"]
    K1["website"]; K2["news"]; K3["rss"]
  end
  subgraph WK["build_collectors(settings) → {kind: CollectorWorker}"]
    W1["Worker(website)\n包 MockSource"]
    W2["Worker(news)\n包 MockSource"]
    W3["Worker(rss)\n包 MockSource"]
  end
  K1 --> W1; K2 --> W2; K3 --> W3
  W1 --> SEM["asyncio.Semaphore\nconcurrency_limit=4"]
  W2 --> SEM
  W3 --> SEM
  SEM --> R1["fetch(竞品, kind) → RawItems"]
  R1 --> NORM["归一化 RawItem → FactCard"]
  NORM --> SUM["CollectSummary\nby_source 计数 + cards 并集"]
  R1 -- 异常 → ISO["WorkerOutcome(ok=False) 旁路"]
  ISO --> SUM
  SUM --> OUT["failures 清单（诚实标注，不吞不崩）"]
```

## 2. 三个设计决策 & 为什么

1. **并发单元 = 每源一个 Worker（包一个 SourceAdapter）**
   - enabled 里每个 kind 建一个 `CollectorWorker`，Worker 手里的是 adapter。现在三类源背后是同一个 MockSource
     （读同一 fixtures 按 kind 过滤），为每个 kind 包一层 Worker 让"并发 + 隔离"的语义在**实例**层面成立。
   - 将来换 HTTP/RSS/搜索 adapter = 替换 Worker 手里包的 adapter，编排层零改动——"新增信源 = 新增 adapter，不加业务代码"落地。
2. **asyncio + 信号量限流**
   - 真场景是 N 竞品 × M 信源、每个都在等网络 IO → 顺序跑太慢，IO 密集天然可并发；asyncio 单线程协程避免线程同步地狱。
   - `asyncio.Semaphore(settings.sources.concurrency_limit)` 把同时在飞的采集请求钉在天花板下（控 QPS/成本）。
     `collect_competitor` 内部 `_gather_bounded(coros, limit)`；单测用 6 慢任务 + limit=2 断言峰值并发 ≤2 且确实发生过并发。
3. **失败隔离 + 诚实标注（部分失败不崩）**
   - Worker.run 自己 try/except：成功 → `WorkerOutcome(ok=True, cards)`；失败 → `(ok=False, error)`，**绝不外抛**；
     编排层把失败汇入 `CollectSummary.failures`，其它源照常产出。
   - 单测注入 `BoomAdapter`（fetch 必抛 ConnectionError）到 rss → website/news 照常出 4 卡、failures 记 `rss/crm_alpha: ConnectionError...`。
   - 哲学同项目一"API 不吞引擎诚实信号"：**单源失败时报告要标注，而不是假装整期情报完整**（README2 §1.3 步骤⑦ M 分支）。
   - `max_retries`/`fetch_timeout_sec` 已在 Worker 接口与 config 占位（重试退避退避实现好，等真网络 adapter 接入时启用；mock 永不失败故不触发）。

## 3. 归一化归属

- `collectors/normalize.py::rawitem_to_factcard(item, competitor_id, fetched_at) -> FactCard` 是**唯一归一化入口**
  （搬运字段，id/digest 由 FactCard 的 after-validator 派生）。
- **fetched_at 必须可注入**：Worker 不传则取当前时间；测试显式传固定值 → 两次采集逐字段一致（回放确定性）。
- 与 `memory/corpus.py`（Phase 1 的离线回放 loader）的关系：corpus 是"按 ISO 周切档"的存档工具，属历史路径；
  流水线正式采集走本层。归一化规则如后续演进，以 `collectors/normalize.py` 为准。

## 4. trace 计数链第一节

README2 §5.10 要的观测是 `采集 N → 去重剩 M → diff 出 K → 门卫拦 X` 的计数链；Phase 2 把第一节做好：

- `CollectSummary.by_source[].{fetched, cards, failed}` + `failures[]`。
- 这三个数就是排障第一块拼图，也是 Phase 8 eval 断言"某源本次失败"与"召回多少条"的对象。

## 5. 运行与验证

```bash
uv run pytest                        # 44 passed（Phase2 新增 10：装配/计数/溯源/fetched_at 注入/派生字段/排序/确定性/隔离/since/collect_all/限流）
# 手工看一次并发采集（crm_alpha）：
uv run python -c "import asyncio; from datetime import datetime; from config.settings import get_settings; from collectors import build_collectors, collect_competitor; \
exec('async def m():\n s=get_settings(); c=await collect_competitor(s, build_collectors(s), \"crm_alpha\", fetched_at=datetime(2026,8,31,9,0)); print({k: v.fetched for k,v in c.by_source.items()}, len(c.cards), c.failures)\nasyncio.run(m())')"
# → {'website': 2, 'news': 2, 'rss': 3} 7 []
```

## 6. 局限（本阶段主动承认）

- **仍全 mock**：真实 HTTP/RSS/搜索 adapter 未实现（无 key 无网络，离线可跑优先）。
- **重试退避未真实触发**：`max_retries`/超时已占位未启用——mock 永不失败，等真网络 adapter 接入时在 Worker 这层正式启用并加单测。
- **竞品间并发未做**：`collect_all` 逐竞品串行；跨竞品 fan-out 属 Phase 4 LangGraph 编排。
- **采集不落库**：FactCard 持久化与编排在 Phase 4 接入（本层纯"采 + 归 + 报"）。

---

_更新日志_：2026-09-03 建（Phase 2 交付）。
