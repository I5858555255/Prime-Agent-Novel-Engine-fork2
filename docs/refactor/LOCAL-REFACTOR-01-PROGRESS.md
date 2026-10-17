# LOCAL-REFACTOR-01 — Progress Report (Session 1)

**Date:** 2026-10-17  
**Status:** P0-P2 complete; P3-P6 deferred per task spec scope

---

## Completed Work

### P0: Baseline ✅
`docs/refactor/LOCAL-REFACTOR-01-BASELINE.md`

- Repository initialized (no prior commits)
- 254 Python files, 6893 LOC in novel-engine
- Environment: Python 3.13, Node v24, npm 11.11
- 5 unit tests pass; 29 fail due to missing `pytest` (pre-existing)
- No prior production checkpoint data (safe to modify)
- Protected data: bible/, config/planning/, config/llm_providers.json

### P1: Source Audit ✅
`docs/refactor/LOCAL-REFACTOR-01-SOURCE-AUDIT.md`

**Key findings:**
- Full call chain traced: `generate_chapter() → PipelineOrchestrator → 6 stages → commit`
- LLM calls per chapter: ~4 (min) to ~30+ (max with fix loops + atmosphere dedup)
- **Critical gap identified:** Prime-Agent RLM kernel (`_KERNEL`) defined but **never consumed** by the production path
- `ThreadPoolExecutor` hardcoded in `WriterAgent.generate_scenes()` — no RLM branching
- Only 3 LLM-using gates (reviewer, boundary_guard stage2, atmosphere_dedup); rest deterministic
- Review overhead: 1-4 LLM calls/chapter (re-review + triple-review logic)
- Deterministic gates run 3× per chapter redundantly (lines 647, 1002, 1174)

### P2: Architecture & Integration ✅
`docs/refactor/LOCAL-REFACTOR-01-TARGET-ARCHITECTURE.md`

**Delivered:**

| Component | File | Lines | Status |
|-----------|------|-------|--------|
| `NovelRLMAdapter` | `adapters/rlm_adapter.py` | 186 | ✅ With failover |
| Domain tools (11) | `tools/domain_tools.py` | 303 | ✅ All wrappers |
| Agent routing | `agent_api.py` | modified | ✅ Conditional branch |
| Adapter tests | `tests/test_rlm_adapter.py` | 194 | ✅ 11 passing |

**Architecture:**
```
generate_chapter()
  ├─ _KERNEL["rlm"] present → NovelRLMAdapter.run_chapter()
  │    ├─ world_sim → directing → synopsis (deterministic, same as before)
  │    ├─ scene write via RLM subagents (NEW)
  │    │    └─ per-group dispatch, per-group ThreadPool fallback
  │    ├─ review (same)
  │    └─ commit (same)
  └─ _KERNEL["rlm"] absent → existing PipelineOrchestrator (unchanged)
```

**Test results:**
- New: 11/11 adapter tests pass
- Existing: 5/5 original unit tests pass (no regression)
- 29 pytest-import failures pre-existing (unrelated)

---

## P3–P6: Status

| Phase | Description | Status | Next Session |
|-------|-------------|--------|--------------|
| P3 | Agent decision loop in fix loop | ⏸ Deferred | Replace hardcoded 2-iter fix loop with agent-driven decisions using `run_quality_checks` tool |
| P4 | Observability / trace per run | ⏸ Deferred | Add `trace` output from adapter to production log; add `run_id` to checkpoint |
| P5 | Checkpoint idempotency & recovery | ⏸ Deferred | Verify existing `CheckpointManager` handles restart; add resume test |
| P6 | Quality regression test set | ⏸ Deferred | Expand test coverage for continuity gates, cross-chapter consistency |

**P3 is the next high-value item.** The fix loop in `_dispatch_remediation()` (pipeline_orchestrator.py:4092-4308) is the single largest opportunity for real agent decision-making. Currently it:
- Hardcodes `max_fix = 2` iterations
- Decides patch vs rewrite via deterministic thresholds
- Never asks "what should I do next?" — only follows pre-programmed branches

A P3 implementation would replace `_dispatch_remediation`'s internal logic with a call to `run_quality_checks(chapter_num, current_text, task_card, synopsis, world_state)` → parse issues → decide repair strategy via agent reasoning.

---

## Artifacts Produced

```
docs/refactor/
├── LOCAL-REFACTOR-01-BASELINE.md       (188 lines)
├── LOCAL-REFACTOR-01-SOURCE-AUDIT.md   (335 lines)
└── LOCAL-REFACTOR-01-TARGET-ARCHITECTURE.md (421 lines)

packages/coding-agent/skills/novel-engine/src/novel_engine/
├── adapters/
│   ├── __init__.py
│   └── rlm_adapter.py                  (NEW: 186 lines)
├── tools/
│   ├── __init__.py                     (NEW: 27 lines)
│   └── domain_tools.py                 (NEW: 303 lines)
├── agent_api.py                        (MODIFIED: +33 lines)
└── tests/
    └── test_rlm_adapter.py             (NEW: 194 lines)
```

**Commit:** `97c6cd4` on `master`
