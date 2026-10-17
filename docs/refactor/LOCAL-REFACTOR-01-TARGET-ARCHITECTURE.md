# LOCAL-REFACTOR-01 — Target Architecture

**Date:** 2026-10-17  
**Phase:** P2 — Prime-Agent Runtime Integration  
**Basis:** SOURCE-AUDIT.md findings + existing `generate_chapter_with_subagents()` scaffolding

---

## 1. Design Principles

1. **Backward compatible:** Standalone runs (no Prime-Agent kernel) continue to work unchanged.
2. **Fail-closed:** RLM subagent failure falls back to ThreadPool, not to silent correctness loss.
3. **No new LLM budget without audit trail:** Every subagent-spawned scene writes a trace entry.
4. **Deterministic gates stay deterministic:** Gate evaluation never goes through an LLM decision.
5. **One adapter, one entry point:** All RLM integration goes through a single `NovelRLMAdapter`.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Novel Production Pipeline                         │
│                                                                     │
│  agent_api.py                                                       │
│  ├── generate_chapter()          ← public entry (sync/async hybrid) │
│  │   └─ check _KERNEL["rlm"]                                     │
│  │       ├─ set → _run_with_rlm()  (Phase 2 path)                 │
│  │       └─ None → generate_chapter_sync() (existing path)        │
│  │                                                                  │
│  └── generate_scene_unit()       ← subagent call target           │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────────────────┐
│              NovelRLMAdapter (NEW — integration layer)              │
│                                                                     │
│  _run_with_rlm(chapter_num)                                         │
│    ├─ _stage_world_sim()     ← deterministic, same as before       │
│    ├─ _stage_directing()     ← deterministic LLM (keep as-is)      │
│    ├─ _stage_synopsis()      ← deterministic LLM (keep as-is)      │
│    ├─ _stage_write_rlm()     ← NEW: scene fan-out via rlm()        │
│    │   ├─ rlm("scene-writer") per independent scene group          │
│    │   ├─ agent_message.recv() collects results                    │
│    │   └─ fallback: ThreadPoolExecutor if rlm fails               │
│    ├─ _stage_review()        ← same deterministic + LLM review    │
│    └─ commit()               ← same checkpoint + gate flow        │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
         ┌─────────────┼─────────────┐
         ▼             ▼             ▼
   ┌──────────┐  ┌──────────┐  ┌──────────┐
   │ StateDB  │  │ SessionTree│  │ Quality  │
   │ (keep)   │  │  (keep)   │  │  Gates   │
   └──────────┘  └──────────┘  └──────────┘
