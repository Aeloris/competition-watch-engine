# competition-watch-agent（竞观 Agent）

把散落官网 / 新闻 / RSS 的竞品信号，加工成**带出处、先审后发、能回答"本周变了什么"**的多 Agent 情报流水线（竞争情报 / 竞品观测）。

> 完整设计稿（数据对象、编排状态机、目标指标、阶段路线）见父级 `D:\develop\MyProject\README2.md`；
> 落地现状见 [`docs/architecture.md`](docs/architecture.md)。

## 当前进度：Phase 3（去重合并 Dedupe + 跨期 diff Memory/Diff）

沿革：Phase 0 = 最小服务骨架 + Mock 底座；Phase 1 = 数据模型/持久化/指纹；Phase 2 = 采集层（并发、隔离、可数）；
Phase 3 = **把散落的证据卡并成"事件"、再和上期记忆比出"本周变了什么"**——项目灵魂"跨期找变化"落地：

- **确定性维度分类** `dedupe/classify.py`：发布/调价/行业/组织/合规 规则关键词 → `Dimension`，**fp 首次去重前就冻结**
  （维度混进 fp，Phase 5 再分类会改 fp、把一件事 diff 成"消失+新增"，所以先用规则占位）。
- **去重合并** `dedupe/merge.py`：发布/上线类事件按**版本锚**（v12.0 / 3.0 / v12.1）跨措辞认亲，无锚卡只并近原文重复；
  并完取最早发布卡标题当 canonical title、`event_fingerprint` 定锚，`evidence_urls` 聚合 = **同一件事带全部佐证出处**。
  fixtures 真值：crm W35 5 卡→3 事件（v12.0 官网/changelog/媒体三源并入一、证据=3），bi W35 5 卡→4 事件（图表库 3.0 双源并入一）。
- **跨期 diff** `memory/diff.py`：上期快照 vs 本期事件，**每 fp 判一次** → add（新增）/ change（同 fp 内容摘要变了）/
  unchanged（同 fp 同摘要 → **零重复计算**）/ remove（**只来自显式撤回**，上期有但本期没报道 ≠ 消失，绝不自动推断）。
  为此 Snapshot 扩展 `event_digests`（fp → 内容摘要 = 证据 url 排序 + 压白摘要的 sha1），双后端向后兼容。
- **快照写档**：`build_snapshot` 把每期 Event 集固化（fps + digests），JSON/SQLite 幂等覆盖，下期 diff 的比对底。
- **诚实边界**：语义近并只到版本锚/近原文（embedding + Qdrant + 跨期措辞语义同一性如实留 Phase 4）；措辞变了再报道 = 本期新增。
- **trace 计数链**：采集 N（Phase 2）→ 去重剩 M（`DedupeSummary`）→ diff 出 K（`DiffSummary`）三段齐了。

```bash
uv sync
uv run pytest            # 全绿（67）
uv run uvicorn app.main:app --port 8000
# http://127.0.0.1:8000/health
```

## 目录

```
app/    FastAPI 入口        config/  强类型配置(fail-fast)
llm/    Provider 抽象       sources/ 信源适配器 + MockSource
memory/ 数据对象+持久化+指纹+跨期diff   collectors/ 采集层：并发/归一化/失败隔离
dedupe/ 维度规则分类 + 版本锚去重合并     fixtures/ 离线回放底座
docs/   schema.md 数据模型 · collectors.md 采集层 · memory.md 去重+跨期diff · architecture.md 骨架现状
tests/  冒烟 / mock / 数据层 / 采集层 / 去重 / diff
```

业务包 `orchestration/ analyst/ writer/ reviewer/ reporter/ eval/` 现为占位类，按 Phase 计划落地；memory / collectors / dedupe / diff 已落地（见上）。
