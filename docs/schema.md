# 数据模型与持久化（schema）

> Phase 1 交付三类对象 **schema 定全** + 双后端可存可取 + mock 语料切"两周两期"快照；
> Phase 3 让 `Event` 真正产出实例（Dedupe 合并），并给 `Snapshot` 加 `event_digests`（fp→内容摘要）承载 diff 的 change 判定。
> 完整 ER 见设计稿 `README2.md` §3，本文是落地现状 + 决策说明。

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
| `Event` | 同一真实事件多源报道合并的一条（事实） | Dedupe（Phase 3 起，`dedupe/merge.py` 产出实例） | `memory/schemas.py` + `dedupe/merge.py` |
| `Snapshot` | 某竞品某周期结束的指纹集合（记忆） | diff 引擎写档（Phase 3，`memory/diff.py` `build_snapshot`） | `memory/schemas.py` + 双后端持久化 |

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
  EVENT { str title; str summary; list evidence_urls ">=1"; str severity "Phase5"; date first_seen; date last_seen; str fp "Phase3 定锚"; datetime created_at }
  SNAPSHOT { str period "2026-W35"; str competitor_id; list event_fps; dict event_digests "Phase3 fp->内容摘要"; datetime created_at }
```

## 3. 事件指纹 fp：书写层归一为底，去重聚类把 fp 定锚（诚实口径）

`fp = sha1(竞品 | 维度 | normalize_title(标题))[:16]`。`normalize_title` 做的是**书写层**归一：
NFKC 统一全半角 → 转小写 → 只留字母/数字/汉字（去空白与全部标点）。

```python
# 同一句措辞的书写差异 → 同一 fp（单测锁死）
event_fingerprint("crm_alpha","feature","Alpha CRM v12.0 正式发布：智能跟进助手 GA")
# == "Alpha CRM v12.0正式发布:智能跟进助手GA"（全半角冒号/空白差异）等
```

**明确不承诺**：措辞完全不同却指同一事（官网 *"v12.0 正式发布"* vs changelog *"【发布】v12.0 智能跟进助手 GA"* vs 媒体 *"押注 AI 跟进助手"*）在 normalize 后**仍不同**。

**Phase 3 怎么把 fp 变成"事件身份"**（去重两层落地版，README2 §5.4）：
- **第一层 语义聚类**（把异句同事件并一条）= `dedupe/merge.py` 的**版本锚 + 近原文**聚类：发布/上线类事件靠
  版本锚（v12.0 / 3.0 / v12.1，`\d+(\.\d+)+`）跨措辞认亲（fixtures 官网/changelog/媒体三源并一、`evidence_urls=3`）；
  无版本锚的卡只在近原文（别名剔除后相同/几乎包含）时才并。
- **第二层 fp 定锚**（并完给规范事件上户口）= 事件取**最早发布卡**的标题当 canonical title，`event_fingerprint`
  在**规范标题 + 已冻结的 dimension** 上算 → fp 首次去重即定锚、**之后不再变**。
- **为什么 dimension 必须先定**：dimension 混进 fp，Phase 5 若再分类就会改 fp、把一件事 diff 成"消失+新增"，
  所以 Phase 3 用**确定性规则分类**（`dedupe/classify.py`）先冻结；Phase 5 Analyst 的改良只改 `dimension` 注解、不改 fp。

> Phase 3 diff 用的"规范化标题"取 **Event 合并后的规范标题**（第一层聚类输出），所以 fp 是 diff 的锚不矛盾。
> 语义聚类只做到"版本锚/近原文"，embedding 级语义近并（Qdrant）如实留到 Phase 4——本节口径与 docs/memory.md §4 一致。

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

## 5. 跨期 diff 的数据视角：fp 认人、digest 认内容（Phase 3）

一期结束后把 Event 集固化进 `Snapshot`：`event_fps`（这一期有哪些事件）+ `event_digests`（fp → 内容摘要）。
下期回来先比对快照再决定要不要重新计算/下发：

- **内容摘要 `event_content_digest`** = `sha1(证据 url 排序后按 \x1f 拼 | \x1e | 压白后的摘要)`。fp 管"是不是同一个身份"，
  digest 管"内容变没变"——同 fp 两期都在但摘要变 → `change`，没变 → `unchanged`（**零重复计算**，省 token 的落点）。
- **每 fp 判一次**：本期有/上期无 → `add`；两期都有、digest 变 → `change`；不变 → `unchanged`；消失只来自**显式撤回**
  （`mark_removed` 校验 fp 确实在上期，不在则 fail fast）——上期有、本期没报道 ≠ 消失，绝不自动推断。
- 为什么老字段不够：只看 fp 集合只能判 add/remove/skip，判不了"同一事件这周更新了证据/摘要"——所以 Phase 3 给 Snapshot 加
  `event_digests`（向后兼容：旧快照无此字段按全 add 处理，双后端 round-trip 有测试锁死）。

比对实现在 `memory/diff.py`（`build_snapshot` / `diff_event_sets` / `mark_removed` / `ChangeSet.summary()`），
"消失语义"与"措辞变了再报道=新增"等诚实边界见 `docs/memory.md` §3/§4。

## 6. Mock 语料周期表（回放底座，可复现）

fixtures 两竞品 × website/news/rss，时间戳 `2026-08-17..08-30`，按原文发布时间切 ISO 周：

| 竞品 | 2026-W34（08-17..08-23） | 2026-W35（08-24..08-30） | 合计 |
|---|---|---|---|
| crm_alpha | 2 | 5 | 7 |
| bi_beta | 2 | 5 | 7 |

> Phase 0 交付说明曾把 bi_beta 记为 W34×3/W35×4，逐条复核 fixture 后更正为 2/5——语料语义以本表 + `test_corpus.py` 断言为准。

`memory/corpus.py` 的 `build_period_cards(mock_dir, competitor_id, fetched_at)` 输出 `{period: [FactCard]}`，
期内按 (published_at, url) 升序；`fetched_at` 由调用方固定 → **同一语料永远切出同样的期与条数（回放确定性）**。
只读 fixtures、不写库；Phase 2 Researchers 接入真实信源后让位给 collector adapter。

## 7. 运行与验证

```bash
uv run pytest                        # 67 passed（Phase1 26 → 之后 schema/fingerprint/store/corpus/collectors/dedupe/diff 累计）
# 手工看语料分区：
uv run python -c "from datetime import datetime; from config.settings import get_settings; from memory import build_period_cards; \
print({p: len(c) for p, c in build_period_cards(get_settings().fixtures_path / 'sources', 'crm_alpha', datetime(2026,8,31,9,0)).items()})"
# → {'2026-W34': 2, '2026-W35': 5}
# 跨期 diff 一瞥（crm_alpha W34 快照 vs W35 事件）：见 docs/memory.md §6 → dedupe W35 5->3、diff adds=3
```

## 8. 局限（本阶段主动承认）

- **语义近并只到版本锚/近原文**：embedding 级"异词异义同事件"（Phase 4 Qdrant）与跨期措辞变体语义同一性未做——
  措辞变了再报道按新增处理（docs/memory.md §4 边界表）。
- **change 只靠 digest 判**：证据与摘要都没变、但"含义实质变"判不出（属 Phase 6 Reviewer）；纯排版/证据序变化不误报。
- **Event 无独立持久化实体**：本期以"Snapshot(fp+digest) + 进程内 Event"承载，Phase 4 编排接入时再落 Event 存储。
- **SQLite 按整期存 JSON blob**：够"回放 + 查最近快照"，未做单条查询/索引（真需要时拆行不迟）。
- **无并发写**：单进程幂等即可（任务卡边界）。

---

_更新日志_：2026-09-03 建（Phase 1 交付）；2026-09-03 更新（Phase 3：Event 产出实例、Snapshot.event_digests、新增 §5 diff 数据视角、口径与局限刷新）。
