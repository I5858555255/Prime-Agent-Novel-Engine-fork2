# 质量层 M2 路线图（v0 之后的增量）

* **状态:** 划定（scoped），未实现
* **基线:** v0 已落地（PR #28，commit 5dbbee3/b127972）+ TS 构建解锁（PR #29）
* **单源真值 spec:** `docs/superpowers/quality-layer-v0-design.md`（M2 占位列表 = 本路线图的上集）
* **日期:** 2026-11-26

v0 是可发布单元：批评者裁决（有界、哈希链叶）+ 确定性卷内旋钮 + CLI 控制面（`qc`/`knobs`/`note-quality`/`status`）+ 未校准批评者 + 预算守卫。本文件划定 v0 刻意推迟到 M2 的全部项，按「价值/风险/依赖」排序，每项给出落点 + 前置条件 + 验收。

---

## 排序总览（建议实施顺序）

| # | M2 项 | 解锁价值 | 前置依赖 | 风险 | 建议批次 |
|---|---|---|---|---|---|
| M2-1 | 修复循环（per-chapter repair + re-judge） | 让「低于旋钮」的章节自动重写→复评，真正兑现 ceiling | G7 校准（M2-7）证明 judge 分数与参考相关 | 高（LLM 意见触发重写可能劣化正文） | 校准后 |
| M2-2 | 旋钮规则引擎 + LLM climax 分类 + `volume_reopen` | 卷内旋钮不再是固定 per-stage，可按主题/climax 自适应 | v0 `select_knobs` 已是 thin override point | 中（需版本化 authority，与 `concept_terms` 并列） | 早 |
| M2-3 | `commit_override` 动词 | 算子「人工转向」变成链上提交（同一哈希链账本同时含自主 + 人工决策） | v0 已有 `knob_overrides` 暂存表 + `record_override` plumbing | 低（纯加法，不重铸链） | 早 |
| M2-4 | `quality-report`/dashboard（三分类聚合） | 「84% viral-on-target / 12% shipped-flat / 4% shipped-with-defect」 | 需 `quality_report` 表（v0 未建） | 中 | 中 |
| M2-5 | 提示缓存（bible + volume state + 上章摘要） | 降低稳态 token 成本 | 无成本节省声明（需实测） | 中（语义缓存一致性） | 中 |
| M2-6 | 漂移 + 报警（rolling drift metrics + paging channel） | 无人值守运行时批评者漂移能在卷边界前被发现/报警 | CUJ-3 依赖 OQ2 的 gold set | 中 | 与 M2-7 联动 |
| M2-7 | `judge_calibration` | 把未校准批评者变成可信的「计量器」 | OQ2（~200 章人工 gold set）+ 校准 rubric | 高（无 gold set 不可做） | 依赖 OQ2 |
| M2-8 | retention 摄取（`retention.py` 模块） | 读者留存 = viral 信号的地面真值（judge 的校准目标） | OQ1（交付/读者分析 feed） | 高（无 feed 不可做） | 依赖 OQ1 |
| M2-9 | `what-if` 动词 | 预览「若 爽点=4」的分数（不改链/不写 telemetry） | 批评者 + 旋钮 | 低 | 中 |
| M2-10 | `migration_ledger` 表 | 取代 v0 的 `schema_version` 标记行，做正式迁移账本 | v0 的 `chapters.schema_version` + `quality_schema_version` 标记 | 低 | 早（与 M2-3 一起） |
| M2-11 | `status` ¥/重试/ETA | 算子看成本与剩余 | 定价数据 + 速率历史 | 低 | 晚（纯读面） |

---

## 逐项详情

### M2-1 修复循环（repair + re-judge）
- **现状（v0）:** 一次批评者调用/chapter（解析/超时重试 1 次，≤2 总调用），无修复。「低于旋钮」= 非阻断 `quality_note`（R21/R28：ceiling 是软目标，不阻断）。
- **M2 目标:** 低于旋钮维度 → 触发一次**最小修复重写**（复用 `expand_run` 的 repair 模板族）→ 复评 → 若达标替换 leaf（`re_verify` 写 version N+1），若连续 2 轮未达标则保留 flat + `quality_note` 盖章。
- **前置:** G7（M2-7）证明 judge 分数与参考（gold set / 留存）相关，否则修复循环是「盲改」。
- **落点:** `expand_run.py`（修复轮）+ 新 `repair_loop.py`（状态机）+ `quality_store.re_verify`（版本化）。
- **验收:** 合成夹具下：低于旋钮章 → 修复后达标 → leaf version+1 且 prose_sha256 重绑；连续未达标 → 保持 flat，不无限重试（有界）。

