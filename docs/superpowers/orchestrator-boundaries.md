# PipelineOrchestrator 职责边界梳理

**文件**: `packages/coding-agent/skills/novel-engine/src/novel_engine/pipeline/pipeline_orchestrator.py`
**基准 commit**: `1972ab8` | 5960 行 | 1 个类 | 75 个方法
**分析日期**: 2026-09-25
**范围**: 只读分析，无代码改动。产出为拆分施工图。

---

## 0. 最重要的发现

### 0.1 病灶是一个方法，不是一个文件

`generate_single_chapter`（L442–L1727，**1285 行，占全文件 21.5%**）本身就是一台缩微版的整条流水线：

- 直接内联 quality_policy 读取（10+ 处）
- 调用 `_deterministic_quality_gate`（门控裁决）
- 调用 6+ 个 remediation 方法
- 6+ 处分散的 draft/ 文件写入（无独立方法）

过去 30+ 轮 round 补丁绝大多数改的就是这个方法的某个 if 分支。**拆文件如果不先拆这个方法，等于换个地方继续打地鼠。**

### 0.2 "发布"边界跨三个文件

`chapters/novel/` 的实际写入在：
- `core/checkpoint.py`
- `agent_api.py`

orchestrator 本身只写 `chapters/draft/`。物理拆分时"发布提交"这一类职责必须跨文件一起设计，不能只在 orchestrator 内部找。

### 0.3 恢复职责已部分迁出

Task-11（commit `1972ab8`）把 `save_resume_state` 迁到 `production_runner.py`。orchestrator 里只剩"加载初始输入"（task_card/synopsis/world_state/journal），与"断点恢复状态"已是两件事。

---

## 1. 五类职责的方法清单

### A. 状态机 / 阶段驱动（Stage Driver）

入口和跨章节编排，负责"下一步该跑哪个阶段"。

| 方法 | 行数区间 | 备注 |
|---|---|---|
| `__init__` | L181–L343 | |
| `generate_single_chapter` | L442–L1727 | **god method**，见 §0.1，内部混杂 B/C/D 三类 |
| `_stage_world_sim` | L1727–L1735 | |
| `_stage_directing` | L1735–L1928 | |
| `_stage_synopsis` | L1928–L1955 | |
| `_stage_write` | L2669–L2909 | |
| `_stage_review` | L4305–L4645（340行） | 内部也调用门控 + 触发修复，混了 B/C |
| `_run_with_retry` / `_attempt_temperature` / `_record_slo_failure` | L5527–L5574 | |
| `_run_chapters_gated` | L5574–L5606 | |
| `run_mini_test` / `run_medium_test` / `run_full_unattended` / `run_remediation_and_finalize` | L5606–L5685 | 外部入口，编排层 |
| `_rollback_world_to` / `_recover_chapter` / `_regenerate_chapter` | L5387–L5445 | 失败恢复路径 |
| `_sync_db_if_changed` / `_load_session_tree` / `_save_session_tree` / `_load_director_fixed_context` | L343–L421 | |
| `close` | L5949 | |

### B. 门控裁决（Gate Adjudication）

判断"这版文本能不能过"，是最该有单一真值源的部分。

| 方法 | 行数区间 | 备注 |
|---|---|---|
| `_deterministic_quality_gate` | L1972–L2145 | 中心裁决方法 |
| `_run_continuity_gate` | L2931–L3032 | |
| `_run_scope_gate` | L3032–L3157 | |
| `_run_density_gate` | L3157–L3269 | |
| `_run_scene_progression_gate` | L3269–L3425 | |
| `_run_cross_scene_repeat_gate` | L3425–L3509 | |
| `_run_latin_leak_gate` | L3548–L3600 | |
| `_run_pov_interiority_gate` | L3600–L3665 | |
| `_run_constraint_compliance_gate` | L3665–L3724 | |
| `_run_punctuation_health_gate` | L3724–L3854 | |
| `_run_final_precommit_gate` | L3967–L4037 | |
| `_run_boundary_reprise_gate` | L4037–L4098 | |
| `_run_boundary_gate` | L4188–L4282 | |
| `_run_continuity_audit` | L5454–L5473 | |
| `_forbidden_violations` | L5501–L5512 | |
| `_review_issue_is_blocking` / `_forbidden_violation_is_blocking`（模块级） | L83–L100 | **backlog 里"reviewer severity 硬映射"任务的确切落点** |

