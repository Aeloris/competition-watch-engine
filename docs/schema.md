# 数据模型与持久化（schema）

> Phase 1 交付：三类核心对象 **schema 定全**、JSON/SQLite **双后端可存可取**、mock 语料可切 **"两周两期"快照**
> —— 给 Phase 3 的 diff（跨期找变化）打好的留档地基。完整 ER 见设计稿 `README2.md` §3，本文是落地现状 + 决策说明。

---

## 1. 三层数据对象：证据 → 事实 → 记忆

```
RawItem(信源原文, Phase0)  →  FactCard(证据)  →  Event(事实)  →  Snapshot(记忆)
   抓到即得                      去重前归一化        Dedupe 合并后      某周期结束的留档
   kind/url/时间                  带 competitor_id    evidence_urls 聚合   event_fps 指纹集合
                                  source_url=溯源锚    佐证它的全部来源       下次 diff 的比对底
```

| 对象 | 语义 | 何时真正产出 | 落地 |
|---|---|---|---|
| `FactCard` | 一条可点回 URL 的原文（证据） | Researchers 归一化（Phase 2 起） | `memory/schemas.py`（schema 已定、已能由 corpus 从 fixture 生成） |
| `Event` | 同一真实事件多源报道合并的一条（事实） | Dedupe（Phase 3） | `memory/schemas.py`（schema 完备，本期不产出实例） |
| `Snapshot` | 某竞品某周期结束的指纹集合（记忆） | diff 引擎写档（Phase 3） | `memory/schemas.py` + 持久化本期可用 |

字段级标注（哪些由哪个 Phase 填）都写在 `memory/schemas.py` 的 docstring / 注释里，此处不再重复。

## 2. 数据对象 ER（记忆层落地版）

```mermaid
erDiagram
  FACT_CARD ||--o{ EVENT : merges_into
  EVENT }o--o{ FACT_CARD : evidenced_by
  COMPETITOR ||--o{ FACT_CARD : produces
  COMPETITOR ||--o{ SNAPSHOT : has
  SNAPSHOT ||--o{ EVENT : records

  FACT_CARD { str id "sha1(竞品,url,发布时)" }
  FACT_CARD { str competitor_id; str source_url "必填=溯源锚"; str title; str summary }
  FACT_CARD { datetime published_at; datetime fetched_at; str digest "sha1(url,归一标题)" }
  EVENT { str id; str competitor_id; str dimension; str kind "add/change/remove" }
  EVENT { str title; str summary; list evidence_urls ">=1"; str severity "Phase5"; date first_seen; date last_seen; str fp "Phase3"; datetime created_at }
  SNAPSHOT { str period "2026-W35"; str competitor_id; list event_fps; datetime created_at }
```

## 3. 事件指纹 fp：只做书写层归一（诚实口径）

`fp = sha1(竞品 | 维度 | normalize_title(标题))[:16]`。`normalize_title` 做的是**书写层**归一：
NFKC 统一全半角 → 转小写 → 只留字母/数字/汉字（去空白与全部标点）。

```python
# 同一句措辞的书写差异 → 同一 fp（单测锁死）
event_fingerprint("crm_alpha","feature","Alpha CRM v12.0 正式发布：智能跟进助手 GA")
# == "Alpha CRM v12.0正式发布:智能跟进助手GA"（全半角冒号/空白差异）等
```

**明确不承诺**：措辞完全不同却指同一事（官网 *"v12.0 正式发布"* vs changelog *"【发布】v12.0 智能跟进助手 GA"* vs 媒体 *"押注 AI 跟进助手"*）在 normalize 后**仍不同**——这是 Phase 3 Dedupe **语义聚类**的职责，不是本纯函数的职责。
fp 精确层负责"同句措辞跨期/跨源可对"，语义层负责"异句同事件可并"，两者是去重的两层（README2 §5.4）。