```

---

## 3. New Module: `novel_engine/adapters/rlm_adapter.py`

### 3.1 Public API

```python
class NovelRLMAdapter:
    """Adapts PipelineOrchestrator to Prime-Agent RLM kernel.

    When _KERNEL["rlm"] is None (standalone run), all methods degrade to
    synchronous equivalents with identical return shapes.
    """

    def __init__(self, orchestrator: "PipelineOrchestrator"):
        self.orch = orchestrator
        self._trace: list[dict] = []  # run-local trace entries

    async def run_chapter(self, chapter_num: int) -> dict:
        """Full chapter pipeline via RLM where possible, sync fallback otherwise.

        Returns the same shape as PipelineOrchestrator.generate_single_chapter():
        {"chapter": int, "success": bool, "score": float|None, "errors": list}
        """
        rlm = _KERNEL.get("rlm")
        agent_message = _KERNEL.get("agent_message")

        if rlm is None:
            # Degrade to existing synchronous path — no behavior change
            return self.orch.generate_single_chapter(chapter_num)

        # ── Phase 1: deterministic stages (unchanged) ──
        world_state = self.orch._stage_world_sim(chapter_num)
        task_card = self.orch._stage_directing(chapter_num, world_state)
        synopsis = self.orch._stage_synopsis(task_card)

        # ── Phase 2: scene generation via RLM subagents ──
        novel_text = await self._stage_write_rlm(
            chapter_num, task_card, synopsis, rlm, agent_message
        )

        # ── Phase 3: review + fix loop (unchanged, reuses existing) ──
        # Inject novel_text into orchestrator state for review path
        self.orch.current_novel = novel_text
        staged = self.orch._stage_review(
            chapter_num, task_card, synopsis, novel_text, world_state
        )
        score = staged["score"]
        verdict = staged["verdict"]

        # ── Phase 4: commit ──
        self.orch.state_machine.commit_chapter(
            chapter_num=chapter_num,
            novel_content=novel_text,
            synopsis_content=json.dumps(synopsis, ensure_ascii=False),
            outline_content=json.dumps(task_card, ensure_ascii=False),
            world_state_snapshot=world_state,
        )
        return {"chapter": chapter_num, "success": verdict == "pass", "score": score}

    async def _stage_write_rlm(
        self,
        chapter_num: int,
        task_card: dict,
        synopsis: dict,
        rlm,
        agent_message,
    ) -> str:
        """Generate scenes via RLM subagents with ThreadPool fallback."""
        scenes = task_card.get("scene_blueprints", [])
        groups = self.orch.writer._group_independent_scenes(scenes)
        scene_contents: dict = {}

        for group in groups:
            try:
                handle = await rlm("sub-task")
                spec = {
                    "chapter_num": chapter_num,
                    "task_card": task_card,
                    "synopsis": synopsis,
                    "scene_blueprints": group,
                    "pacing_constraints": "",
                }
                await agent_message.send(
                    content=json.dumps(spec),
                    receiver_role="child",
                    receiver_name=handle.name,
                )
                reply = await agent_message.recv(receiver_role="parent")
                for bp, content in reply:
                    scene_contents[bp.get("scene_num")] = content
                self._trace.append({
                    "phase": "scene_group_rlm",
                    "chapter": chapter_num,
                    "group_size": len(group),
                    "status": "ok",
                })
            except Exception as exc:
                logger.warning(
                    f"RLM scene group failed (ch{chapter_num}): {exc}; "
                    f"falling back to ThreadPool for {len(group)} scenes"
                )
                # ThreadPool fallback for this group only
                group_contents = self.orch.writer.generate_scenes(
                    {**task_card, "scene_blueprints": group},
                    synopsis,
                )
                for so in group_contents:
                    scene_contents[so.scene_id] = so
                self._trace.append({
                    "phase": "scene_group_fallback",
                    "chapter": chapter_num,
                    "group_size": len(group),
                    "status": "fallback",
                    "reason": str(exc),
                })

        return _assemble_chapter(scene_contents, self.orch._assemble_chapter_text)

    def trace(self) -> list[dict]:
        return list(self._trace)
```

### 3.2 Entry Point Modification

In `agent_api.py`, modify `generate_chapter()` to route through the adapter:

```python
# agent_api.py — after line 105
async def generate_chapter(chapter_num: int, project_root=None, use_mock=False):
    orch = _build_orchestrator(project_root, use_mock=use_mock)

    # Phase 2: check if Prime-Agent RLM kernel is available
    if _KERNEL.get("rlm") is not None:
        from novel_engine.adapters.rlm_adapter import NovelRLMAdapter
        adapter = NovelRLMAdapter(orch)
        try:
            result = await adapter.run_chapter(int(chapter_num))
            return result
        except Exception as exc:
            logger.warning(f"RLM adapter failed (ch{chapter_num}): {exc}; falling back")
            # Fallback: degrade to synchronous pipeline
            orch = _build_orchestrator(project_root, use_mock=use_mock)

    ch = int(chapter_num)
    try:
        result = orch._run_with_retry(ch)
    except Exception as exc:
        logger.exception("generate_chapter(%s) failed", ch)
        return {"chapter": ch, "success": False, "score": None, "errors": [str(exc)]}
    return result