> **12 个 `_run_*_gate` 方法**命名规整、平均 80–150 行、边界干净，是拆分风险最低、可第一批动手的部分。

### C. 修复 / 补救（Remediation Loop）

输入是门控失败结果，输出是重写后文本。天然应独立为 `remediation/` 子模块，接口 `(text, gate_failure) -> new_text`。

| 方法 | 行数区间 | 备注 |
|---|---|---|
| `_validate_and_regen_scenes` | L2212–L2532（320行） | |
| `_scene_issues_to_directives` / `_regen_scene_for_id` / `_cc25_rescue_scene_l2l3` | L2532–L2669 | |
| `_punct_only_repair` / `_repair_paragraphs_punct_parallel` / `_repair_paragraphs_punct_batched` | L3509–L3548, L3854–L3967 | |
| `_apply_length_floor` | L4098–L4188 | |
| `_enforce_word_count` | L4645–L4736 | |
| `_enforce_mandatory_beats` | L4736–L5046（310行） | |
| `_patch_weak_scenes` | L5046–L5260（214行） | |
| `_cc25_literary_rewrite` / `_rewrite_weak_dimensions` | L5288–L5387 | |
| `_finalize_current_novel` | L4282–L4305 | 标点/长句收尾，幂等 |

### D. 发布 / 持久化（Publish & Journal）— 跨文件

| 位置 | 内容 |
|---|---|
| `pipeline_orchestrator.py`（本文件） | 写 `chapters/draft/*.txt`、`chapters/draft/failed/*`，散落在 `generate_single_chapter` 内 6+ 处（无独立方法） |
| 本文件 | `_journal_validated_scenes` / `_write_polish_flags` / `_load_journal_scenes` / `_sync_journal_from_draft` / `_assemble_chapter_text` |
| **`core/checkpoint.py`** | 实际写 `chapters/novel/*.txt`（正式发布）、备份、回滚 |
| **`agent_api.py`** | 也直接读写 `chapters/novel/`，路径与 checkpoint.py 部分重复 |

> **关键发现**: "发布"职责分散在三个文件里，没有单一入口。路径拼接 `chapters/novel/chapter_{n}.txt` 在 `checkpoint.py` 和 `agent_api.py` 里各写了一遍。建议先抽共享函数，成本很低。

### E. 恢复（Resume）— 已部分迁出

| 方法 | 行数区间 | 备注 |
|---|---|---|
| `_load_task_card_file` / `_load_synopsis_file` / `_load_world_state_file` | L5685–L5703 | 加载"初始输入"，非断点恢复 |
| `_load_journal_scenes` | L2205–L2212 | |
| （断点恢复状态本身） | 已迁移至 `production_runner.save_resume_state`（Task-11） | |

### F. 杂项 / 工具函数

`_novel_string`、`_ensure_chinese`、`_extract_keywords`、`_bpt`、`_structure_fingerprint`、`_beat_to_str`、`_is_coherent_short`、`_normalize_blueprint_beats`、`get_cost_sandbox_report`、`_flag_for_human`、`_apply_sequence_order`。体量小、依赖少，拆分时顺挪到 `utils.py`。

---

## 2. 交叉依赖 / 坏味道清单

拆分前必须先解决，否则拆完还会互相调用。

### 2.1 God method：`generate_single_chapter`
同时内联调用 B（`_deterministic_quality_gate`）、C（多个 remediation 方法）、D（draft 写入）。**这是唯一一个横跨三类职责的方法**。拆分顺序建议：
1. 先把 B、C、D 的部分抽成"仅接收状态、返回结果"的纯函数调用
2. 最后 `generate_single_chapter` 只剩状态机编排本身
3. 不要直接把整个方法搬到某个新文件

