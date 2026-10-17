# PA-ARCH-RESEARCH-AND-REFACTOR-01 — 03-TARGET-ARCHITECTURE

**Date:** 2026-10-17  
**Basis:** SOURCE-AUDIT + REFERENCE-RESEARCH + CURRENT-ARCHITECTURE evidence

---

## 1. 目标架构职责划分

### A. Prime-Agent运行时

| 职责 | 当前状态 | 目标状态 |
|------|----------|----------|
| 理解章节创作目标 | ✅ `task_card`包含目标 | ✅ 保持不变 |
| 制定/调整执行计划 | ❌ 无（固定流程） | ✅ FixLoopAgent接入RLM路径 |
| 决定下一步调用工具 | ❌ 无 | ✅ 领域工具注册到kernel |
| 读取工具返回 | ❌ 无 | ✅ 已有`agent_message.recv()` |
| 分析失败和质量反馈 | ⚠️ 规则驱动 | ✅ RLM推理 + 规则兜底 |
| 选择定向修复/重新规划/停止 | ⚠️ FixLoopAgent规则 | ✅ `recommend_via_rlm()`激活 |
| 预算内完成任务 | ❌ 无上限 | ✅ per-chapter LLM调用计数 |

### B. 小说领域服务（保持不变）

- 世界观（StateDB查询）
- 人物档案及状态（StateDB查询）
- 章节大纲（task_card JSON）
- 时间线（timeline_anchors）
- 伏笔（foreshadowing_state）
- 前文及章节摘要（current_novel）
- 场景生成接口（WriterAgent）
- 连续性检查（15+ quality gates）
- 审稿和修复工具（ReviewerAgent + Patcher）

### C. 确定性控制层（保持不变）

- 状态机（ChapterPhase FSM）
- 任务依赖（scene group依赖关系）
- 任务次数上限（max_chapter_fix_rounds: 2）
- 超时与取消（LLM timeout + retry）
- 数据校验（15+ quality gates）
- 检查点（CheckpointManager）
- 正式稿保护（hash dedup + gate block）
- 最终质量门禁（score >= 88）
- 数据一致性（StateDB事务）

### D. 模型和工具适配层（新增/扩展）

| 组件 | 状态 | 职责 |
|------|------|------|
| `NovelRLMAdapter` | ✅ 已有 | RLM分发 + failover + tracer |
| `FixLoopAgent` | ✅ 已有（规则） | 决策路由（待RLM集成） |
| `ProductionTracer` | ✅ 已有 | trace持久化到runtime/traces/ |
| `domain_tools.py` | ✅ 已有（11个） | 工具封装，待注册到kernel |

---

## 2. 目标架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                     Prime-Agent IPython Kernel                               │
│                                                                              │
│  用户输入: "生成第1章"                                                        │
│         ↓                                                                    │
│  Prime-Agent Agent Loop                                                     │
│         ↓                                                                    │
│  await novel_engine.generate_chapter(1)                                      │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     NovelRLMAdapter (核心适配层)                              │
│                                                                              │
│  run_chapter(chapter_num)                                                   │
│    │                                                                         │
│    ├─ [Phase 1] _stage_world_sim(ch)      → StateDB查询 (0 LLM)             │
│    ├─ [Phase 2] _stage_directing(ch, ws)  → LLM×1-3 (ChapterDirector)       │
│    ├─ [Phase 3] _stage_synopsis(tc)       → LLM×1 (SynopsisAgent)           │
│    │                                                                         │
│    ├─ [Phase 4] _stage_write_rlm(ch, tc, syn, rlm, msg)                     │
│    │    ├─ for each scene_group:                                           │
│    │    │   ├─ rlm("sub-task") → handle                                    │
│    │    │   ├─ msg.send(spec, receiver="child")                            │
│    │    │   ├─ msg.recv(receiver="parent") → [(bp, content), ...]          │
│    │    │   └─ fallback: ThreadPoolExecutor if rlm fails                   │
│    │    └─ assemble chapter text from scene_contents                        │
│    │                                                                         │
│    ├─ [Phase 5] _stage_review(ch, tc, syn, text, ws)                        │
│    │    ├─ ReviewerAgent.review_chapter() → score + issues                  │
│    │    └─ grade_review(score) → verdict ("pass"/"fix"/"fail")             │
│    │                                                                         │
│    ├─ [Phase 6] FixLoopAgent决策                                            │
│    │    ├─ build_diagnostic(ch, text, best_state, iter, result, ...)       │
│    │    ├─ recommend_via_rlm(diag, rlm, msg)  ← ★待激活                    │
│    │    │   └─ [fallback] recommend(diag) 规则决策                         │
│    │    ├─ action="patch"    → apply_targeted_repair(ch, scene, directive) │
│    │    ├─ action="rewrite_scene" → rewrite_scene(ch, scene_nums, directive)│
│    │    ├─ action="rewrite_chapter" → regenerate chapter from synopsis     │
│    │    ├─ action="stop"     → accept current best                         │
│    │    └─ action="proceed"  → skip fix loop                               │
│    │                                                                         │
│    └─ [Phase 7] commit_chapter(ch, text, syn, tc, ws)                       │
│         ├─ deterministic quality gates (15+)                               │
│         ├─ checkpoint hash computation                                      │
│         └─ save checkpoint + update StateDB                                │
│                                                                              │
│  ProductionTracer: 所有phase记录trace → runtime/traces/{run_id}.jsonl       │
└───────────────────────────┬─────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  ┌───────────┐      ┌─────────────┐      ┌──────────────┐
  │ StateDB   │      │ SessionTree  │      │ CheckpointMgr│
  │ (保持)    │      │ (保持)       │      │ (保持)       │
  └───────────┘      └─────────────┘      └──────────────┘
