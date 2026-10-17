# LOCAL-REFACTOR-01 — Baseline Report

**Generated:** 2026-10-17  
**Project root:** `D:/projectAGI/Prime-Agent-Novel-Engine-feat-stability-quality`  
**Novel engine root:** `packages/coding-agent/skills/novel-engine/src/novel_engine/`

---

## 1. Repository State

| Item | Value |
|------|-------|
| Git state | Fresh init (no commits yet) |
| Branch | N/A (empty repo) |
| Uncommitted files | All project files (untracked) |
| Python | 3.13.0 |
| Node.js | v24.14.1 |
| npm | 11.11.0 |
| Poetry | Not installed (pyproject.toml uses Poetry; tests run via `python -m unittest` directly) |

### Protected Data Directories

| Directory | Purpose | Status |
|-----------|---------|--------|
| `chapters/` | Generated chapter .txt files | Empty |
| `runtime/` | Checkpoint.json, resume_state.json | Empty (no prior production runs) |
| `bible/` | World, character, style bibles | Present with content |
| `config/planning/` | Mid-outlines (ch1-616), character_registry, decision_rules, foreshadow_anchors | Present |
| `config/` | Quality thresholds, cost sandbox, llm_providers, forbidden lists | Present |
| `memory/` | Short-term / long-term / world_state memory | Present but empty of generated data |
| `audit/` | Audit reports | `needs_human_review.json` exists |
| `timeline/` | Timeline anchors | `timeline_anchors.json` present |

---

## 2. Code Inventory

| Metric | Value |
|--------|-------|
| Total Python files | 254 |
| Non-test Python files | 105 |
| Total source lines | 6893 |
| Largest files | pipeline_orchestrator.py (5219), chapter_director.py (1656), gates.py (1620), writer_agent.py (1135), llm_client.py (1086) |

### Module Tree

```
novel_engine/
├── agent_api.py             # Prime-Agent IPython kernel entry point
├── agents/                  # Agent implementations
│   ├── chapter_director.py  # Task card dispatcher (1656 LOC)
│   ├── writer_agent.py      # Multi-scene generator (1135 LOC)
│   ├── reviewer_agent.py    # Multi-dimension audit (535 LOC)
│   ├── world_simulator.py
│   └── scene_schema.py
├── core/
│   ├── llm_client.py        # Mock + HTTPX LLM client (1086 LOC)
│   ├── model_router.py      # Phase-level provider routing
│   ├── llm_failover.py      # Primary/fallback controller
│   ├── state_machine.py     # ChapterPhase FSM
│   ├── checkpoint.py        # Hash-backed save/restore
│   └── quality_policy.py    # Threshold loading
├── engine/
│   ├── db.py                # StateDB (SQLite code-query)
│   ├── session.py           # Durable session tree
│   └── patcher.py           # Incremental scene patching
├── pipeline/
│   ├── production_runner.py # Full batch runner (920 LOC)
│   ├── pipeline_orchestrator.py  # Core pipeline (5219 LOC)
│   └── chapter_status.py    # COMMITTED/HALTED tracking
├── quality/                 # 30+ gate modules
│   ├── scene_model_router.py   # Degradation → pro switch logic
│   ├── scene_failover_controller.py  # Per-scene budget controller
│   ├── continuity_gate.py
│   ├── timeline_gate.py
│   └── (25 more gates)
├── bible/                   # Author intent, character, style, world, ending
├── config/
│   ├── runtime_config.json  # Master runtime config
│   ├── llm_providers.json   # Multi-profile provider config
│   └── quality_thresholds.json
└── tests/                   # 50+ test files (~30 pass, 29 pytest-import errors)
```

---

## 3. Environment & Dependencies

### Existing Python dependencies (installed)
| Package | Status |
|---------|--------|
| httpx | Required by llm_client.py |
| jieba | Required (Chinese text processing) |
| json-repair | Required |
| pytest | **Missing** — 29 test failures due to `ModuleNotFoundError` |
| ipykernel | Required for Prime-Agent kernel integration |
| nest-asyncio | Required by prime-agent-runtime |