### 2.2 `_stage_review`（L4305–L4645，340行）
同时做门控判断和触发修复，是第二个需要"先拆职责再拆文件"的方法。

### 2.3 `load_quality_policy(self.root)` 被直接调用 20+ 次
散布在 A/B/C 三类方法里，没有统一通过"policy context"对象传递。每个子模块都会各自 import + 各自读文件。建议：在最外层状态机里读一次，通过参数传给 B/C 类方法。

### 2.4 `chapters/novel/` 路径拼接重复
`checkpoint.py` 和 `agent_api.py` 各手写了一遍 `"chapters" / "novel" / f"chapter_{n}.txt"`。先抽共享函数，作为"发布边界"拆分的第一步。

### 2.5 12 个 `_run_*_gate` 边界最干净
命名一致、依赖最少，是拆分风险最低、第一批可动手的部分。

---

## 3. 建议的拆分顺序

**不是本次任务范围，仅供排期参考。**

### 第一批（低风险，可单独排期）
- 12 个 `_run_*_gate` → 独立 `gates/` 子模块
- 每个配一条现有 regression 测试搬过去验证
- 预计耗时：1 session

### 第二批
- C 类 remediation 方法 → 独立 `remediation/` 子模块
- 接口统一为 `(text, gate_result) -> text`
- 预计耗时：1–2 sessions

### 第三批（风险最高，建议留到最后，且要专门 session）
- 把 `generate_single_chapter` 按"编排 vs 门控 vs 修复 vs 发布"拆开
- 只保留状态机本身在 orchestrator 里

### 并行动作（低成本，可随时做）
- `chapters/novel/` 路径拼接 → 抽共享函数（`core/checkpoint.py` + `agent_api.py`）
- 模块级 `_review_issue_is_blocking` / `_forbidden_violation_is_blocking` — 这是 backlog 里"reviewer severity 硬映射"的确切落点，等 beats pilot 稳定后直接在这里接入

---

## 4. 与 backlog 的关联

| Backlog 条目 | 对应方法/位置 | 状态 |
|---|---|---|
| reviewer severity 硬映射 | `_review_issue_is_blocking` / `_forbidden_violation_is_blocking`（L83–L100） | 等 beats pilot |
| Task-11 休眠方法 | 已删除（commit `1972ab8`） | ✅ 完成 |
| force-best log 文案 | 已修复（commit `1972ab8`） | ✅ 完成 |
| force-note 快照 | 已修复（commit `1972ab8`） | ✅ 完成 |

---

## 5. 已完成拆分（2026-09-25）

### 5.1 Phase 2: 门控方法拆分 ✅

**commit**: `3bae92a`

将 13 个门控/修复方法从 `pipeline_orchestrator.py` 移至 `pipeline/gates.py`:

| 方法 | 原行数 | 状态 |
|---|---|---|
| `_run_continuity_gate` | L2931-3031 | ✅ 已移入 gates.py |
| `_run_scope_gate` | L3032-3156 | ✅ 已移入 gates.py |
| `_run_density_gate` | L3157-3268 | ✅ 已移入 gates.py |
| `_run_scene_progression_gate` | L3269-3424 | ✅ 已移入 gates.py |
| `_run_cross_scene_repeat_gate` | L3425-3507 | ✅ 已移入 gates.py |
| `_run_latin_leak_gate` | L3548-3599 | ✅ 已移入 gates.py |
| `_run_pov_interiority_gate` | L3600-3664 | ✅ 已移入 gates.py |
| `_run_constraint_compliance_gate` | L3665-3723 | ✅ 已移入 gates.py |
| `_run_punctuation_health_gate` | L3724-3852 | ✅ 已移入 gates.py |
| `_run_final_precommit_gate` | L3967-4036 | ✅ 已移入 gates.py |
| `_run_boundary_reprise_gate` | L4037-4097 | ✅ 已移入 gates.py |
| `_apply_length_floor` | L4098-4187 | ✅ 已移入 gates.py |
| `_run_boundary_gate` | L4188-4281 | ✅ 已移入 gates.py |