```

---

## 3. 新旧路径对比

| 维度 | 现有路径（无RLM） | 目标路径（有RLM） |
|------|------------------|-------------------|
| 场景生成 | ThreadPoolExecutor并发 | RLM子代理（组内串行，组间串行） |
| 修复决策 | 硬编码2轮fix loop | FixLoopAgent规则/RLM推理 |
| 工具调用 | 无（固定流程） | 11个领域工具可用 |
| 可观测性 | 无trace | ProductionTracer完整trace |
| 降级处理 | 无（唯一路径） | RLM失败→ThreadPool兜底 |
| LLM调用次数 | ~4-30+/章 | ~4-30+/章 + 1 RLM dispatch |

**关键差异：** Prime-Agent实际执行了「规划→工具调用→观察→决策」循环，而非单次LLM调用。

---

## 4. 结构化数据Schema（P5要求）

基于现有代码和PydanticAI启发，定义以下数据类型：

```python
# 章节任务计划
@dataclass
class ChapterPlan:
    chapter_num: int
    task_card: dict  # 来自ChapterDirector
    synopsis: dict   # 来自SynopsisAgent
    world_state: dict  # 来自StateDB

# 场景任务规格
@dataclass
class SceneTask:
    scene_num: int
    beats: list[str]       # 必 Beats
    perspective: str        # 视角
    pacing_constraints: str # 节奏约束
    event_ledger: list[str] # 已发生事件

# 工具请求/结果
@dataclass
class ToolRequest:
    tool_name: str
    args: dict
    chapter_num: int

@dataclass
class ToolResult:
    tool_name: str
    success: bool
    output: Optional[dict] = None
    error: Optional[str] = None

# 审稿结果
@dataclass
class ReviewResult:
    score: float
    verdict: str  # "pass" | "fix" | "fail"
    issues: list[dict]  # [{"type": str, "severity": str, "scene_num": int}]
    review_text: str

# 修复计划
@dataclass
class RepairPlan:
    action: str  # "patch" | "rewrite_scene" | "rewrite_chapter"
    target_scenes: list[int]
    directive: str
    reasoning: str

# 质量门禁结果
@dataclass
class QualityGateResult:
    gates_run: int
    issues: list[str]
    soft_issues: list[str]
    passed: bool

# 执行检查点
@dataclass
class ExecutionCheckpoint:
    chapter_num: int
    stage: str  # "world_sim" | "directing" | "synopsis" | "write" | "review" | "commit"
    input_hash: str
    output_hash: Optional[str] = None
    status: str  # "pending" | "completed" | "failed"
    timestamp_ms: int
```

**校验规则：**
- `ChapterPlan.task_card`必填字段：chapter_num, scene_blueprints
- `SceneTask.beats`不可为空列表
- `ReviewResult.score`范围[0, 100]
- `QualityGateResult.passed`必须与issues列表一致（硬issue存在则不能通过）

---

## 5. 验证计划

### 5.1 已有验证（已完成）

```bash
pytest: 52 passed (rlm_adapter, fix_loop_agent, observability, p5_recovery, p6_continuity)
unittest: 5 passed (state_db, session_tree, phase_cache, canary)
```

### 5.2 待验证（下一阶段）

| 测试项 | 状态 | 说明 |
|--------|------|------|
| FixLoopAgent集成到RLM路径 | PARTIAL | `recommend_via_rlm()`已定义但未在主路径调用 |
| 领域工具实际调用测试 | BLOCKED | 需要Prime-Agent kernel注入 |
| 实机端到端测试（ch1-ch10） | BLOCKED | 需明确授权 |
| Step级结果缓存 | NOT STARTED | DBOS启发，需实现 |