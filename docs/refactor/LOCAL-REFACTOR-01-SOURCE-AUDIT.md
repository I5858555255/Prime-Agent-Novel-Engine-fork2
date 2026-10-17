# LOCAL-REFACTOR-01 — Source Audit

**Date:** 2026-10-17  
**Scope:** `packages/coding-agent/skills/novel-engine/src/novel_engine/`  
**Files audited:** All production Python files (105 non-test .py files, 6893 LOC)

---

## 1. Full Call Chain — Single Chapter Generation

```
generate_chapter(ch)                          [agent_api.py:98]
  └─ orch._run_with_retry(ch)                 [pipeline_orchestrator.py:4759]
       └─ generate_single_chapter(ch)          [pipeline_orchestrator.py:473]
            │
            ├─ Stage 0: PLANNING               [state_machine.transition]
            ├─ Stage 1: _stage_world_sim(ch)   [line 1521] → 0 LLM calls (pure Python StateDB queries)
            ├─ Stage 2: _stage_directing(ch)   [line 1529] → 1 LLM call (ChapterDirector.generate_task_card)
            │     └─ chapter_director.py → call_llm() (1 call, max 3 retry attempts)
            │
            ├─ Stage 3: _stage_synopsis(tc)    [line 1757] → 1 LLM call (SynopsisAgent.generate_synopsis)
            │     └─ writer_agent.py:82 → call_llm() (1 call)
            │
            ├─ Stage 4: _stage_write(tc, syn)  [line 2627]
            │     ├─ writer.generate_full_chapter(tc, syn) → N scene groups
            │     │   └─ writer.generate_scenes(tc, syn) [line 781]
            │     │       └─ ThreadPoolExecutor(max_workers=4)
            │     │           └─ generate_scene(bp) × M scenes [line 444]
            │     │               └─ _client30.chat_completion() [line 542] — 1 call per scene
            │     │                   (CC30: flash first, switch to pro on degradation signals)
            │     ├─ _enforce_word_count(novel_text) — deterministic (no LLM)
            │     └─ polish_chapter(novel_text) [line 1019] — M LLM calls (1 per scene, polish_router)
            │         └─ _polish_single() → client.chat_completion()
            │     Plus: up to 2 expansion rounds (call_llm per short scene) [line 2730]
            │
            ├─ Stage 5: _stage_review(ch, tc, syn, novel, ws) [line 2965]
            │     ├─ ReviewerAgent.review_chapter() → call_llm() [line 368] — 1 call
            │     ├─ Re-review if first score <65 with clean gates: +1 call
            │     └─ Triple review if score in [81.5,85.5] band or gate_fired: +1–2 calls
            │     Total review calls: 1–4 per chapter
            │
            ├─ Fix loop (max 2 iterations)       [line 3970, _run_fix_loop_iteration]
            │     Each iteration:
            │     ├─ _decide_fix_loop_outcome() → _stage_review() → 1–4 LLM calls
            │     └─ _dispatch_remediation()
            │           ├─ _patch_weak_scenes() → N LLM calls (1 per weak scene)
            │           └─ _rewrite_scene() → M LLM calls (polish_router)
            │
            └─ Stage 6: Commit
                  ├─ evaluate_forbidden_gate() — deterministic
                  ├─ run_stage6_foreshadow_check() — deterministic
                  ├─ scrub_scaffold_latin() — deterministic
                  ├─ _deterministic_quality_gate() — deterministic
                  └─ CheckpointManager.create_checkpoint() — disk write, no LLM
```

### LLM Call Count Per Chapter (real-API, not mock)

| Stage | Min calls | Max calls | Notes |
|-------|-----------|-----------|-------|
| World sim | 0 | 0 | Pure SQLite queries |
| Director | 1 | 3 | 1 initial + up to 2 retries on invalid card |
| Synopsis | 1 | 1 | Cached after first generation |
| Scene write | M | M×2 | M = scenes per chapter (3–4); each scene: 1 flash call, optional 1 pro fallback |
| Expansion | 0 | 2M | Up to 2 rounds × N short scenes |
| Polish | M | M | 1 per scene (polish_router) |
| Review | 1 | 4 | Base + optional re-review + triple-review |
| Fix loop (×2 iter) | 0 | ~12 | Review + patch calls per iteration |
| **Total per chapter** | **~4** | **~30+** | Avg ~8–12 in practice |

