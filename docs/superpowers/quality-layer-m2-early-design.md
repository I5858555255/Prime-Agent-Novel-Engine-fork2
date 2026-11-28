# 质量层 M2 早批次 — 设计（commit_override 动词 + 旋钮规则引擎 + migration_ledger）

* **作者:** juliandbarnard
* **基线:** v0 已落地（main `d699ca7`），v0 的 `knob_overrides` 暂存表 + `record_override` + `select_knobs`/`select_knobs_for_volume` + `chapters.schema_version`/`quality_schema_version` 标记行已存在。
* **范围:** M2 路线图（`quality-layer-m2-roadmap.md`）里的「早批次」三项 —— 无外部依赖（不阻塞在 OQ1/OQ2）、纯加法、不重铸链。
* **纪律:** EGM TDD（红-绿-重构，合成 fixture + 桩，提交前全量门禁；禁止 staticmethod(lambda)）。

---

## 0. 三项与 v0 的衔接（must-exist-before）

| M2 项 | 依赖的 v0 件 | 新增 |
|---|---|---|
| M2-3 `commit_override` 动词 | `knob_overrides` 表 + `record_override`（v0 已暂存） | `quality_store.commit_override(db, store, chapter, dim, new_value, rationale)` + `runner` 新动词 `commit-override` |
| M2-2 旋钮规则引擎 | `select_knobs`（v0 thin override point）+ `select_knobs_for_volume`（regime 发射） | `knob_rules.py`：规则引擎（bible 字段 + 卷主题 + climax_chapter → 旋钮），版本化 `rationale.rules_ver`；`select_knobs` 委托给它 |
| M2-10 `migration_ledger` 表 | `chapters.schema_version` + `quality_schema_version` 标记行（v0 替代物） | `migration_ledger` DDL + `record_migration` + `validate_migrations`；`Store` 开库时回填既有 schema_version 标记到 ledger |

**不变量（与 v0 一致）:** 全部纯加法；`chapters`/`quality_verdicts` 现有行哈希不变；`invalidate_from` 同步处理新表；`enable_quality=false` 项目不受影响（新表都在门控块里）。

---

## 1. M2-3 `commit_override` 动词（哈希链人工转向）

### 目标
把算子经 `record_override` 暂存的旋钮覆盖，**折叠成一条新的链上提交**（`knob_overrides` 行 → 消费 + 生成一个 `knob_commit` 叶，父链完整），使「自主决策 + 人工决策」共存于同一哈希链账本（G4）。v0 只有暂存（un-chained），写路径是 M2。

### 数据契约
```
-- knob_overrides 增 1 列（v0 表，加法 ALTER）
ALTER TABLE knob_overrides ADD COLUMN committed_at TEXT;   -- 折链时间戳；NULL = 仍暂存

-- 新表：折链叶（un-chained staging 的链上对应物）
CREATE TABLE knob_commits(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  chapter INTEGER NOT NULL, dim TEXT NOT NULL, new_value INTEGER NOT NULL,
  rationale TEXT, override_hash TEXT NOT NULL,
  knob_commit_hash TEXT NOT NULL,   -- digest({chapter,dim,new_value,rationale,override_hash, parent_hash, trace_id})
  parent_hash TEXT NOT NULL,         -- 该 chapter 的 chapters.hash（或 GENESIS）
  trace_id TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX idx_kc_chapter ON knob_commits(chapter, id DESC);
```

### API（`quality_store.py`）
```
def commit_override(db, store, chapter, dim, new_value, rationale="") -> str:
    """把 knob_overrides 暂存行折链。
    - 校验 dim ∈ {爽点,钩子,压抑}、new_value ∈ 1..5（安全注：M2-3 的安全注记）。
    - 要求该 (chapter, dim) 的暂存行存在（先 record_override）；没有则 ValueError。
    - 写一条 knob_commits 叶（父链 = chapters.hash，GENESIS if missing）。
    - 把暂存行标 committed_at（消费，避免重复折链）。
    - 幂等：同 override_hash + 已折链 → 返回既有 knob_commit_hash，不重复写。
    - 返回 knob_commit_hash。
    """
def knob_commits_for(db, chapter) -> list[dict]:  # 只读：该 chapter 全部折链叶
def validate_knob_commits(db, store) -> list[dict]:  # 重算每叶哈希 + 父链；[] 干净
```

