# Goldfish Review Report — `design_draft.md` (Viral-Quality Acceptance Layer)

* **Round:** 1
* **Reviewers (fresh-context Goldfish, lens + standard inlined):** design-critic, zoom-out, implementation-readiness, comprehension, codebase-details. Persona/standard files are not shipped in this install; lenses were fed inline from the EGM SKILL.md table.
* **Target scope:** full document (lines 1–298 at review time).
* **Baseline sources:** `accept_check.py`, `mark_verified.py`, `store.py`, `expand_run.py`, `runner.py`, `providers.py`, `contracts.py`, `concept_terms.py`, `dual_check.py`, `quality_thresholds.json`.
* **Findings:** 15 design-critic + 15 zoom-out + 26 implementation-readiness + 26 comprehension + 11 codebase-details = 93 raw; deduped to **42 unique** below.

## Decision legend

- **PROPOSE FIX** — structural defect that blocks implementation or breaks a real invariant; fixed in this round.
- **CONTEST / defer** — a scope decision (v0 vs milestone-2). Resolved toward the zoom-out v0 cut; the three forks (1a/2a/3a) remain honored.
- **ACCEPTED (no change)** — noted, left as-is with justification.
- **DECLINED** — rejected with reason.

## Findings (deduped, by severity)

### 🔴 Blocker — PROPOSE FIX

| ID | Source | Finding | Resolution |
|---|---|---|---|
| R1 | zoom-out F4, codebase F2/F11, impl T-04 | `quality_verdicts` PK is `(chapter)` but `re_verify`/`volume_reopen` need multiple versions; and the verdict leaf is not bound to the prose it scored (the real integrity hazard) | PK → `(chapter, version)`; add `prose_sha256`; `record_verdict` inserts v1; `re_verify` inserts vN+1; `verdict_for` returns latest; add `validate_quality_chain()` called by `runner.verify`. |
| R2 | codebase F3 | `invalidate_from` deletes `chapters`/`expansion` but does not account for `quality_verdicts` → dangling `parent_hash`, silent provenance loss | `invalidate_from` must `DELETE FROM quality_verdicts WHERE chapter >= ?` in the same transaction; the archived-DB snapshot already includes the table. |
| R3 | codebase F6/F8 | `check_prose` gains `spec` but the new findings' landing key is unspecified; if they land in `blocking`, `mark_verified` (which keys on `blocking`) breaks; `dual_check`'s `fast_path` early-return silently swallows quality | New top-level key `"quality_notes"` (absent when `spec=None`); `mark_verified` + `dual_check` read it. `dual_check.py` added to the modified-files table. |
| R4 | impl T-01/T-02/T-03, design-critic F1/F4 | `spec`/`knobs`/`verdict`/`rationale` JSON shapes undefined; `bible`/`theme`/`climax_anchors`/`volume` types undefined; `KNOB_RULES` content empty | Define schemas + a `spec` derivation API + the `volume` loader (from `config/planning/volumes.json`) + `climax_chapter` (real data has one per volume) + `KNOB_RULES` seed in `quality_config.json` (per-stage fixed sets; the rules engine is the thin override point). |
| R5 | impl T-05 | No build order / dependency graph across 13 work items | Add a numbered build order with "must-exist-before" edges + a smoke checkpoint per phase. |
| R6 | impl T-16 | Test specs name behaviors, not assertions; no LLM-mock strategy | Pin per-test names, fixtures, stubbed-provider strategy, and exact assertions. |
| R7 | codebase F1 | `mark_verified` is a file-level verb (no `Store` handle) but the draft says it "links the `quality_verdicts` leaf" — no write surface | `mark_verified` gains an optional `--store` (root) param; it records `verdict_record_hash` **into the manifest only** (the leaf is already chained by `record_verdict`); G4 steering-as-chain-commit is a **new verb** `commit_override`, not a `mark_verified` change. |
| R8 | codebase F4 | `volume_knobs` "deterministic core + versioned LLM edge" misstates the real stage path: there is **no** LLM classification in `stage_forbidden_terms`/`concept_terms` (pure JSON lookup) | `volume_knobs` is **fully deterministic** (rules table + stage + concept-unlock chapter). Any LLM climax classification is a **new, separate authority** beside `concept_terms`, explicitly versioned (`rationale.llm_classify_ver`) — not a "reuse". |
| R9 | codebase F5/F7 | `usage` table is per-`run_id` with no time dimension → a "monthly cap" is unbacked; expansion-layer `_llm` calls are **not** in `usage` at all (budget guard sees only condensed tokens) | Add a `captured_at` timestamp (or `usage_expansion` sidecar) to `usage`; add a `record_expansion_call(trace_id, tokens)` path so expansion calls are accounted; budget guard aggregates condensed + expansion. |
| R10 | comprehension #19/#20 | "Model upgrade ≠ re-score (G6)" and "re_verify … G6" mis-cite the goal | G6 is the CLI control surface; the substance is G1 + NG6. Recite to (G1, NG6). |