**Bottleneck:** Scene write phase dominates — M×2 calls with 4-concurrency ThreadPool. Polish is second.

---

## 2. Decision Points — Agent vs Static Logic

| Decision | Type | Location | Evidence |
|----------|------|----------|----------|
| Scene count (3–4 per chapter) | Static config | `runtime_config.json` → `scene_per_chapter.min/max` | Hard-coded in task card generation |
| Scene model (flash vs pro) | **Dynamic** | `SceneFailoverController.use_pro_for_scene()` | CC30 runtime signal-based switching |
| Review count (1/2/3) | **Dynamic** | `_force_triple24`, `_green24` flags | Score-band + gate-fires determines triple-review |
| Fix loop depth (0–2) | Static limit | `max_chapter_fix_rounds: 2` | Hard cap, not model-decided |
| Scene regeneration on degradation | **Dynamic** | `should_switch_scene_pro()` | 6退化信号触发pro切换 |
| Whole-chapter rewrite | Disabled by default | `allow_whole_chapter_rewrite: false` | Config-gated, off in production |
| Skip polish if on-target | Dynamic | `skip_polish_on_target` | Length check + gate-pass → skip |
| Director cache use | Static with bypass | `_director_cache_hit` | Infra retries use cache; quality replans bypass |
| Scene count mismatch fix | Deterministic | `len(staged) != len(expected_bps)` | Auto-generates missing scenes, no model input |

**Key finding:** The only truly dynamic, model-driven decisions are:
1. Flash→pro scene switching (CC30, signal-based, not LLM-decided)
2. Review count selection (score-band based)
3. Fix loop continuation (score + gate evaluation, deterministic threshold)

**All other decisions are static thresholds or config values.** The current "agents" (Director, Writer, Reviewer) are stateless prompt routers — they receive context and return LLM outputs but make no autonomous decisions about task structure or execution order.

---

## 3. Redundant / Inefficient Call Patterns

### 3.1 Frozen task cards and synopsis — correct caching, but stale risk

```python
# pipeline_orchestrator.py:519-528
if chapter_num in self._frozen_task_cards:
    task_card = self._frozen_task_cards[chapter_num]
# Same for synopsis at line 556-564
```
**Risk:** If a quality replan changes the end_state or timeline constraints, the frozen card still carries the old director output. The `_bypass` flag (`NOVEL_DIRECTOR_BYPASS_CACHE`) exists but is only set by `production_runner` on HALT recovery — not by the per-chapter fix path.

### 3.2 Deterministic gates run twice per chapter

```
Stage 4 post-enforce: _deterministic_quality_gate(novel_text, card)   [line 647]
Stage 5 post-fix:     _deterministic_quality_gate(best_novel, card)   [line 1002]
Stage 6 commit:       _deterministic_quality_gate(final_text, card)   [line 1174]
```
Each runs the same 10+ gate functions (timeline, continuity, scope, repetition, etc.) over nearly identical text. The intermediate results are not cached across runs.

### 3.3 Reviewer call overhead

The review system can call the LLM up to 4 times per chapter:
- 1st review (always)
- 1 re-review if first score < 65 with clean deterministic gates
- 1–2 triple-review calls if score in [81.5, 85.5] band or gate fired

**Evidence:** `pipeline_orchestrator.py:2965-3200` contains `_one_review24()` closure + conditional branching logic.

### 3.4 Expansion loop potential double-calls

```python
# pipeline_orchestrator.py:2708-2730
for _ea in range(2):  # up to 2 expansion rounds
    _todo = [..scenes below 0.85*target..]
    # Each short scene → 1 call_llm() via refine_router
```
A chapter with 3 scenes all under 0.85×target can trigger 6 expansion calls (2 rounds × 3 scenes), plus the original write calls, plus polish calls.