**辅助方法**（同样移入 gates.py）:
- `_punct_only_repair`（L3509-3547）
- `_repair_paragraphs_punct_parallel`（L3854-3892）
- `_repair_paragraphs_punct_batched`（L3894-3965）

**orchestrator 现有 16 行委托块**，使用 lambda + `_gates` 模块引用:
```python
_run_continuity_gate = staticmethod(lambda self, *a, **k: _gates._run_continuity_gate(self, *a, **k))
# ... 15 more
```

**效果**: orchestrator 从 5960 → 4630 行（-22%），gates.py 1391 行独立可测。

### 5.2 Side C: 共享路径函数 ✅

**commit**: `1aff4efb8`

在 `core/checkpoint.py` 新增 `novel_chapter_path(root, chapter)` 单一真值源:
```python
def novel_chapter_path(root, chapter: int) -> Path:
    return Path(root) / "chapters" / "novel" / f"chapter_{chapter}.txt"
```

更新了 5 个文件共 9 处调用点:
- `core/checkpoint.py`: 4 处
- `agent_api.py`: 2 处
- `pipeline/gates.py`: 1 处
- `pipeline/pipeline_orchestrator.py`: 1 处
- `pipeline/reset_state.py`: 1 处

`web_dashboard.py` 使用目录级路径（`novel_dir`），不涉及文件级拼接，未改动。

---

## 6. 已完成拆分（续）

### 6.1 Stage6 Foreshadow 检查 ✅

**commit**: `42a9b1fe3`

将 `generate_single_chapter` 内的 stage6 伏笔覆盖检查移至 `pipeline/final_gate.py`:

- `run_stage6_foreshadow_check(root, chapter_num, task_card, final_text, cur_score)` 返回 dict:
  - `blocked`: 是否硬阻断
  - `missing_fs_ids`: 注册但未解析的伏笔 ID
  - `blocked_scenes`: 场景级缺失详情
- 调用时机：在 `final_text` 净化后、最终门控前执行
- 阻塞条件：注册伏笔未解析 / 场景级伏笔缺失

### 6.2 Review Journal 持久化 ✅

**commit**: `edd8e82f4`

将每章评审结果追加逻辑移至 `pipeline/review_journal.py`:

- `save_review_journal(root, chapter_num, score, review)` 追加到 `audit/per_chapter_reviews.json`
- 供滑动窗口质量记忆使用

### 6.3 Cost Tracking 简化 ✅

**commit**: `edd8e82f4`

将手动 token 累加替换为 `call_metrics.snapshot()`:

```python
# Before: 10+ lines of manual accumulation
# After:
from novel_engine.core.call_metrics import snapshot as _snapshot_metrics
self.cost_tracker.update(_snapshot_metrics())
self._reset_call_log()
```

---

## 7. Phase 4b 已完成拆分（2026-09-26）

### 7.1 `_commit_chapter` 方法提取 ✅

**commit**: `eefb54bbc`

从 `generate_single_chapter` 内提取 `_commit_chapter` 方法:

- 方法签名: `_commit_chapter(self, chapter_num, task_card, synopsis, world_state, result)`
- 功能: 将章节结果持久化到 `chapters/draft/` 和 `chapters/novel/`
- 原代码位置: L1526-L1607（82 行）
- 调用点: 在 `generate_single_chapter` 的阶段提交逻辑中调用

### 7.2 `_decide_publish` 方法提取 ✅

**commit**: `b11287cef`

从 `generate_single_chapter` 内提取 `_decide_publish` 方法:

- 方法签名: `_decide_publish(self, chapter_num, task_card, synopsis, world_state, result, score, sm, review, final_text, violations, det_issues, det_soft, high_list, apply_world_state, cur_score, _commit_policy)`
- 功能: 根据分数和门控结果决定章节是否发布到 `chapters/novel/`
- 原代码位置: L1608-L1668（61 行）
- 调用点: 在 `generate_single_chapter` 的最终决策阶段调用

### 7.3 `_policy` 属性缓存 ✅

**commit**: `30efff618`

添加 `_policy` 属性缓存 `load_quality_policy(self.root)` 调用:

- 属性位置: L444-L449
- 功能: 避免在 `generate_single_chapter` 中重复读取 quality_policy 文件
- 原调用次数: 23 次（其中 11 次在 `generate_single_chapter` 内）
- 现行为: 首次访问时缓存，后续复用

---

## 8. Phase 4c 已完成拆分（2026-09-26）

### 8.1 Forbidden Gate 评估函数 ✅

**commit**: `0bf97a0f4`

将 forbidden gate 评估逻辑从 `generate_single_chapter` 内联代码提取为纯函数:

- 新增 `ForbiddenGateResult` dataclass: 返回 `apply_world_state`, `success`, `violations`, `flag_human`, `flag_reason`
- 新增 `evaluate_forbidden_gate()` 纯函数接收所有依赖作为参数,避免直接修改 `self` 状态
- 调用点通过返回对象解构结果,消除隐式副作用

```python
@dataclass
class ForbiddenGateResult:
    apply_world_state: bool
    success: bool
    violations: list = field(default_factory=list)
    flag_human: bool = False
    flag_reason: str = ""

def evaluate_forbidden_gate(
    chapter_num: int,
    current_novel: str,
    review: dict,
    policy: dict,
    apply_world_state: bool,
    forbidden_violations_fn: Callable,
) -> ForbiddenGateResult:
    ...
```

### 8.2 Fix Loop 早期停止检查 ✅

**commit**: `ee6b005bd`

将 fix loop 的早停条件提取为独立函数:

- 新增 `check_fix_loop_early_stop(iteration, no_improve, best_score) -> bool`
- 当 `no_improve >= 2` 且 `iteration >= 2` 时触发早停
- 消除了内联的条件判断,便于单元测试

---

## 9. 待拆分（按优先级排序）

### Phase 3: Best Candidate 追踪逻辑（中风险）

**建议提取为 `FixLoopContext` dataclass + `track_best_candidate()` 函数:**

```python
@dataclass
class FixLoopContext:
    best_score: float
    best_novel: str
    best_draft: str
    best_journal_snapshot: list
    best_green: Optional[tuple]
    no_improve: int
```

- **阻塞原因**: 与采纳逻辑(L991+)深度耦合,需同时修改追踪侧和采纳侧
- **建议顺序**: 
  1. 先提取 `_track_best_candidate()` 纯函数(仅更新状态,不改采纳)
  2. 验证后,再重构采纳逻辑使用新数据结构
  3. 最后考虑是否将主循环整体移入 `remediation/` 模块

### Phase 4: 主修复循环拆分（高风险，需专门 session）

**fix loop 结构 (L723-L1322, 600行):**

```
fix loop
├── Initialization (L697-L720)
│   ├── orig_novel, orig_draft (backup)
│   ├── pre_fix_score
│   ├── publication_line, soft_line
│   ├── max_fix (default 2 rounds)
│   ├── current, current_draft
│   ├── best (track highest score candidate)
│   ├── best_green (track hard-gate-clean candidate)
│   └── no_improve (stall counter)
│
├── Main loop (for _ in range(max_fix))
│   ├── _enforce_mandatory_beats (pre-check each round)
│   ├── _stage_review (review)
│   ├── _deterministic_quality_gate (gate check)
│   ├── Decision logic
│   │   ├── s >= min_ch → break (pass)
│   │   ├── _gray_ok → break (gray band release)
│   │   └── no_improve >= 2 → break (stall)
│   │
│   ├── Remediation path selection
│   │   ├── _patch_weak_scenes (scene-level incremental repair)
│   │   ├── _cc25_literary_rewrite (literary rewrite)
│   │   └── _rewrite_weak_dimensions (full chapter rewrite)
│   │
│   └── Gate regression
│       └── _deterministic_quality_gate (verify)
│
└── Finalization (L1280-L1299)
    ├── Adopt best_green or best
    ├── Journal atomic rollback
    ├── Secondary word count gate (_apply_length_floor)
    └── Atmosphere dedup detection (atmosphere_dedup)
```