### 🟡 Major — PROPOSE FIX

| ID | Source | Finding | Resolution |
|---|---|---|---|
| R11 | impl T-06 | Two conflicting `quality_verdicts` DDLs; `schema_version`/`accept_check_version` pinned to the wrong table | One canonical DDL; `schema_version` on `chapters` (backfilled 0 for existing rows), `accept_check_version` on `quality_verdicts`; bootstrap ledger row on first apply. |
| R12 | impl T-09 | Budget-guard defaults unspecified (cap value, alert %, halt-vs-checkpoint, per-run vs monthly) | Default cap + 90% alert + halt-with-checkpoint; monthly window backed by R9's timestamp. |
| R13 | impl T-08 | Per-volume knob set has no home (table / file / read API) | A `volume_knobs` table (or `quality_config.json` seed) + a `read_active_knobs(volume)` API; `expand_run` reads it. |
| R14 | impl T-12 | "Calibrated" beat-coverage threshold: "confidence" undefined, no config key, conflicts with 0.6 default | v0 keeps 0.6 + **log-only** per-chapter confidence; a real threshold change is a separate versioned compliance migration (zoom-out F14). |
| R15 | impl T-13 | `rationale` length N, `knob_set_hash` digest input, int-vs-float scores undefined | N=80, truncate to whole sentence + `[…]`; `knob_set_hash = digest` of the knob dict (values only, ordered); scores are **int 0–5** (clamped). |
| R16 | impl T-18 | `trace_id` (new UUID) vs the real `run_id`/`chapter` correlation unstated | `quality_verdicts.trace_id` = expansion trace; a `chapter` + `run_id` column links to the condensed row; join rule stated. |
| R17 | impl T-21/T-22 | `what-if` and `note-quality` output contracts missing | `what-if` (deferred to M2) re-derives spec + re-judges once, prints score, writes a temp report, no chain/telemetry write; `note-quality` takes `(package, --chapter)` and stamps `manifest.quality_note` from the verdict leaf. |
| R18 | comprehension #21/#22 | "ch316→317" vs "ch317" inconsistency; `concept_terms` does not export `STAGE1_MAX_CHAPTER` | One precise statement ("stage 1 = ch1–316, stage 2 = 317+"); `stage_for` reuses `accept_check.STAGE1_MAX_CHAPTER` only. |
| R19 | design-critic F5, zoom-out F12 | `verdict_error` downstream effect (chain, manifest, budget, retry) unspecified | Define `verdict_error` as a valid leaf (`verdict="error", error_type="parse"\|"timeout"\|"budget"`); `mark_verified` proceeds (compliance independent); pipeline continues; retry budget = 1 critic retry; repair-failure → `verdict_error`, no further calls. |
| R20 | design-critic F6, zoom-out (budget) | Budget cap hit mid-chapter: half-scored chapter + resume + cache-survival undefined | On quota halt the chapter stays **uncommitted** (no `record_verdict`); `quality_verdicts` untouched; on resume re-expand from last draft + fresh judge; on-disk cache survives, in-memory does not. |

### 🟡 Major — CONTEST / defer to milestone-2 (zoom-out v0 cut)