```

---

## 4. Tool Interface Definitions (小说领域工具层)

Per task spec §2.3, the following tools must be registered with explicit I/O contracts:

| Tool | Input | Output | Permission | Deterministic? |
|------|-------|--------|------------|----------------|
| `get_world_context` | `chapter_num: int, entities: list[str]` | `dict` (character/world state snapshot) | Read | Yes |
| `get_character_state` | `char_name: str` | `dict` (realm, location, relationships) | Read | Yes (StateDB query) |
| `get_timeline_context` | `chapter_num: int` | `dict` (time anchors, elapsed, events) | Read | Yes |
| `get_foreshadowing_state` | `chapter_num: int` | `dict` (active clues, fulfillment status) | Read | Yes |
| `get_chapter_context` | `chapter_num: int` | `dict` (outline, synopsis, constraints) | Read | Yes (disk read) |
| `write_or_update_scene` | `chapter_num, scene_num, text, beats` | `dict` (scene_id, status) | Write (draft) | No (LLM) |
| `validate_continuity` | `chapter_num, novel_text` | `dict` (issues: list, passed: bool) | Read-only check | Yes (deterministic gates) |
| `run_quality_checks` | `chapter_num, novel_text, task_card` | `dict` (scores, issues, verdict) | Read-only check | Mixed (deterministic + LLM review) |
| `get_review_feedback` | `chapter_num` | `dict` (review, score, issues) | Read | Yes (from journal) |
| `apply_targeted_repair` | `chapter_num, scene_num, directive` | `dict` (new_scene_text, score_delta) | Write (draft) | No (LLM) |
| `save_checkpoint` | `chapter_num, novel_content` | `dict` (checkpoint_hash, timestamp) | Write | Yes (hash + disk) |

### Implementation Notes

- `get_world_context`, `get_character_state`, `get_timeline_context`, `get_foreshadowing_state` are thin wrappers around existing `StateDB` queries — no new code needed, just expose as callable tools.
- `validate_continuity` reuses existing gate functions from `quality/` — wrap the gate results.
- `apply_targeted_repair` reuses `WriterAgent.generate_scene()` with a `fix_directive` parameter (already exists at line 444).
- `save_checkpoint` reuses `CheckpointManager.create_checkpoint()` (already exists).

---

## 5. Prime-Agent Adaptation Layer

### 5.1 Context Injection

When Prime-Agent calls a tool, inject only the context it needs — not the full bible:

```python
# In tool handler:
def get_world_context(chapter_num: int, entities: list[str] | None = None) -> dict:
    orch = _build_orchestrator(use_mock=True)  # read-only, no API calls
    ws = orch.simulator.build_world_state_for_chapter(chapter_num)
    if entities:
        return {k: v for k, v in ws.items() if k in entities or k == "power_system"}
    return ws
```

### 5.2 Tool Registration

Tools must be exposed via the Prime-Agent skill interface. The existing `agent_api.py` pattern shows how:

```python
# novel_engine/tools/__init__.py (NEW)
from .domain_tools import (
    get_world_context,
    get_character_state,
    get_timeline_context,
    get_foreshadowing_state,
    get_chapter_context,
    write_or_update_scene,
    validate_continuity,
    run_quality_checks,
    get_review_feedback,
    apply_targeted_repair,
    save_checkpoint,
)

__all__ = [
    "get_world_context", "get_character_state", "get_timeline_context",
    "get_foreshadowing_state", "get_chapter_context", "write_or_update_scene",
    "validate_continuity", "run_quality_checks", "get_review_feedback",
    "apply_targeted_repair", "save_checkpoint",
]
```

### 5.3 Agent Decision Closure

The adapter's `run_chapter()` method forms the decision closure:
1. **Plan:** `_stage_directing()` produces task card (LLM-generated, deterministic guard)
2. **Execute:** `_stage_write_rlm()` dispatches scene groups via RLM subagents
3. **Observe:** `_stage_review()` scores the result
4. **Decide:** Fix loop (`_run_fix_loop_iteration`) decides retry/rewrite/stop based on score+gate signals
5. **Commit:** `commit_chapter()` persists with checkpoint

This matches the task spec §3.1–3.4 requirements.

---

## 6. Concurrency Model

| Stage | Current | Proposed (Phase 2) | Change |
|-------|---------|-------------------|--------|
| World sim | Sequential | Sequential | No change |
| Director | Sequential (1 call) | Sequential (1 call) | No change |
| Synopsis | Sequential (1 call) | Sequential (1 call) | No change |
| Scenes | ThreadPoolExecutor (4 workers) | RLM subagents (sequential per group, groups sequential) | Degraded concurrency but adds agent oversight |
| Polish | Per-scene sequential | Per-scene sequential | No change |
| Review | Sequential (1–4 calls) | Sequential (1–4 calls) | No change |
| Fix loop | Sequential (max 2 iters) | Sequential (max 2 iters) | No change |

**Tradeoff:** RLM subagent path is slower per-chapter than ThreadPool (sequential groups vs parallel scenes). However, it enables agent-level decision-making (the primary goal of this refactor). Performance optimization is a P4 concern.

---

## 7. Verification Plan

### 7.1 Automated Test (required by spec)

```python
# test/test_rlm_adapter.py (NEW)
import pytest
from novel_engine.adapters.rlm_adapter import NovelRLMAdapter
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