### runner 动词
`commit-override <output> --chapter N --dim 爽点 --new-value 4 [--rationale ...]` → 调 `record_override`（若暂存缺）再 `commit_override`，打印叶哈希 + 验证结果。**只在 `enable_quality=true` 时可用**（否则 RuntimeError，与 v0 一致）。

### 不变量
- `chapters`/`quality_verdicts` 行哈希不变（纯加法表）。
- `invalidate_from(N)` 同步 `DELETE FROM knob_commits WHERE chapter >= N` + `knob_overrides`（新表纳入失效）。
- 安全：`dim` 枚举 + `new_value` 1..5 校验（spec 安全审计 M2 注记）。

### 验收测试（合成，无真实 LLM）
- `test_commit_override_folds_into_chain`: record_override + commit_override → knob_commits 叶父链 = chapters.hash；暂存行 committed_at 非空。
- `test_commit_override_idempotent`: 同 override_hash 重复折链 → 同哈希、不重复写。
- `test_commit_override_rejects_bad_dim`: dim ∉ {爽点,钩子,压抑} → ValueError。
- `test_commit_override_requires_staging`: 无暂存行 → ValueError。
- `test_invalidate_deletes_knob_commits`: invalidate_from(N) 删 knob_commits 行 >= N。
- `test_validate_knob_commits_clean/tampered`: 重算干净；篡改叶哈希 → ValueError。
- `test_commit_override_verb_cli`: runner `commit-override` 端到端（合成 store）。

---

## 2. M2-2 旋钮规则引擎（`knob_rules.py`）

### 目标
替代 v0 的「固定 per-stage 旋钮」，让旋钮由 **bible 字段 + 卷主题 + `climax_chapter`** 决定（仍**无 LLM 在 v0 路径**；LLM climax 分类是更晚的 M2，不在本批）。`select_knobs` 的 thin override point 改为委托给规则引擎（版本化 `rationale.rules_ver`）。

### 数据契约
```
# quality_config.json 新增（可选；缺省则回退 per-stage 固定旋钮 = v0 行为）
"knob_rules": {
  "rules_ver": "v1",
  "rules": [
    {"when": {"climax_within_last_chapters": 5, "theme_contains": "逆袭"},
     "knob_set": {"爽点": 4, "钩子": 4, "压抑": 3}},
    ...
  ],
  "fallback_stage_knobs": true   # 无规则命中 → 回退 v0 的 per-stage 固定旋钮
}
```
`select_knobs` 规则：按 `rules_ver` 排序评估 `rules`（`when` 条件对 volume + climax + theme 匹配）→ 命中取该 `knob_set`；全不命中且 `fallback_stage_knobs` → 取 per-stage 固定旋钮（v0 行为，保证向后兼容）。`rationale` 增 `rules_ver` + `matched_rule`（命中规则 id / "stage-fallback"）。**纯确定性**（JSON 匹配，无 LLM）。

### API（`knob_rules.py`）
```
def evaluate_knob_rules(volume, stage, rules_cfg) -> tuple[dict, dict]:
    """返回 (knob_set, rationale)；rationale 含 rules_ver + matched_rule + bible_field + climax_chapter + stage。
    无命中 → per-stage 回退（v0 行为）。纯 JSON，确定性。"""
```
`volume_knobs.select_knobs` 改为：若 `quality_config.json` 有 `knob_rules` → 委托 `evaluate_knob_rules`；否则 v0 per-stage 固定（向后兼容，无配置改动者零影响）。

### 不变量
- 无 `knob_rules` 配置 → `select_knobs` 行为**逐字节等于 v0**（回归安全）。
- `regimes_for` / `select_knobs_for_volume` 的 regime 发射语义不变（每段 stage 各自评估规则）。
- 纯 JSON 匹配，无 LLM、无网络。

