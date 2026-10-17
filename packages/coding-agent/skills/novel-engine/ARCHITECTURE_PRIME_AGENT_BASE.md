# Novel-Engine × Prime-Agent — §四 Architecture Spec (Literal Secondary Development)

**Status:** Design approved (2026-08-28). Spike passed. NOT yet implemented.
**Scope:** Implement plan §四 "基于 Prime-Agent 底座二次开发的 Novel-Engine" by running novel-engine *inside* the Prime-Agent kernel/RLM instead of as a standalone `production_runner.py`.

---

## 0. Spike evidence (feasibility confirmed)

| Gate | Result |
|------|--------|
| `npm install` (workspaces) | ✅ 352 pkgs (~1 min) |
| Full build `tui→ai→agent→coding-agent` | ✅ EXIT 0, all 4 `dist/` built |
| Compiled agent runs | ✅ `node packages/coding-agent/dist/cli.js --help` works; exposes `--skill`, `--goal`, `--mode daemon/rpc/acp` |
| Persistent IPython kernel | ✅ `ipykernel 7.3.0` + `pyzmq 27.2.0` installed & launchable |
| novel-engine skill format | ✅ Already a Python skill (see §3) |

**Conclusion: GO.** Prime-Agent builds and runs in this environment; the kernel and skill system are present.

---

## 1. Goal

Replace the standalone orchestration (`production_runner.py` → `pipeline_orchestrator`) with Prime-Agent's
agent loop, gaining §四's three pillars:
1. **Deterministic State DSL** — already implemented (SQLite `StateDB`, `query_active_foreshadow`). Keep, run inside kernel.
2. **RLM recursive subagents + Durable Branching Session** — spawn per-scene/per-character subagents; branch-compare candidate chapters and keep the best.
3. **Code-level Incremental Patch self-healing** — critic emits a code/state patch, not a natural-language nudge.

---

## 2. Prime-Agent primitives (verified, `packages/coding-agent/src/core`)

- **Skills**: `SkillKind = "markdown" | "python"`. Python skill = `SKILL.md` (frontmatter `name`, `description`, `disable-model-invocation?`) + `pyproject.toml` + `src/<module>/`. Metadata `SkillPythonMetadata { importName, packagePath, pyprojectPath }` (`skills.ts:73-105`).
- **RLM call contract** (`rlm.ts:33`, `refinement.ts:458`):
  - Python skills are **pre-imported modules** → agent calls `await <skill>.<function>(...)`.
  - Subagents: `handle = await rlm('sub-task')` → returns child handle immediately; results arrive only via `await agent_message.send(..., receiver_role='parent')` or files. Never as an `rlm()` return value.
  - `await rlm.list_subagents()` recovers handles.
- **Durable sessions**: `--goal <objective>` seeds a persistent root session; `--fork`/`--resume` give branching/rollback — maps to §四 "Durable Branching Session".
- **Kernel**: persistent IPython tool across turns → durable Python state (our `StateDB` survives across agent turns without re-spawning processes).
- **Misc**: prompt caching, web console (observability, optional).

---

## 3. Current state of novel-engine skill

Path: `packages/coding-agent/skills/novel-engine/`
- `SKILL.md` ✅, `pyproject.toml` ✅, `src/novel_engine/**` ✅ — **already a Prime-Agent Python skill**, bundled into `dist/skills` at build time.
- Today it is driven **standalone** via `src/novel_engine/pipeline/production_runner.py --real` (PID 29244 live run), NOT through the agent loop.
- Self-contained: imports only `novel_engine.*` + 3rd-party (httpx/openai/etc.); **no** import of `pi-*`/parent packages. This isolation is preserved.

**Key realization:** the "secondary development" is mostly *wiring*, not a rewrite. novel-engine already lives in the right place; we change *who drives it*.

---

## 4. Target architecture

```
                         Prime-Agent (coding-agent/dist/cli.js)
                         ─────────────────────────────────────
  user ── --goal "write ch.1..3800" ─▶  ROOT agent (orchestrator)
                                         │  model = AGNES (orchestrator)
                                         │  persistent IPython kernel
                                         │
                                         ├─ calls  await novel_engine.*        (skill, pre-imported)
                                         │
                                         ├─ RLM subagent A: scene 1   ─┐
                                         ├─ RLM subagent B: scene 2    │  await rlm('sub-task')
                                         ├─ RLM subagent C: character  │  → agent_message back
                                         │                            ┘
                                         ├─ BRANCH: fork session → 2 outline variants
                                         │            → review model scores → keep best
                                         │
                                         └─ self-heal: critic emits patch → apply → re-run

  novel_engine skill internals (inside kernel):
    StateDB (SQLite, deterministic DSL)   ← pillar 1 (KEEP)
    generate / review / self_heal          ← pillar 3 (code-level patch)
    scene_fanout → rlm subagents          ← pillar 2 (NEW)
    branch_compare → durable session fork ← pillar 2 (NEW)
```