- **风险**: 嵌套 try/except,多处 break,状态回溯(best/best_green/no_improve tracking),journal 原子操作
- **建议**: 先完成 Phase 3 (Best Candidate 追踪),建立清晰的 `FixLoopContext`/`FixResult` 接口后再动主循环

### P2: Reviewer severity 硬映射

- 位置: `pipeline_orchestrator.py` L83-100（`_review_issue_is_blocking`）
- 触发条件: beats pilot dimension→category mapping spec 落地
- 操作: 在 `quality_policy.DEFAULT_POLICY["severity_map"]` 加 9 条映射

---

## 10. 当前文件规模对比

| 文件 | 原始 | 当前 | 变化 |
|---|---|---|---|
| `pipeline_orchestrator.py` | 5960 | 4566 | **-1394 (-23.4%)** |
| `pipeline/gates.py` | - | 1494 | +1494 (新) |
| `pipeline/final_gate.py` | - | 112 | +112 (新) |
| `pipeline/review_journal.py` | - | 45 | +45 (新) |
| **合计** | **5960** | **6217** | **+257 (净增量)** |

> 净增量来自: 新增 dataclass/函数签名/注释。提取后核心逻辑行数减少,但边界定义增加了代码量。

---

## 11. 测试基线

**测试结果**: 874 passed, 2 pre-existing failures

**Pre-existing failures**:
- `test_pipeline_incremental_patcher` - AssertionError
- `test_same_location_cluster_shared_objects_not_flagged` - gate now detects shared images

**每次提取后验证**: 确保 874 passed, 0 new failures。

---

## 12. Fix Loop 详细分析（Phase 4c 文档）

### 12.1 循环结构

```
for _ in range(max_fix):
    # 每轮开始前: _enforce_mandatory_beats
    # 评审: _stage_review
    # 门控: _deterministic_quality_gate
    
    # 决策:
    # - s >= min_ch → break (pass)
    # - gray_ok → break (gray band release)
    # - no_improve >= 2 → break (stall)
    
    # 修复路径选择:
    # - _patch_weak_scenes (scene-level)
    # - _cc25_literary_rewrite (literary)
    # - _rewrite_weak_dimensions (full chapter)
```

### 12.2 状态追踪

| 变量 | 类型 | 用途 |
|---|---|---|
| `best` | tuple | (score, novel, draft) - 最高分候选 |
| `best_green` | tuple | (score, novel, draft, journal_snapshot) - 硬门全绿候选 |
| `no_improve` | int | 连续未改进轮数 |
| `_best_journal_snapshot` | list | best 候选的 journal 快照 |

### 12.3 采纳逻辑 (L991+)

```python
if best_green is not None:
    best_score, best_novel, best_draft, _best_journal_snapshot = best_green
    if best_score < best[0]:
        logger.warning(f"ch{chapter_num}: adopt gate-green candidate {best_score} over higher-score blocked candidate {best[0]}")
else:
    best_score, best_novel, best_draft = best

self.current_novel = best_novel
self._draft_novel = best_draft
result["score"] = best_score
# Journal 原子回滚...
```

### 12.4 推荐的接口草案

```python
@dataclass
class FixResult:
    """Fix loop 返回值"""
    success: bool
    published: bool
    score: float
    novel_text: str
    draft_text: str
    gray_band_release: bool = False
    force_published: bool = False
    note: str = ""
    severe_shortfall: bool = False

@dataclass  
class FixLoopContext:
    """Fix loop 状态追踪"""
    best_score: float
    best_novel: str
    best_draft: str
    best_journal_snapshot: list
    best_green: Optional[tuple]
    no_improve: int
    
    def update_best(self, score, novel, draft, journal_snapshot, det_passed, has_high):
        """更新最佳候选"""
        ...
    
    def should_stop_early(self, iteration):
        """检查是否应早停"""
        return self.no_improve >= 2 and iteration >= 2
```

### 12.5 拆分建议顺序

1. **Phase 3.1**: 提取 `track_best_candidate()` 纯函数（仅状态更新，不改采纳逻辑）
2. **Phase 3.2**: 验证 `FixLoopContext` 接口是否顺手
3. **Phase 3.3**: 重构采纳逻辑使用新数据结构
4. **Phase 4**: 最后考虑主循环整体提取