### 验收测试
- `test_knob_rules_no_config_is_v0_behavior`: 无 knob_rules → select_knobs 与 v0 固定旋钮逐字节相等。
- `test_knob_rules_match`: 合成 rule 命中 → 取该 knob_set + rationale.matched_rule = 规则 id。
- `test_knob_rules_fallback`: 全不命中 + fallback=true → per-stage 固定旋钮 + matched_rule="stage-fallback"。
- `test_knob_rules_deterministic`: 同 volume+同 rules_ver → 同旋钮（可复现）。
- `test_knob_rules_versioned`: rules_ver 记录在 rationale（可复现/可审计）。

---

## 3. M2-10 `migration_ledger` 表

### 目标
取代 v0 的 `chapters.schema_version` + `quality_schema_version` 标记行的**正式迁移账本**：一条 `migration_ledger` 行记录「哪次迁移、何时、把 schema 从 N 升到 N+1」，可审计、可回放。v0 的标记行保留（向后兼容），但新的迁移经 ledger。

### 数据契约
```
CREATE TABLE migration_ledger(
  migration_id TEXT PRIMARY KEY,      -- 迁移标识（如 "quality_schema_v1"）
  from_version INTEGER NOT NULL,
  to_version INTEGER NOT NULL,
  applied_at TEXT NOT NULL,
  description TEXT,
  ledger_hash TEXT NOT NULL);         -- digest({migration_id, from_version, to_version, applied_at, description, parent_hash})
```
`parent_hash` 指向上一条 ledger 行（或 GENESIS）→ 账本自身哈希链。

### API（`quality_store.py`）
```
def record_migration(db, migration_id, from_version, to_version, description="") -> str:
    """写一条 migration_ledger 叶（父链 = 上一条 ledger 的 ledger_hash / GENESIS）。幂等 on migration_id。"""
def migrations(db) -> list[dict]:  # 只读：全部迁移记录（按 applied_at）
def validate_migration_ledger(db) -> list[dict]:  # 重算每行 ledger_hash + 父链；[] 干净
def ledger_head(db) -> int:  # 当前 schema 版本（最新 to_version；无则 0）
```

### Store 衔接
- 门控块 `CREATE TABLE IF NOT EXISTS migration_ledger` + 启动时：若 `chapters` 已有 `schema_version` 标记（v0）但 ledger 空 → 回填一条 `migration_id="quality_schema_v1", from_version=0, to_version=1`（把 v0 标记行纳入账本，纯加法、不改现有行哈希）。
- `quality_schema_version` 标记行保留（双信号），新增 `schema_version` 语义升级经 ledger。

### 不变量
- 纯加法表；`chapters`/`quality_verdicts` 行哈希不变。
- 幂等：同 migration_id 重复 record_migration → 返回既有 ledger_hash。
- `validate_migration_ledger` 重算每行 + 父链（篡改 → ValueError）。

### 验收测试
- `test_record_migration_appends`: 首条 from=0→to=1 parent=GENESIS；次条 parent=前一条 ledger_hash。
- `test_record_migration_idempotent`: 同 migration_id → 同哈希、不重复。
- `test_store_backfills_v0_marker`: v0 有 schema_version 标记 + 空 ledger 的开库 → 自动回填 quality_schema_v1 行；现有 chapters 行哈希不变。
- `test_validate_migration_ledger_clean/tampered`: 重算干净；篡改 → ValueError。
- `test_ledger_head`: 返回最新 to_version；空 → 0。

---

## 4. 构建顺序（must-exist-before + 检查点）

1. **M2-10 `migration_ledger`**（独立，纯 DDL + record/validate）→ 检查点：append/幂等/回填/validate + 现有行哈希不变。
2. **M2-3 `commit_override`**（依赖 v0 knob_overrides）→ 检查点：折链/幂等/校验/失效/validate + runner 动词。
3. **M2-2 `knob_rules`**（依赖 select_knobs override point）→ 检查点：无配置=v0 行为 / 规则命中 / 回退 / 确定性 / 版本化。

每项独立 PR-able，但同批交付（都在 `feat/quality-layer-m2-early`）。全量门禁 = condensed 基线（3 既有失败）+ 新增 M2 测试全绿。