### 4.1 Pillar 1 — Deterministic State DSL (KEEP + relocate into kernel)
- Keep `StateDB`, `query_active_foreshadow`, character/world state, 7-layer context builder.
- Run them inside the agent's persistent kernel (no more `subprocess` + in-memory state round-trips). State persists across turns natively.
- SQLite file remains the durable backing store (survives restarts, enables `SessionTree` rollback).

### 4.2 Pillar 2 — RLM recursive subagents + Durable Branching (NEW)
- Replace `ThreadPoolExecutor(max_workers=4)` scene fan-out in `pipeline_orchestrator` with `await rlm('sub-task')` per scene/character; children return via `agent_message`.
- The orchestrator (root agent) holds consistency: gathers subagent outputs, reconciles into chapter, checks `query_active_foreshadow`.
- **Branch comparison**: for outline/chapters where divergence matters, fork the session (`--fork`), generate N variants in parallel subagents, score with the review model, keep the winning branch (§四 "best-branch selection").

### 4.3 Pillar 3 — Code-level Incremental Patch self-healing (UPGRADE)
- Current `self_heal` nudges via natural-language re-prompt (D1 forbidden-element gate, D2 `_ensure_chinese`).
- New: critic returns a **structured patch** (edit to a scene/state JSON, or a diff to engine code) applied via a patch applier; re-run only the affected unit. Enables true incremental fix without full-chapter regen.

### 4.4 Orchestration (REPLACE)
- Delete reliance on `production_runner.py` as the driver. New entry: `prime-agent --goal "write chapters 1..3800" --skill novel-engine --provider agnes`.
- novel-engine exposes explicit skill functions (§5) the root agent calls per chapter.
- `min_chapter_score` gate stays (config-driven), now enforced by the orchestrator before branch-commit.

---

## 5. Skill interface contract (functions to expose)

novel-engine becomes callable as `await novel_engine.<fn>(...)` inside the kernel:
- `await novel_engine.init_state(config_path)` — load `runtime_config.json`, open `StateDB`.
- `await novel_engine.generate_chapter(n)` — run §4.2 generation (RLM fan-out) → draft.
- `await novel_engine.review_chapter(n)` → `{score, verdict, issues}`.
- `await novel_engine.self_heal(n, issues)` → applies §4.3 patch, returns new score.
- `await novel_engine.branch_compare(n, variants=2)` → best branch id.
- `await novel_engine.commit(n)` — finalize, update `StateDB`, emit chapter file.

These are thin wrappers over the existing `pipeline_orchestrator` logic, restructured for agent-callable units.

---

## 6. Model topology (per approved decision)

| Role | Model | Config |
|------|-------|--------|
| **Orchestrator / RLM** | **Agnes** (review model) | `AGNES_API_KEY`, base `apihub.agnes-ai.com` — register as a Prime-Agent provider (OpenAI-compatible base URL override). |
| **Generation** | DeepSeek-V3.2 (ZLEAP) | unchanged; called *inside* the kernel by novel_engine. |
| **Review / scoring** | Agnes | unchanged. |

Open task: confirm Prime-Agent can take Agnes as an OpenAI-compatible provider (base-URL + key). If Agnes lacks an OpenAI-compatible endpoint, fall back to the `openai` provider shape or add a thin proxy.

---

## 7. Implementation phases (maps to §四's 4 phases / ~12 weeks)

**Phase 1 — Skill entrypoints (week 1-2).** Add §5 functions to `novel_engine` as agent-callable units; keep standalone path working (dual-mode). Verify via `prime-agent --skill novel-engine --print "init_state + generate_chapter(1)"` in kernel.

**Phase 2 — RLM fan-out (week 3-5).** Replace `ThreadPoolExecutor` with `rlm('sub-task')`; subagents generate scenes/characters; root reconciles. Add `branch_compare` via session fork.

**Phase 3 — Code-level patch self-heal (week 6-8).** Upgrade critic → structured patch + applier; incremental re-run.

**Phase 4 — Durable orchestration + observability (week 9-12).** Full `--goal` driver; prompt caching; web-console dashboard; SLO gate (reuse `tests/slo_gate.py`). Cut over from `production_runner` once SLOs met; keep standalone as fallback.

---

## 8. Risks / open questions
- **Agnes as orchestrator**: must verify OpenAI-compatible provider registration in Prime-Agent.
- **Latency/cost**: routing every chapter through an LLM orchestrator adds overhead vs. direct `production_runner`. Mitigate with prompt caching + branch-only-when-needed.
- **Kernel state size**: 3800 chapters of `StateDB` + kernel memory — keep `StateDB` as the source of truth; kernel holds working set only.
- **Dual-mode**: keep `production_runner.py` as fallback until Phase 4 SLOs pass.

## 9. Success metrics (reuse existing SLO framework)
- Chapter pass rate at `min_chapter_score=60` ≥ standalone baseline (currently ~62 avg, fix-verdict).
- Branch comparison improves average score vs. single-pass (target +10%).
- Zero human intervention across a 50-chapter pilot (autonomy parity with current run).
