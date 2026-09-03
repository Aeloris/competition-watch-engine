# Eval-Harness 回放式评测（Phase 8，README2 §5.11 + §7.4 row 8）

> Phase 8 交付：给"先审后发流水线"加一套**可重复执行的离线评测 + 自动门禁**——
> 用**固定语料回放**量化"本周变了什么"产出的质量，而不是嘴上说"应该不错"。
> 门禁失败 `uv run python -m eval.run` 退出码 1，可当 CI 守卫。
> 六指标来自 README2 §5.11 目标指标表的**确定性可测子集**；评测代码在 `eval/`，
> 门禁阈值在 `config/config.yaml` 的 `eval:` 块。

**先读口径声明（不然数字会误导）**：这是**固定语料回归基线（regression oracle）**，
不是"真实世界泛化能力"的宣称——它回答的是"我们造的场景里，流水线有没有稳定做到
我们设计它该做的"，不是"真实网络/真实竞品下能打几分"。真实网络从未接入（全程 Mock），
所以**任何数字都不该被说成真实世界准确率**。能做到"同输入必同输出"靠的是三个诚实设计：

1. **真值先于引擎独立存在**：gold 事件集是从 `fixtures/sources/*.json` 的**原文**人工标出来的
   （锚词 = fixture 标题里的原词），不是从引擎输出反推的——gold 是裁判，不是流水线的回声。
2. **对抗注入用的是"真锚 + 一个假零件"**：编造条目复用真实 fp/URL/维度/severity 作锚，
   只换一处造假，保证每条恰好触发一个信任漏洞码、且被门卫拦下 = 是"那一个零件"被识破，
   不是碰巧别的问题拦的。
3. **真实回放 = 冻结语料**：W34/W35 fixtures 不再改动；测试锁死每个环节的规范数字，
   任何回归会立刻红。

## 1. 三个评测面（README2 §5.11 的事件集回放拆法）

| 面 | 干什么 | 回答的问题 | 不许宣称的 |
|---|---|---|---|
| **回归一致性** | 冻结真实 fixtures W34/W35，跑完整 pipeline（图级，含门卫） | 本期结果与已锁死的规范数字是否仍一致（trace 5→3→3 / 5→4→4、radar、verdict、gate 全 PASS） | "真实世界情报质量" |
| **gold 真值集** | 对 W35 合并后的事件集与手标 gold 事件对账 | 该并的是不是都并了、该报的是不是都报了、每条的来源是不是真采集到的 | 未标 gold 的场景表现 |
| **对抗注入** | 把"编造 URL / 凭空 fp / 无证据 claim / 反义矛盾 / 旧闻重发 / 混合批量"喂给流水线 | 门卫是不是每种造假都拦下、且落的是**那一个** code | 覆盖所有真实造假形态 |

## 2. 六项指标（eval/metrics.py，都是确定性纯函数）

| 指标 | 计算 | 验收阈值（config eval:） |
|---|---|---|
| **事件一致率** | gold 7 事件 × 引擎 W35 合并事件贪心匹配（同 competitor + 同维度 + 高相似标题）；`match_gold` 返回 `(matched, total)` | —— 回放须 `7/7` |
| **去重准确率** | gold 的跨源重复组全部并成一条 Event（`dedupe_groups_ok`）且 W35 事件数 == 预期（`event_count_matches`：crm 3、bi 4） | —— 回放须全过 |
| **来源 / grounding 准确率** | 周报每条 item 的 `sources[].url` 都在该 run 本期真采集到的 card URLs 集合里（`grounded_fraction` → `(grounded,total)`） | `precision_threshold: 1.0`（每条出处都真采集到） |
| **结构合规率** | 周报 draft 自洽：headline/雷达/kind/sections/items/sources 计数一致（`structure_pass`，防"看起来能发但结构烂"） | `structure_threshold: 1.0` |
| **trace 计数链一致性** | 每 run `trace{collected,merged,diff}` == 实际中间产物数（`trace_consistent`，防御性回归：计数链撒谎 = 观测坏了） | `require_trace_consistent: true` |
| **编造拦截率** | 对抗场景中"被门卫拦下 + 收件箱出现预期 code"的比例（`detection_rate = caught/total`）；真实语料误拦须为 0 | `detection_threshold: 1.0`（6 场景全拦） |

真实回放还顺带出 **误拦哨兵**：真实 W35 两竞品全 PASS、收件箱 0 —— 若某天真语料也开始
"转人工"，评测会红，提醒"门卫变严过头了"，而不是悄悄多拦。

## 3. 对抗场景表（eval/adversarial.py · ADVERSARIAL_CASES）

图级 = 经真实 `run_cycle` 全链路（毒化 writer：真写条目 + 掺假）；`duplicate` 因真实 W35/W34
话题不重叠、unchanged_fps 为空而走**门卫级**判定路径直接复现（见下）。