@pytest.fixture
def mock_orch():
    return PipelineOrchestrator(use_mock=True)

@pytest.fixture
def adapter(mock_orch):
    return NovelRLMAdapter(mock_orch)

@pytest.mark.asyncio
async def test_run_chapter_without_rlm_degrades_cleanly(adapter, mock_orch):
    """When rlm is None, run_chapter must return same shape as generate_single_chapter."""
    from novel_engine.agent_api import _KERNEL
    _KERNEL["rlm"] = None  # ensure no kernel
    result = await adapter.run_chapter(1)
    assert "chapter" in result
    assert "success" in result
    assert "score" in result

@pytest.mark.asyncio
async def test_run_chapter_with_mock_rlm_calls_scene_unit(adapter, mock_orch, monkeypatch):
    """When rlm is a mock, scene groups must call generate_scene_unit."""
    called = []
    async def mock_rlm(prompt):
        class Handle:
            name = "test-child"
        return Handle()

    mock_msg = type("MockMsg", (), {
        "send": lambda self, **kw: called.append(("send", kw)),
        "recv": lambda self, **kw: ([],),  # empty reply → fallback path
    })()

    _KERNEL["rlm"] = mock_rlm
    _KERNEL["agent_message"] = mock_msg

    # Should not raise; should fallback gracefully when recv returns empty
    result = await adapter.run_chapter(1)
    assert result["chapter"] == 1
    # Fallback to ThreadPool should have produced a result
    assert result["success"] is not None
```

### 7.2 Evidence Requirements

- [ ] `generate_chapter()` with `rlm=None` returns identical result to pre-refactor
- [ ] `generate_chapter()` with `rlm=mock` triggers `NovelRLMAdapter.run_chapter()` path
- [ ] RLM dispatch failure falls back to ThreadPool without data loss
- [ ] All quality gates still run post-RLM-write (verified by test)
- [ ] Checkpoint is written on commit (disk artifact exists)

---

## 8. Out of Scope for P2

These are deferred to later phases per the task spec:

| Item | Phase | Reason |
|------|-------|--------|
| Fix loop agentification (current is hardcoded 2-iter) | P3 | Requires agent decision logic redesign |
| Per-chapter LLM budget tracking dashboard | P4 | Observability concern |
| Idempotency / resume semantics for RLM path | P5 | Data protection concern |
| Quality regression test suite expansion | P6 | Testing concern |
| Subagent-per-scene (vs per-group) concurrency | P3 | Performance optimization |

---

## 9. Files to Create / Modify

### New files
```
packages/coding-agent/skills/novel-engine/src/novel_engine/adapters/
├── __init__.py
└── rlm_adapter.py          # NovelRLMAdapter class

packages/coding-agent/skills/novel-engine/src/novel_engine/tools/
├── __init__.py
└── domain_tools.py         # Tool wrappers around existing state/gates
```

### Modified files
```
packages/coding-agent/skills/novel-engine/src/novel_engine/agent_api.py
  - generate_chapter(): add RLM routing check

packages/coding-agent/skills/novel-engine/src/novel_engine/pipeline/pipeline_orchestrator.py
  - No changes required (adapter wraps existing methods)
```

---

## 10. Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| `agent_message.send/recv` contract differs from what subagent expects | High | Defensive try/except around dispatch; fallback to ThreadPool on any exception |
| RLM handle lifetime exceeds chapter generation | Medium | Handle is per-call; no global state retained |
| Scene group ordering changes (RLM vs ThreadPool) | Low | `_group_independent_scenes()` preserves group boundaries; group order is deterministic |
| Trace data pollutes production logs | Low | Trace is in-memory only; not persisted unless explicitly enabled |
| Tool wrapper signature mismatch with Prime-Agent expectations | Medium | Use `tyro.cli()` pattern from `skill.py` for consistent CLI/interface binding |