### 3.5 Atmosphere dedup repolish — extra LLM budget

```python
# pipeline_orchestrator.py:867-987
if _atm_hits:
    for hit in _atm_hits:  # max 2 repolish attempts
        _ns = self.writer.generate_scene(...)  # 1 LLM call per hit
        # Then: deterministic gate check + forbidden check (no LLM)
```
Up to 2 additional scene writes post-review, each triggering a full scene LLM call + gate re-evaluation.

---

## 4. Prime-Agent Integration Gap Analysis

### Current state

| Component | Status | Details |
|-----------|--------|---------|
| `agent_api.py:set_kernel_handles()` | **Defined** | Stores `rlm` and `agent_message` handles in `_KERNEL` dict |
| `agent_api.py:generate_scene_unit()` | **Defined** | Wraps `orch.writer.generate_scene()` for single-scene subagent use |
| `agent_api.py:generate_chapter_with_subagents()` | **Defined but dead** | Only called if explicit; NOT used by `generate_chapter()` |
| `PipelineOrchestrator` hot path | **No RLM calls** | Uses `ThreadPoolExecutor` for scene fan-out (line 813) |
| `prime-agent-runtime/src/rlm/` | **Skeleton** | `harness.py`, `skill.py`, `mcp_base.py` exist but no live integration |

### Where the wiring breaks

**File:** `pipeline_orchestrator.py`  
**Line:** 813 (inside `WriterAgent.generate_scenes()`)

```python
# Current: synchronous ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=max_workers, ...) as ex:
    fut2bp = {ex.submit(_do_one, bp): bp for bp in scenes}
```

**Expected (Phase 2 design):**
```python
# Should be: async RLM subagent spawns per scene group
for group in groups:
    handle = await rlm("sub-task")
    await agent_message.send(spec, receiver_role="child", receiver_name=handle.name)
    reply = await agent_message.recv(receiver_role="parent")
```

The `generate_chapter_with_subagents()` function in `agent_api.py:256` implements exactly this pattern — but it is a **separate entry point**, not wired into the standard `generate_chapter()` → `_stage_write()` flow.

### Root cause

`generate_chapter()` → `orch._run_with_retry()` → `generate_single_chapter()` calls `_stage_write()` which calls `writer.generate_full_chapter()` → `writer.generate_scenes()` which hardcodes `ThreadPoolExecutor`. The `_KERNEL["rlm"]` global is never read in this path.

### Minimal fix surface

| File | Change needed |
|------|--------------|
| `pipeline_orchestrator.py:_stage_write()` | Accept async rlm handle; branch on `_KERNEL["rlm"]` presence |
| `agents/writer_agent.py:generate_scenes()` | Add async subagent path parallel to ThreadPool path |
| `agent_api.py:generate_chapter()` | Route through `generate_chapter_with_subagents()` when kernel handles present |

---

## 5. Quality Gate Inventory

| Gate | Type | LLM? | Block? | Location |
|------|------|------|--------|----------|
| `timeline_gate` | Deterministic | No | Soft | `quality/timeline_gate.py` |
| `continuity_gate` | Deterministic | No | Hard | `quality/continuity_gate.py` |
| `scope_gate` | Deterministic | No | Hard | `quality/scope_gate.py` |
| `repetition_detector` | Deterministic | No | Hard | `quality/repetition_detector.py` |
| `forbidden_scanner` | Deterministic | No | Hard | `quality/forbidden_scanner.py` |
| `chinese_gate` | Deterministic | No | Hard | `quality/chinese_gate.py` |
| `latin_leak_gate` | Deterministic | No | Hard | `quality/latin_leak_gate.py` |
| `naming_consistency` | Deterministic | No | Soft | `quality/naming_consistency_gate.py` |
| `scene_failover_controller` | Signal-based | No | Hard (pro switch) | `quality/scene_failover_controller.py` |
| `reviewer_agent` | **LLM** | Yes | Soft→Hard | `agents/reviewer_agent.py:368` |
| `atmosphere_dedup` | Mixed | No+LLM | Soft | `quality/atmosphere_dedup.py` |
| `boundary_guard` | LLM stage2 | Yes | Hard | `pipeline/boundary_guard.py` |
| `outline_coverage` | Deterministic | No | Hard | `quality/outline_coverage_gate.py` |
| `density_gate` | Deterministic | No | Soft | `quality/density_gate.py` |
| `scene_progression_gate` | Deterministic | No | Soft | `quality/scene_progression_gate.py` |