---

*文档最后更新: 2026-09-26*


---

## 13. 教训：提取方法/拆模块到 gates.py 的委托写法与全量验证（2026-09-27）

### 13.1 事件

提交 `3bae92a8d` 将 13 个 gate 方法提取到 `pipeline/gates.py` 后，在 orchestrator 类体内用以下委托写法（16 处：12 gate + `_apply_length_floor` + 3 个标点修复）：

```python
_run_continuity_gate = staticmethod(lambda self, *a, **k: _gates._run_continuity_gate(self, *a, **k))
```

`staticmethod()` 关闭 Python 自动实例绑定。调用点仍按普通实例方法调用（`self._run_continuity_gate(_bn, task_card, _scenes_list, purified)`），实际执行时第一个实参顶替 lambda 的 `self`，后续参数集体错位一位，末参（如 `assembled_text`）丢失：

```
_Gates._run_continuity_gate() missing 1 required positional argument: 'assembled_text'
```

有 try/except 包裹的委托（如 `_run_boundary_gate`）静默跳过、只打 warning——表现为"日志正常、实际未把关"。

### 13.2 修复

去掉 `staticmethod()` 外壳，改用普通实例方法（保留自动 self 绑定）：

```python
def _run_continuity_gate(self, *a, **k):
    return _gates._run_continuity_gate(self, *a, **k)
```

修复提交：`f23292e5a`。修复后全量测试从 34 failed 回到 **874 passed / 2 failed**（基线）。

### 13.3 流程教训（强制要求）

1. **"提取方法/拆 gate 到模块"类改动，提交前必须跑全量测试套件**（从 `packages/coding-agent/skills/novel-engine/src` 执行 `python -m pytest novel_engine/tests/ -q`）。本次回归分布在 8+ 个测试文件，只跑新增/相关测试会完全漏掉。
2. **禁止** `staticmethod(lambda self, *a, **k: ...)` 委托写法——关闭实例绑定易踩参数错位陷阱。
3. 委托到模块函数一律用普通方法定义（见 13.2）。

### 13.4 新增规则：测试依赖真实生成产物的替代方案（2026-09-29）

**背景**：远端全新 clone 环境下，依赖本地真实生成产物（`chapters/novel/chapter_N.txt`、`runtime/state.db`、`audit/per_chapter_reviews.json`）的测试会失败。

**规则**：新增或修改测试若需真实生成产物做回归验证，必须同时提供最小合成 fixture 兜底，禁止依赖"本地恰好跑过真实生成"。具体做法：
1. 在 `novel_engine/tests/fixtures_real_data.py` 提供结构化合成数据（synthetic chapter text、最小 state.db schema、最小 per_chapter_reviews.json 结构）。
2. 测试函数优先使用 `tmp_path` 写入合成数据，而非从生产路径读取。
3. 若被测逻辑需要真实文件路径（如 `load_authoritative_scenes(root, ch)`），将 root 参数注入为 `tmp_path` 下的临时目录，由测试负责初始化对应目录结构。

**已改造**：
- `test_d2_ch4_real_text_regression`（test_round18_gates_wiring.py）：改用 `CHAPTER_4_SYNTHETIC`
- `test_p7b_polarity_ch1_ch2_ch3_clean`（test_round18_gates_wiring.py）：改用合成章节文本
- `test_p7d_crisis_scene_texts_str_ch4_clean`（test_round18_gates_wiring.py）：改用合成章节文本
- `test_ch5_real_text_no_contradiction_after_rebase`（test_round19_reaction_consistency.py）：`_ch5_text()` 改用 `CHAPTER_5_SYNTHETIC`
- `test_ch5_real_text_no_false_positive_on_ch1_to_ch4`（test_round19_reaction_consistency.py）：改用 `CHAPTER_4_SYNTHETIC`
- `test_data_dir_contains_real_state_db` → 重命名为 `test_data_dir_resolves_to_package_root`，仅验证 DATA_DIR 解析逻辑，不再断言运行时产物存在