> Phase 3 diff 用的"规范化标题"取 **Event 合并后的规范标题**（语义聚类输出），所以 fp 是 diff 的锚不矛盾。

## 4. 存储抽象：JSON vs SQLite（一个接口，为什么两个都做）

- 接口 `MemoryStore`（`memory/store.py`）：`save_fact_cards / list_fact_cards`（按 竞品×期，**幂等覆盖**）
  + `save_snapshot / latest_snapshot`（给 diff 用）。上层只面对接口 → 换后端零改动 = **"存储可替换"工程证据**。
- `get_memory_store(settings)` 按 `config.pipeline.snapshot_store`（json|sqlite）分派，根目录 `data/memory/`（gitignored）；
  未知值启动即 raise（fail fast）。

| 后端 | 布局 | 取舍 |
|---|---|---|
| `JsonMemoryStore`（默认） | `data/memory/fact_cards/{竞品}/{period}.json`、`snapshots/...`，UTF-8 + `ensure_ascii=False` | 人可读、可 diff、无需引擎 → 离线回放调试友好 |
| `SqliteMemoryStore` | `data/memory/memory.db`，`fact_cards`/`snapshots` 两表，主键 `(竞品, period)`，`INSERT OR REPLACE` 幂等 | 标准库 `sqlite3`（零新依赖）、事务、可长大 |

两点工程细节：
- **写同一 (竞品, 期) 幂等覆盖** = 流水线重跑不产生重复存档（trace 计数链的前置条件）。
- **SQLite 连接用 `contextmanager` 保证 finally close**——早期版本只 `with conn`（只 commit 不 close），Windows 下 memory.db 句柄泄漏导致临时目录无法删除，测试的清理阶段暴露后修复。

## 5. Mock 语料周期表（回放底座，可复现）

fixtures 两竞品 × website/news/rss，时间戳 `2026-08-17..08-30`，按原文发布时间切 ISO 周：

| 竞品 | 2026-W34（08-17..08-23） | 2026-W35（08-24..08-30） | 合计 |
|---|---|---|---|
| crm_alpha | 2 | 5 | 7 |
| bi_beta | 2 | 5 | 7 |

> Phase 0 交付说明曾把 bi_beta 记为 W34×3/W35×4，逐条复核 fixture 后更正为 2/5——语料语义以本表 + `test_corpus.py` 断言为准。

`memory/corpus.py` 的 `build_period_cards(mock_dir, competitor_id, fetched_at)` 输出 `{period: [FactCard]}`，
期内按 (published_at, url) 升序；`fetched_at` 由调用方固定 → **同一语料永远切出同样的期与条数（回放确定性）**。
只读 fixtures、不写库；Phase 2 Researchers 接入真实信源后让位给 collector adapter。

## 6. 运行与验证

```bash
uv run pytest                        # 34 passed（Phase1 新增 26：schema 约束/fp 确定性+归一/store 双后端 roundtrip·幂等·latest/工厂 fail-fast/corpus 分区与确定性）
# 手工看语料分区：
uv run python -c "from datetime import datetime; from config.settings import get_settings; from memory import build_period_cards; \
print({p: len(c) for p, c in build_period_cards(get_settings().fixtures_path / 'sources', 'crm_alpha', datetime(2026,8,31,9,0)).items()})"
# → {'2026-W34': 2, '2026-W35': 5}
```

## 7. 局限（本阶段主动承认）

- **Event 只有 schema、无实例**：diff 引擎（Phase 3）才产出合并事件与快照内容，Snapshot 现在只有容器 + 持久化。
- **fp 只承诺书写层命中**：跨措辞语义合并（官网/changelog/媒体三源并一条）留待 Phase 3 Dedupe。
- **SQLite 按整期存 JSON blob**：够"回放 + 查最近快照"，未做单条查询/索引（真需要时拆行不迟）。
- **无并发写**：单进程幂等即可（任务卡边界）。

---

_更新日志_：2026-09-03 建（Phase 1 交付）。