| 场景 | 注入的假零件 | 预期被拦 code | 级别 |
|---|---|---|---|
| `unsourced_url` | 真 fp + **编造 URL** | `UNSOURCED_URL` | 图级 |
| `ungrounded_fp` | **凭空 fp** + 真 URL | `UNGROUNDED_FP` | 图级 |
| `ungrounded_claim` | 理由命中词不在正文（**张冠李戴**） | `UNGROUNDED_CLAIM` | 图级 |
| `contradiction` | **反义极性矛盾**（同轴反词 + 共享主体锚） | `CONTRADICTION` | 图级 |
| `duplicate` | **旧闻重发**：老事件 fp（真 W34 存在过）当本期发出 | `DUPLICATE` | 门卫级 |
| `mixed_batch` | 一批条目里掺**两种**造假如前两行 | `UNSOURCED_URL` + `CONTRADICTION` | 图级 |

设计意图：前五个各验"一个信任漏洞码被精准识别"（single-code purity：expected 只有那一个），
`mixed_batch` 验"一单里多个问题都暴露，不互相吞掉"。真实 fp/URL 作锚 → 拦下 ≠ 误伤真事件。

## 4. 图级 vs 门卫级（诚实口径）

- **图级**：毒化 writer → 真实 `run_cycle`（LangGraph 全链路含 reviewer_node 路由）→ 断言终态
  gate_trace / human_inbox 出现预期 code。这验证"整条流水线在端点堵住了假货"。
- **门卫级（只有 `duplicate`）**：真实 W35 与 W34 话题不重叠 → `unchanged_fps` 为空 →
  DUPLICATE 判定路径在真实链路上天然不可达；于是把**真实老事件**当本期条目直接喂
  reviewer_node（unchanged_fps=[老 fp]），验证的是"同一判定逻辑正确反应"，不是"图会拦它"。
  写进 docs 是防止把"门卫级复现"说成"图级拦截 6/6"——**图级实际是 5 种 + 1 混合 = 5 图级 1 门卫级**。

## 5. 实测口径（2026-09-04 跑通，非编造，测试锁死）

- 回归一致性：crm W35 `5→3→3` radar{high1,med1,low1}、bi W35 `5→4→4` radar{high1,med1,low2}，
  trace 计数链一致，verdict 全 PASS、收件箱 0（**真实语料 0 误拦**）。
- gold 真值集：gold 7 事件 → 引擎全对出 = **7/7（事件一致率 1.0）**；去重重复组全并、W35 事件数
  与 gold 预期一致（crm 3 / bi 4）；周报每条 sources 都在本期采集卡内 = **来源/grounding 7/7（1.0）**；
  两竞品 draft 结构自检全过 = **结构合规 4/4 run 口径下 1.0**。
- 对抗注入：6/6 场景全拦，`gate_codes == expected`（单码纯、mixed 双码都在），**编造拦截率 1.0**。
- gold 独立性体检：`gold_health_check()` 空（锚词在 fixture 标题、组 URL ⊆ fixture URL、每事件有
  来源 URL）——gold 没偷看引擎输出。

## 6. 局限与边界（别把回归基线说成生产评测）

- **未标 more gold / 未见真实造假分布**：7 事件 gold + 6 对抗场景是"我们编得动的口径"；
  真实世界命中率无从谈起（没接真实网络）。对抗只覆盖造得出锚的确定性漏洞，不覆盖
  LLM 语义级编造（provider-gated，本期未接）。
- **`duplicate` 是门卫级**：图级不可达路径诚实降级为判定逻辑复现，不冒充图级拦截。
- **评测对象是当前 fixtures 冻结语料**：fixtures 一改，规范数字要一起更新（有测试会红提示），
  这正是回归基线的用法，不是缺陷。
- **threshold 全 1.0** = "这些固定场景必须全过"，不是"真实世界必须完美"；别把门禁当成泛化保证。

## 7. 运行与验证

```bash
uv sync
uv run pytest tests/test_eval.py          # 评测单测（15 条：纯函数手算 + gold 独立性 + 真实回放规范数字 + 对抗 6/6 + 门禁）
uv run pytest                             # 全绿（Phase 8 = 165）
uv run python -m eval.run                 # 跑全量评测 → data/eval/eval_report.{json,md}；门禁失败退出码 1
cat data/eval/eval_report.md              # 人读版：三面六指标 + 对抗明细 + 门禁结论
# 想看门禁怎么"失败"：把 config.yaml eval.detection_threshold 改成 >1.0 再跑 eval.run（会红，看完改回 1.0）
```

报告 JSON 结构：`{meta, regression:{runs[]}, gold:{...}, adversarial:{cases[]}, metrics, gates}`；
`metrics` 六个标量/元组给 `check_gates` 判门禁。门禁消息前缀：编造拦截率 / 来源 grounding 准确率 /
trace 计数链一致性 / 真实语料误拦 / gold 标注不健康 —— 哪条失败一目了然。

---

_更新日志_：2026-09-04 建（Phase 8 交付：eval/{dataset,adversarial,metrics,harness,run}.py + config eval 块 +
tests/test_eval.py 15 条；三面六指标 + 对抗 6 场景表 + 图级/门卫级口径 + 实测数字 165 全绿）。