**Total gates:** 15+, of which only 3 use LLM (reviewer, boundary_guard stage2, atmosphere_dedup repolish). Rest are pure Python.

---

## 6. Audit Answers to Required Questions

### Q1: What functions and states does a single chapter pass through?
```
INIT → PLANNING → WORLD_SIM → DIRECTING → SYNOPSIS → WRITE_SCENE → POLISH → REVIEW
  → [PASS→COMMIT] or [FAIL→FIX(loop×2)→REVIEW] or [NEEDS_HUMAN]
```
Each transition is a `state_machine.transition(ChapterPhase.XXX)` call in `pipeline_orchestrator.py`.

### Q2: Which component decides task content, order, and termination?
**Static decision layer:** `PipelineOrchestrator.generate_single_chapter()` — hardcoded phase sequence with config-gated branches (fix loop max 2, review triple threshold, etc.).  
**Dynamic decision layer:** `SceneFailoverController` — decides flash→pro per scene based on real-time degradation signals (no LLM involved in the decision).  
**No agent-level planning:** The chapter plan is generated once by `ChapterDirector` (LLM call) and then frozen. No runtime replanning except on infra failure.

### Q3: Which steps call models directly vs through routing/failover?
- **Direct:** All `call_llm()` and `.chat_completion()` calls go through phase-specific `ModelRouter` instances
- **Routing:** `ModelRouter` handles per-phase model selection from `llm_providers.json` profiles
- **Failover:** `FailoverLLMClient` wraps primary/fallback per phase; `SceneFailoverController` adds per-scene pro-switch logic
- **Retry:** `LLMClient.chat_completion()` has built-in degenerate-content retry (temperature ramp 0.85→1.0); external retry is 3 attempts in `_stage_directing`

### Q4: Is the current "Agent" truly autonomous or static prompts?
**Static prompts with deterministic guards.** Director, Writer, Reviewer are all:
1. Receive a fixed prompt template + context injection
2. Call LLM once (or with retry)
3. Parse structured output
4. Return result

No task decomposition, no sub-task spawning, no self-correction beyond retry loops. The `WriterAgent` does have degradation detection (CC30) but that's signal-based, not reasoning-based.

### Q5: Does Prime-Agent's runtime currently participate in novel production?
**No.** The `_KERNEL` handles in `agent_api.py` are populated by `set_kernel_handles()` but never consumed by the production path. The `generate_chapter_with_subagents()` function exists as an alternative entry point but is not invoked by `generate_chapter()`. The `ThreadPoolExecutor` in `generate_scenes()` is the sole fan-out mechanism.

### Q6: How is context assembled per task?
**Per-chapter, not per-scene optimized:**
- Bible cache loaded once at `PipelineOrchestrator.__init__` (world/character/style bibles)
- Each scene prompt includes: task card, blueprint, synopsis, bible snippet (character+location targeted), perspective block (age-appropriate), pacing constraints, event ledger (recent events to avoid), and covered beats list
- Previous chapter context: last 600 chars of `self.current_novel` passed as `previous_context` to next scene
- **Issue:** All scenes in a chapter share the same synopsis and bible context; no per-scene context pruning