| ID | Source | Item deferred | Justification (v0 keeps the three forks) |
|---|---|---|---|
| R21 | zoom-out F12, design-critic F3 | Per-chapter repair + re-judge loop | v0 = 1 critic call, no repair. A flat verdict is a non-blocking `quality_note` (G5). Repair ships in M2 only after G7 proves the judge correlates with something. Fork 1a still honored (a critic verdict exists). |
| R22 | zoom-out F3/F8, impl T-03 | `volume_knobs` rules engine + LLM climax classification + `volume_reopen` | v0 = fixed per-stage `knob_set` in `quality_config.json`; `select_knobs` is a thin override point; `volume_reopen` deferred. |
| R23 | zoom-out F8/F9, impl T-07/T-24 | Prompt cache + the "50–70% prompt-side savings" claim | Deferred to M2; the unverifiable % is dropped. (R9's expansion accounting stays in v0.) |
| R24 | zoom-out F6/F7/F8 | `dashboard`, drift metrics + paging, `judge_calibration`, `retention.py` module | v0 = `quality-report` (an extension of `status`), budget guard + trace_id only. G7 ships **schema + stub** and is marked "uncalibrated until OQ1/OQ2"; CUJ 3 is "unreachable until OQ2". |
| R25 | zoom-out F13, impl T-15 | `status` ¥/chapter, retries-remaining, ETA | v0 `status` = calls + tokens + wall + verdict pass-rate (what `runner.status()` already has); ¥/retries/ETA deferred. |
| R26 | zoom-out F15, codebase F10 | `migration_ledger` table + orphaned `quality_thresholds.json` relationship | v0 = one `schema_version` column; `quality_config.json` explicitly coexists with (and supersedes the grading use of) the orphaned `quality_thresholds.json`. |

### 🟢 Nit — accepted / noted

| ID | Source | Note |
|---|---|---|
| R27 | codebase F9 | `INFANT_SELF_AWARENESS` is shared by `expand_run` + `synonym_repair`; draft to say "frozen authority", not just "as-is". |
| R28 | design-critic F14 | G2 "never drift" vs G5 "soft, never blocking" tension: resolved by stating the ceiling is a **soft target** (flat chapters ship with a `quality_note`; the operator reviews via dashboard/report), not a blocking gate. This is the price of Fork 2a "no human input" — recorded as an accepted trade-off, not silently. |
| R29 | comprehension #1–#9/#15–#17 | Jargon + cross-ref + plain-English lead-ins: definitions added inline (lens list, "fork" gloss, shingle gloss, cognition-boundary gloss, climax-anchor gloss, gold-set gloss, suspect-event gloss, trace_id gloss, "Why this shape" lead-in, NFR lead-ins, modified-files intro, data-flow lead-in). |
| R30 | comprehension #23 | "soft / non-blocking quality class" used consistently, defined once. |
| R31 | design-critic F8, impl T-08 | Bootstrap: existing DB gets `schema_version=0` backfill + a bootstrap ledger row; two-population split is on `accept_check_version >= 1`. |
| R32 | design-critic F19, F20 | `quality_score` for drift = mean of the 3 dims (int), window = 10 chapters (config). Volumes are contiguous from `volumes.json`; climax = `volumes.json:climax_chapter` (mid-outline gaps do not affect volume boundary). |

## Clean-round status

This is **round 1 of the loop**; not converged yet. Two consecutive clean rounds (no open Blocker/Major that is unsettled) are required before the loop exits. The fixes above are applied to `design_draft.md`; a second round of fresh Goldfish reviewers re-audits.

## Applied fixes (this round)

All 🔴/🟡 PROPOSE FIX items R1–R11 and R14–R20 are applied to `design_draft.md`. CONTEST items R21–R26 re-scope the draft to a v0 build list + explicit milestone-2 placeholders. Nit R27–R32 are inline edits. No fork (1a/2a/3a) was overturned.

---

## Round 2

* **Round:** 2 of the loop. Reviewers re-spawned fresh (design-critic, zoom-out, implementation-readiness, comprehension, codebase-details) against the post-round-1 `design_draft.md`.
* **Raw findings:** ~50; deduped below. **Convergence:** round 2 still raised Blocker/Major items, so the loop has **not** converged; the fixes below are applied, then a round 3 re-audit is required.

### Round-2 decisions

**🔴 PROPOSE FIX (structural; blocks v0):**
| ID | Source | Finding | Fix |
|---|---|---|---|
| P1 | zoom-out #2, codebase N-2.4, impl F2 | `commit_override` split-brain (v0 goal + M2 verb) | G4 → M2; `commit-override` removed from the v0 verb list / build order / target files; v0 flow terminates at `note_quality`. |
| P2 | zoom-out #1, codebase N-2.5 | `quality-report` ghost + CUJ-1 "84/12/4" aggregate with no v0 data source | `quality-report` dropped from v0; CUJ-1 "Then" rewritten to `qc`/`status` reading `quality_verdicts`+`quality_notes`+`manifest.quality_note`; the three-class aggregate is M2 (needs a `quality_report` table). |
| P3 | codebase N-2.1, N-4.2 | budget guard + `status` must both read the real `usage` table (expansion calls land in `usage`, not only `telemetry`); `usage` needs `captured_at` | `record_expansion_call` writes a `usage` row (same INSERT/UPDATE-MAX shape as `record_trace`); `ALTER TABLE usage ADD COLUMN captured_at` (NULL backfill); `telemetry` is drift-only; guard + status both read `usage`. |
| P4 | codebase N-2.2, N-3.4 | `mark_verified --store` is a dead param | give it a real job: `mark_verified` calls `store.verdict_for(chapter)` and **refuses to stamp if the leaf hash/parent/prose_sha256 fails**; the param is a root path, read-only open, never a fresh `Store`. |
| P5 | zoom-out #7, codebase N-3.3 | `verdict_error.type="budget"` unreachable (the run halts before the critic) | reduce the union to `{parse, timeout}`; a budget halt is a run-level `failure.json` event, not a leaf. |
| P6 | zoom-out #8, codebase N-3.7 | DDL persists `spec` but the contract says derived; `accept_check_version` column + index missing | drop the `spec` column (recompute from `knob_set_hash`+config); add `accept_check_version INTEGER NOT NULL DEFAULT 1` + `CREATE INDEX idx_qv_chapter ON quality_verdicts(chapter, version DESC)`. |

**🟡 PROPOSE FIX (major; pin v0):**
| ID | Source | Fix |
|---|---|---|
| P7 | impl F1 | pin the `quality_config.json` schema (exact keys: `enable_quality`, `knob_sets[{stage, knob_set}]`, `token_cap`, `alert_pct`, `rationale_max_chars=80`, `beat_coverage_threshold=0.6`, `judge{temperature:0, reasoning_effort:none}`). |
| P8 | impl F3 | pin the `telemetry` DDL (`id, trace_id, run_id, kind, tokens, model, prompt_ver, captured_at`) + `budget_guard` contract (raise `QuotaExceeded` at 1.0; alert row at `alert_pct`). |
| P9 | impl F4 | `validate_quality_chain(store) -> list[dict]` (empty = clean); `ValueError` on any `quality_verdict_hash`/`parent_hash`/`prose_sha256` mismatch. |
| P10 | impl F6/F7 | pin the `_llm` new return `(content, usage_dict)` + all three call-site updates; `usage` type = `{total_tokens, prompt_tokens, completion_tokens, usage_estimated}`; if the provider omits usage, estimate (CJK≈1/char, ASCII≈4/char) + flag. Pin `expand_until_range`'s full new signature (`+ trace_id, spec, store`). |
| P11 | impl F10 | `record_expansion_call(trace_id, run_id, total_tokens, model, prompt_ver) -> None` in `observability.py`; writes a `telemetry` row `kind="expansion"` **and** a `usage` row. |
| P12 | impl F11 | build-order: `new_trace_id()` ships with step 2 (store); step 4 (`quality_judge`) builds/tests with `trace_id=None` (generates internally); the `telemetry` table + `record_call` (step 7) is integration-only. |
| P13 | codebase N-3.2 | `judge` temperature pinned to `0` in `quality_config.json` (not "from the polish phase", which is 0.85). |
| P14 | codebase N-3.6 | `quality_config.json` **coexists** with the vestigial `quality_thresholds.json` (no code consumer; the active source is `core/quality_policy.py` reading `runtime_config.json`); the old file's deletion is a separate migration, not this design's. |
| P15 | codebase N-3.1, impl F17 | `accept_check.run()` passes `quality_notes` through verbatim; `pass`/`verdict` stay computed from `blocking` only; `dual_check` copies `quality_notes` at both merged-dict sites (fast-path `spec=None` → `[]`; LLM path `spec=knob_set` → may be present). |
| P16 | codebase N-4.4/N-4.5, impl F16 | `stage` is **derived** from `chapter_range` via `STAGE1_MAX_CHAPTER` (not a `volumes.json` field); the "volume straddles a regime" test guards against future re-cuts, not current data. |
| P17 | comprehension #1–#5, #8 | v0/M2 legend; `leaf` definition; `压抑` typo; `bootstrap ledger row` → "schema_version backfill (ledger table is M2)"; CUJ-2 per-step M2 tags. |
| P18 | zoom-out #4, G2 | one-line disambiguation: v0 enforces the ceiling **only** via the `qc`/`status` read surface (a soft target); a blocking gate on the knob is M2 (paired with the repair loop). |
| P19 | zoom-out #5 | `qc`/`status` surface a `verdict_error_count` and a `verdict_pass_rate` that excludes errored leaves from the denominator; E2E asserts a forced `verdict_error` is readable by `qc`. |

**🟢 accepted (no change):** round-1 fixes verified intact (codebase F-1.1 `invalidate_from` DELETE placement, F-1.2 DDL/Store fit); `parent_hash = chapters.hash` (the row's own content hash, **not** `chapters.parent_hash`) clarified; `re_verify` no-op on same-model stated.

**CONTEST (deferred, no fork overturned):** `quality-report` aggregate (P2), `commit_override` (P1), the repair loop, the knob rules engine, `volume_reopen`, prompt cache, drift+pager, `judge_calibration`, `retention.py`, `migration_ledger` table, `status` ¥/retries/ETA.

---

## Round 3

* **Round:** 3. Two fresh reviewers (codebase+impl fit; critic+zoom+comprehension shape) re-audited the post-round-2 draft. **Round 2 was NOT clean** (the loop rule needs two consecutive clean rounds), so this round is a re-audit, not a termination.
* **Raw findings:** 8; deduped below. Round-2 fixes were **confirmed landed and internally consistent** (the DDL, `commit_override` split, `quality-report` M2, `record_expansion_call`, `mark_verified --store`, `verdict_error`, build order, pinned tests). New items:

### Round-3 decisions

**🔴 PROPOSE FIX (Blocker):**
| ID | Source | Finding | Fix |
|---|---|---|---|
| Q1 | shape F1 | G10 "v0 ships a schema + stub" has no `retention` DDL row in Data contracts and no build-order step — the v0/M2 boundary is not closed | Add the `retention` DDL row + `knob_overrides` DDL row to Data contracts; build-order step 2 lists them; v0 ships the tables, the M2 modules ship later. |

**🟡 PROPOSE FIX (Major):**
| ID | Source | Finding | Fix |
|---|---|---|---|
| Q2 | codebase F1/F2 | `usage.captured_at` ALTER ownership vs `enable_quality` gate: an `enable_quality=false` project keeps a 3-column `usage` but an unconditional 4-column `record_trace` would break it | The `usage` ALTER + 4-column `record_trace` are **unconditional (first `executescript`, both new + existing DBs)**; only the `quality_verdicts` DDL is `enable_quality`-gated (second `executescript`). Add `test_migration_existing_populated_db`. |
| Q3 | shape F2 | G4 title says "(M2)" but the body ships a v0 read/steer surface (= G6's set) | G4 title → "v0 read/steer surface; M2 write path"; the v0 component is the same set as G6 + the `knob_overrides` staging table. |
| Q4 | shape F3 | `knob_overrides` v0 table has no `parent_hash` yet G4 says "the same hash-chained ledger holds it" | State explicitly: v0 `knob_overrides` is an **un-chained staging table**; the chain link is the M2 `commit_override` verb. |
| Q5 | shape F4 | "volume straddling 仙门@653 → 2 knob sets" test reads as real data when no current volume crosses a regime boundary | Tag the test as a **synthetic fixture** (guard against future re-cuts). |

**🟢 accepted / pinned:** `api_key` → `api_key_env` (the real helper returns the env-var name; the judge HTTP call does `os.environ.get` first); `record_verdict` gains a `trace_id` param (written to the leaf column); `mark_verified` "refuses to stamp" is narrowed to **integrity failures only** (a `quality_notes`-only report does not trigger it); the v0 shippable-unit list gains "an uncalibrated critic (G7)".

**Status after round 3:** all Q1–Q5 + the nits applied to `design_draft.md`. **Not converged** (round 3 raised Blocker/Major). Round 4 re-audit is required; convergence needs two consecutive clean rounds.

---

## Round 4

* **Round:** 4. Two fresh reviewers (codebase+impl fit; critic+zoom+comprehension shape). **BOTH reported "clean, no Blocker/Major"** — so round 4 is one clean round; convergence needs a second consecutive clean round (round 5).
* **Confirmed landed:** `usage.captured_at` unconditional-first-script vs gated `quality_verdicts` (with `test_migration_existing_populated_db`); `retention` + `knob_overrides` DDL rows + v0/M2 boundary; `record_verdict(chapter, verdict, trace_id)` consistent; `api_key_env` correct; G4 title split; G10 retention DDL; R28/G2/NG1 unambiguous; three forks honored; no orphaned goals; no new dangling refs.
* **Nits found (all applied, non-blocking):** NIT-1 `record_verdict` 3-arg summary line; NIT-2 redundant "must ALTER usage" test note (now auto-runs); NIT-3 hash-input enumeration vs Data-contracts digest (made it reference the canonical def); NIT-4 idempotency "same verdict content" + `re_verify` no-op when `judge_model==new_model`; NIT-5 `knob_overrides.chapter` `NOT NULL` (per-chapter override, no NULL chapter); the "bootstrap ledger row" wording → "bootstrap `schema_version=1` marker row (the `migration_ledger` table is M2)".

**Status after round 4:** one clean round. **Round 5** re-audit required to confirm the second consecutive clean round.

---

## Round 5 (converged)

* **Round:** 5. Two fresh reviewers (codebase+impl fit; critic+zoom+comprehension shape). **BOTH reported "clean, no Blocker/Major"** — and all 5 round-4 nits were verified landed. The one remaining nit (line-242 "~15 calls/chapter" overstated; the real `expand_until_range` is ≤6 expansion calls) was fixed to "on the order of a handful (≤6 expansion + 1 critic)".
* **Confirmed landed:** the 5 round-4 nits; every code-symbol / table / path / signature in the draft now matches the real `store.py` / `expand_run.py` / `accept_check.py` / `mark_verified.py` / `dual_check.py` / `providers.py` / `concept_terms.py` / `runner.py`; no v0→M2 edge blocks a v0 build; no orphaned goal; three forks honored; R28 unambiguous.

**Convergence:** rounds **4 and 5 are two consecutive clean rounds** → the Goldfish review loop has **converged**. Open nits: none blocking. The `design_draft.md` is ready for the author approval gate.

---

## Loop result

**Converged after 5 rounds** (rounds 1–3 had Blocker/Major; rounds 4–5 clean). All findings classified and applied to `design_draft.md`. No fork (1a/2a/3a) was overturned; the v0/M2 boundary is explicit; the three highest-leverage contracts (the `quality_verdicts` DDL + `prose_sha256`, the `usage`/`telemetry` split, the build order) are pinned. The draft is ready to be the **spec** for the next stage (author approval, then TDD implementation).