### LLM Configuration
- **Active profile:** `agnes_30_token` (llm_providers.json)
- **API base:** `https://apihub.agnes-ai.com`
- **Key env var:** `AGNES_TOKEN_API_KEY`
- **Primary model:** `agnes-3.0-flash`
- **Polish/Review fallback:** `agnes-2.5-pro`
- **All phases default to flash**; polish/review/refine prefer pro first

---

## 4. Test Baseline

```
Ran 34 tests in 1.644s
FAILED (errors=29)
```

- 5 tests in `test_novel_engine.py` pass (state_db, session_tree, phase_cache, canary)
- 29 tests fail at import time due to missing `pytest` — not implementation bugs
- Mock-only tests pass (no API calls required)
- **No real-API integration test has been run against live endpoints**

### Key passing tests
- `test_state_db` — SQLite code-query layer
- `test_session_tree` — Durable session branching
- `test_phase_cache` — Prompt cache key generation

---

## 5. Prime-Agent Integration Status

### Current integration points

| File | Mechanism | Status |
|------|-----------|--------|
| `agent_api.py` | `set_kernel_handles(rlm, agent_message)` | **Defined but not wired into pipeline** |
| `agent_api.py` | `generate_chapter_with_subagents()` | Exists, branches on `_KERNEL["rlm"]` |
| `agent_api.py` | `generate_scene_unit()` | Subagent-per-scene entry point |
| `prime-agent-runtime/src/rlm/` | Kernel shim (`harness.py`, `skill.py`, `mcp_base.py`) | Skeleton; IPython-comm based |

### Critical gap
The pipeline orchestrator (`pipeline_orchestrator.py`) uses a synchronous `ThreadPoolExecutor` for scene fan-out — **it does not call `rlm("sub-task")`**. The `_KERNEL` handles are set but never consulted in the hot path. The `generate_chapter_with_subagents` path in `agent_api.py` is a separate entry point, not used by the standard `generate_chapter()` → `PipelineOrchestrator` flow.

**Answer to audit question #5:** Prime-Agent's RLM kernel does **not** participate in the current novel production chain. The `agent_api.py` hooks are defined but remain dead code unless called directly by an external agent session.

---

## 6. Production Cost Model

| Phase | Est. cost/chapter |
|-------|-------------------|
| Director | $0.12 |
| Synopsis | $0.06 |
| Writer (per scene) | $0.04 × 3–4 scenes |
| Review | $0.05 |
| **Per chapter total** | ~$0.40–$0.56 |
| **Full 3800-chapter estimate** | UNKNOWN (budget sandbox caps at $400) |

- Cost sandbox total budget: $500 (production: $400)
- Daily max: $50
- `cost_sandbox.json` tracks per-call metrics but production run data is absent (no prior runs)

---

## 7. Blockers Identified

| # | Blocker | Severity |
|---|---------|----------|
| B1 | `pytest` not installed — 29/34 tests fail at import | High |
| B2 | Prime-Agent RLM kernel not wired into `PipelineOrchestrator` hot path | High |
| B3 | `AGNES_TOKEN_API_KEY` env var required but not verified present | Medium |
| B4 | No prior production checkpoint data (empty chapters/, runtime/) | Low (no data to lose) |
| B5 | `runtime_config.json` and `llm_providers.json` specify `agnes-3.0-flash` — must confirm this model exists on the target endpoint | Medium |

---

## 8. Baseline Artifacts Protected

- `bible/*.md` — world/character/style bibles (read-only reference)
- `config/planning/*.json` — outlines up to ch616 (read-only reference)
- `config/quality_thresholds.json` — quality gates config
- `config/llm_providers.json` — provider routing
- `config/runtime_config.json` — master runtime config
- `timeline/timeline_anchors.json` — timeline state

---

## 9. Next Step

Proceed to **P1: Source Audit** — trace the actual call chain from `generate_chapter()` through `PipelineOrchestrator` to identify exact decision points, redundant calls, and integration gaps.