### M2-2 旋钮规则引擎 + LLM climax 分类 + `volume_reopen`
- **现状（v0）:** `select_knobs` 是固定 per-stage（无 LLM）；`read_active_knobs` 取 volume 末章 stage；`select_knobs_for_volume` 已按 regime 发射多套。
- **M2 目标:** 规则引擎读 bible 字段 + 卷主题 + `climax_chapter` 决定旋钮（替代固定 per-stage）；LLM climax 分类是**新增、独立、版本化的 authority**（`rationale.llm_classify_ver`），与 `concept_terms` 并列而非替代；`volume_reopen` 产出新行版本（不重铸）。
- **落点:** 新 `knob_rules.py`（引擎，是 v0 `select_knobs` 的 thin override point）+ `concept_terms` 并列的分类器。
- **验收:** 同一卷+同一规则版本 → 同旋钮（可复现）；跨 regime 边界 → 每 regime 一套；`volume_reopen` 不改动已提交章。

### M2-3 `commit_override` 动词
- **现状（v0）:** `knob_overrides` 暂存表 + `record_override` 已落地（未链化）；`runner` 无 `commit-override` 动词（G4 承诺：v0 只出读/转向面，写路径 M2）。
- **M2 目标:** `commit_override` 把 `record_override` 暂存行折叠成**新的链上提交**（哈希链账本同时含自主 + 人工决策）；下游章用被覆盖的旋钮。
- **落点:** `runner.py` 新动词 + `quality_store` 一条 `commit_override`（把暂存行 → 新 leaf/commit，父链完整）。
- **安全注（来自 v0 安全审计）:** `record_override` 的 `rationale` 是自由文本（v0 仅在 un-chained 暂存表）。折链时**必须**加 `dim ∈ {爽点,钩子,压抑}` 枚举校验 + `rationale` 长度上限，避免把未校验 operator 文本写入哈希链。
- **验收:** 人工覆盖 → 账本多一条链提交，`validate_quality_chain` 仍干净；暂存行被消费后不留悬挂；折链前有 dim 枚举 + rationale 长度校验。

### M2-4 `quality-report`/dashboard
- **现状（v0）:** 读面是 `qc`/`status`（读 `quality_verdicts` + `quality_notes` + `manifest.quality_note`）；三分类聚合是 M2（需 `quality_report` 表，v0 未建）。
- **M2 目标:** `quality_report` 表 + `quality-report` 动词 → 「84/12/4」三分类（viral-on-target / shipped-flat / shipped-with-defect）。
- **落点:** 新表 + `runner` 动词 + 聚合 SQL。
- **验收:** 三分类之和 = 已评分章数；与 `qc` 读数一致。

### M2-5 提示缓存
- **现状（v0）:** 无；spec 明确「无节省声明」（M2 才做，且需实测）。
- **M2 目标:** 语义缓存 bible + 卷 state + 上章摘要（prompt 侧），降低稳态 token。
- **落点:** 扩写/批评 prompt 的缓存键 + 一致性校验。
- **验收:** 缓存命中率 + 稳态 token 下降可测量（不预设 50–70% 那种未证数字）。

### M2-6 漂移 + 报警
- **现状（v0）:** `telemetry` 表已有（drift/budget only），无 rolling drift metrics + paging。
- **M2 目标:** rolling 漂移指标（judge–human 一致性）+ 报警通道（卷边界前 halt/page）。
- **前置:** OQ4（报警通道形态：log / metrics 文件 / 外部端点）+ OQ2（gold set）。
- **落点:** 新 `drift.py`（rolling 指标）+ 通道。
- **验收:** 一致性跌破阈值 → 哈希链上 suspect 事件（绑到旋钮集/prompt 版本）+ 报警。

### M2-7 `judge_calibration`
- **现状（v0）:** 批评者**未校准**（G7），只出 schema + stub。
- **前置:** OQ2（~200 章人工 gold set + rubric；CUJ-3 在 OQ2 落地前不可达）。
- **M2 目标:** 对 gold set 抽样评分 → 一致性度量；一致性下降 = 哈希链上 suspect 事件（旋钮集/prompt 版本）。
- **落点:** 新 `judge_calibration.py`。
- **验收:** 一致性与阈值可比；低一致 → 触发 M2-6 报警 + 阻塞 M2-1 修复循环上线。

### M2-8 retention 摄取
- **现状（v0）:** `retention` DDL 表已建（schema only），无 `retention.py` 模块。
- **前置:** OQ1（交付/读者分析 feed；无 feed 则只能 schema + stub）。
- **M2 目标:** `retention.py` 摄取每章 read-through/reread/abandon-rate（viral 信号地面真值）；judge 校准目标。
- **落点:** 新 `retention.py`（OQ1 落地前是 no-op ingest）。
- **验收:** 有 feed 后每章留入 retention 表 + 与 judge 分数可联合分析。