### Q7: Which tasks are redundantly generated or retried?
| Pattern | Frequency | Evidence |
|---------|-----------|----------|
| Deterministic gate re-evaluation | 3× per chapter | Lines 647, 1002, 1174 run same 10+ gates on near-identical text |
| Review re-calls | 1–3× per chapter | Score <65 or in [81.5,85.5] band triggers extra calls |
| Expansion rounds | 0–2× per short scene | Two-round loop for scenes below 0.85×target |
| Atmosphere dedup repolish | 0–2× per chapter | Post-review scene rewrites with gate re-check |
| Director retry on parse failure | Up to 3× | `_stage_directing` max_attempts=3 |
| Fix loop | Up to 2 iterations | Each: review + patch + re-review |

### Q8: What must remain deterministic (no model freedom)?
- World state mutations (character realm changes, faction shifts)
- Timeline anchor validation
- Forbidden term scanning
- Scaffold token leak detection
- Scene beat coverage counting
- Checkpoint hash computation
- Session tree rollback logic

### Q9: What to keep, what to adapt, what to refactor?
| Component | Action | Rationale |
|-----------|--------|-----------|
| `StateDB` (engine/db.py) | **Keep** | Core precision state retrieval, no RAG drift |
| `StateMachine` (core/state_machine.py) | **Keep** | Clean FSM, well-tested |
| `CheckpointManager` | **Keep** | Hash-backed save/restore, works |
| `Quality gates` (30+ modules) | **Keep** | Mostly deterministic, proven |
| `ModelRouter` / `FailoverLLMClient` | **Keep** | Clean abstraction, phase-aware |
| `PipelineOrchestrator` | **Adapt** | Add RLM branch in `_stage_write` and fix loop |
| `WriterAgent.generate_scenes` | **Adapt** | Add async subagent path alongside ThreadPool |
| `ChapterDirector` | **Adapt** | Add self-correction loop (currently single-shot) |
| `ReviewerAgent` | **Refactor** | Consolidate 1–4 call pattern into structured multi-evaluator |
| `Fix loop` | **Refactor** | Replace with agent-decision loop (current is hardcoded 2-iter) |
| `Prime-Agent RLM kernel` | **Build** | Bridge between orchestration and subagent dispatch |

---

## 7. Risk Register

| Risk | Severity | Mitigation |
|------|----------|------------|
| RLM kernel interface unstable (Comm-based, ipykernel-dependent) | High | Wrap in version-gated adapter; degrade to ThreadPool if kernel unavailable |
| Frozen task cards cause stale planning after fix loops | Medium | Add task_card invalidation on fix-loop entry |
| Triple-review + expansion + repolish can exceed budget on single chapter | Medium | Add per-chapter LLM call budget with hard stop |
| `pytest` missing breaks 29/34 tests | High (blocking) | Install pytest before P2/P3 |
| `_KERNEL` dict is module-global, not thread-safe | Low | RLM subagent path is already async-single-threaded; ThreadPool path unaffected |

---

## 8. Recommended Target Architecture (Summary)

```
                    ┌─────────────────────────────────────┐
                    │     PipelineOrchestrator (keep)      │
                    │  FSM + quality gates + commit        │
                    └──────────────┬──────────────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              ▼                    ▼                    ▼
    ┌─────────────────┐  ┌──────────────────┐  ┌─────────────────┐
    │ Phase 1: Current│  │ Phase 2: Prime   │  │ Phase 3: Agent  │
    │ ThreadPool path │  │ RLM subagent     │  │ self-heal loop  │
    │ (kept for fallback│  │ per scene group  │  │ (review-driven  │
    │  when rlm absent)│  │ (new path)       │  │  remediation)   │
    └─────────────────┘  └──────────────────┘  └─────────────────┘
                                   │
                    ┌──────────────┴──────────────┐
                    ▼                             ▼
           ┌─────────────────┐            ┌─────────────────┐
           │ StateDB         │            │ SessionTree     │
           │ (keep)          │            │ (keep)          │
           └─────────────────┘            └─────────────────┘
```

**Next step:** P2 — design the adapter layer connecting `PipelineOrchestrator` to Prime-Agent's RLM kernel.