### M2-9 `what-if` 动词
- **现状（v0）:** 无；OQ5 默认 M2。
- **M2 目标:** `what-if 爽点=4` → 只重评一次（无修复、不写链/telemetry），打印分数预览。
- **前置:** 批评者 + 旋钮（v0 已有）。
- **落点:** `runner` 新动词 + 一次性 critic 调用。
- **验收:** 无链写、无 telemetry 写；分数预览可读。

### M2-10 `migration_ledger` 表
- **现状（v0）:** 用 `chapters.schema_version` + `quality_schema_version` 标记行做迁移（NG2 纯增量，不重铸链）；`migration_ledger` 表是 M2。
- **M2 目标:** 正式迁移账本（版本化迁移记录，取代标记行）。
- **落点:** 新表 + 迁移脚本。
- **验收:** 双人群（compliance-only vs dual-signal）可被正式账本切分。

### M2-11 `status` ¥/重试/ETA
- **现状（v0）:** `status` 已有 tokens/calls/wall + `verdict_pass_rate`/`verdict_error_rate`/`quality_budget`；¥/重试/ETA 是 M2。
- **M2 目标:** 每章成本（¥）、剩余重试预算、ETA（按速率历史）。
- **落点:** `runner.status()` 读 pricing（`llm_providers.json:pricing`）+ 速率窗口。
- **验收:** 读数与 usage 表 + 定价一致；ETA 随窗口更新。

---

## 依赖图

```
OQ1 (retention feed) ──► M2-8 retention 摄取 ──► M2-7 judge_calibration ──► M2-1 修复循环
OQ2 (gold set)     ──► M2-7 judge_calibration ──► M2-6 漂移+报警 ──────────────┘
v0 暂存表 + record_override ──► M2-3 commit_override（早）
v0 select_knobs override point ──► M2-2 旋钮规则引擎（早）
v0 schema_version 标记 ──► M2-10 migration_ledger（早）
无前置（纯读面/预览）──► M2-4 quality-report（中）/ M2-9 what-if（中）/ M2-5 提示缓存（中）/ M2-11 status ¥（晚）
```

**可独立先做（无阻塞依赖）:** M2-2、M2-3、M2-10（早批次，纯加法/薄覆盖点）。
**阻塞在外部 feed/gold set:** M2-7、M2-8、M2-6（OQ1/OQ2 未答前不可做，OQ 默认 deferred）。
**需校准先行:** M2-1 修复循环（G7 证明分数与参考相关后，避免「盲改」劣化正文）。

---

## 与 spec 一致性

本路线图的「Milestone-2 placeholders」清单与 `design_draft.md` §"Milestone-2 placeholders" 一一对应（repair loop / 旋钮规则引擎 / 提示缓存 / 漂移+pager / judge_calibration / retention / quality-report·dashboard / commit_override / what-if / migration_ledger / status ¥·retries·ETA）。每项在 v0 都是**刻意缺席**（spec 已标注 M2），本文件补齐「何时做、依赖谁、怎么验收」。

---

## 安全审计结论（v0 落地代码，2026-11-26）

v0 质量层代码（commit 5dbbee3 + b127972）通过 7 合同安全审计，**结论 CLEAN，无 [阻断]/[严重] 风险**。spec 两条核心安全 NFR 均成立：

- **「no secrets in verdict records」**✓ — `quality_verdicts` / `telemetry` / `knob_overrides` / `manifest.quality_note` / `verification.verdict_record_hash` / `failure.json` / `status.json` 逐字段核对，无 api key 值、无 PII、无 prompt 原文、无 raw LLM body。`rationale`（≤80 字截断）是批评者自述，非 prose 回显。
- **「trace_id opaque, not PII」**✓ — `trace_id = uuid.uuid4().hex`（32 字符随机十六进制）。
- 其余 5 合同（api_key_env 仅用于 Authorization header、prompt 注入隔离、路径安全、LLM 调用有界、哈希链完整性作为安全属性、config 在可信位置）全部 [通过]。

**带入 M2 的安全注：**（1）`commit_override`（M2-3）折链时需加 `dim ∈ {爽点,钩子,压抑}` 枚举校验 + `rationale` 长度上限；（2）critic 的 `rationale` 允许引用 prose 原句（≤80 字），若正文含敏感数据会携带其 80 字子串——属小说正文数据而非系统 secret，可接受（记录在案）。

