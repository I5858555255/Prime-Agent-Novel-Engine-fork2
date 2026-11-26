# Viral-Quality Acceptance Layer (measurable viral verdict + auto per-volume knobs + CLI control surface)

* **Author:** juliandbarnard
* **Status:** Draft
* **Date:** 2026-07-24

---

## Overview

This design adds a **viral-quality acceptance layer** to the existing two-layer novel-engine pipeline. The pipeline already produces 简写 (condensed) chapters — PLAN / DRAFT / REVIEW / commit into a hash-chained SQLite StateDB — and an external expansion layer turns each condensed chapter into 扩写 prose, checked by a programmatic accept-check (word_count / metadata / beat_coverage / forbidden-term / cognition-boundary) and then `mark-verified`. Target scale is 3,800 chapters / ~16M hanzi.

The layer this design adds is the part that is missing today: a **measurable viral-quality verdict** plus a **control surface**. Quality knobs (爽点 / 钩子 / 压抑, each 1–5) are selected **per-volume automatically** from the book's bible, the volume's theme, and its climax anchors — with no human input at selection time. Quality priority is the **ceiling**: each chapter must genuinely read like a viral hit, even at the cost of more LLM calls and longer waiting per chapter.

The three architecture forks were resolved by the author as:

- **Fork 1 (a):** the viral signal is an **LLM critic verdict** (a soft, per-chapter verdict; the repair loop is M2).
- **Fork 2 (a):** the judging layer ships now — critic verdict + versioned chain leaf + deterministic per-stage knob set + CLI control surface (the v0 subset of the layer); the control system (drift + paging + `quality-report`/dashboard + state aggregation + `commit_override` verb) lands in M2.
- **Fork 3 (a):** the **judge verdict record is hash-chained** — a versioned, stable artifact; the prose is not re-scored on model upgrade, and re-verify is cheap.

## The Problem

Today the accept-check only measures **compliance** (word count, metadata, beat coverage, forbidden terms, cognition boundary). It does **not** measure whether a chapter is actually *good* — whether it reads like a viral web-novel hit. Two consequences:

1. **There is no quality signal to accept or reject on.** A chapter can pass every compliance check and still read flat. The system has no number that says "this chapter is not viral enough," so a quality ceiling cannot be enforced or observed.
2. **Quality, once added, collides with the deterministic core.** A "viral" verdict is soft and LLM-decided; the StateDB is hash-chained and treats every commit as permanently final. Coupling a moving, taste-dependent verdict to an immutable chain, and auto-choosing knobs per volume with no provenance, is exactly the debt the design-review brainstorm flagged (immutability vs a soft criterion; non-reproducible auto knobs; a wrong auto-knob propagating across ~80-chapter volumes with no backport).

The design adds the missing layer **without** breaking stability or making the system un-debuggable: the verdict becomes a **versioned, hash-chained leaf** (G1, NG6 — stable), the knobs become **provenance-carrying, stage-aware, per-volume auto picks** (explainable), and the operator gets a **first-class CLI control surface** to see and steer quality without hand-tuning every volume.

> *Glosses for a first-time reader:* a **fork** is one of the open design decisions the brainstorm raised, each with candidate options; the author picked option (a) for each of Forks 1–3. The seven review **lenses** were Cost, Tech-Debt, Migration, Observability (SRE), Inventive, UX, and Contrarian. A **shingle** is an overlapping 2-character chunk of a beat's action; **beat coverage** is the fraction of those chunks that appear in the prose (below 60% means the beat's content was likely paraphrased away). **Cognition-boundary** = the rule that prose must not use terms the character could not yet know (stage-locked vocabulary). A **climax anchor** is a volume's peak-tension chapter (`volumes.json:climax_chapter`) that biases how much 爽点/钩子 the knobs demand. A **gold set** is a reference set of chapters humans scored by hand, used to check the judge. A **suspect event** is a hash-chained alert record that a knob-set/prompt version is possibly miscalibrated.

## Goals

* **G1 — Measurable viral verdict.** Each expanded chapter carries a structured quality verdict `{爽点, 钩子, 压抑: int 0–5 + rationale≤80 + judge_model + prompt_ver + knob_set_hash + prose_sha256}`. The verdict is the unit that is hash-chained and versioned (`(chapter, version)` leaf); the prose is not re-scored on model upgrade.
* **G2 — Per-dimension acceptance.** The accept-check gains per-dimension `quality_notes` **derived from the same per-stage knob set** (e.g. knob_set `{爽点:4,钩子:3,压抑:2}` → each dimension must be ≥ its knob), so acceptance criteria and knob choice structurally never drift apart. A dimension below its knob is a **soft, non-blocking** finding. **R28 (ceiling resolution): the viral-quality ceiling is a soft target — the machine enforces it only as non-blocking `quality_notes` (dim < knob); a below-knob dimension never enters `blocking`, never blocks `mark-verified`, and never blocks a chain commit. v0 enforces the ceiling only via the `qc`/`status` read surface; a blocking gate on the knob (paired with the repair loop) is M2.**
* **G3 — Per-volume knobs, stage-aware.** 爽点/钩子/压抑 1–5 are selected per volume from the book's bible + the volume's theme + its `climax_chapter` (from `volumes.json`), with **no human input at selection**, **rationale + provenance persisted** for every pick, and **stage-aware at every concept-unlock boundary** (炼气@317, 仙门@653, 筑基@988, … — not just ch317) so a knob never applies across two forbidden-term regimes. **v0 = deterministic per-stage `knob_set`; the rules engine + LLM climax classification are M2.**
* **G4 — Human steering (v0 read/steer surface; M2 write path).** An operator override of an auto knob or a verdict becomes a **new chain commit** via the M2 `commit_override` verb (the same hash-chained ledger holds autonomous + manual decisions). **v0 ships the read/steer surface only — `qc`, `knobs --show/--history`, `note-quality`, `status` (same set as G6) plus the `knob_overrides` staging table + `record_override` plumbing; the `commit_override` write path is M2.** In practice: `knobs --what-if` previews, `commit_override` makes it a real ledger entry — the operator never edits the SQLite file.
* **G5 — Separate failure classes.** The manifest splits **`verified` (compliance)** from **`quality_note` (soft/flat)** as separate fields with separate verbs (`mark-verified` vs `note-quality`). `quality_notes` is a new top-level key that is **never** in `blocking`. A waiver covers **compliance defects only**, never "shipped-flat".
* **G6 — CLI control surface.** First-class verbs: `qc <output>`, `knobs --show / --history`, `note-quality`; `status` surfaces tokens/calls/wall + verdict pass-rate. **`what-if`, `dashboard`, `commit-override`, and `status` ¥/retries/ETA are M2.** v0's report surface is `qc`/`status` reading `quality_verdicts` + `quality_notes` + `manifest.quality_note`; the "84% viral-on-target / 12% shipped-flat / 4% shipped-with-defect" three-class aggregate is M2 (it needs a `quality_report` table v0 does not ship).
* **G7 — Judge calibration (M2).** The judge is a meter, not an oracle: it is sampled against a ~200-chapter human gold set (every N + all climax chapters); an agreement drop is a hash-chained suspect event on the knob-set / prompt version. **v0 ships the judge as uncalibrated (schema + stub); G7 activates when OQ2 lands.**
* **G8 — Observability.** **v0:** a `trace_id` (one UUID; stamped on expansion LLM calls, and alongside the existing condensed `run_id`) correlates a chapter's life (condensed row → expansion calls → verdict); `usage` gains a `captured_at` timestamp and a budget guard emits a cap-approach alert + clean `"quota"` halt. `telemetry` (new table) carries `trace_id`/`kind`/`tokens`; **budget + `status` read `usage`, not `telemetry`**, so the guard and the operator never diverge. **M2:** rolling drift metrics + a paging channel.
* **G9 — Cost guard (v0) + prompt caching (M2).** v0: a per-run/monthly token guard with a cap-approach alert (closes the round-2 cap-halt class — the run stops with a clear "quota" reason, not a 30-second unlogged freeze). M2: semantic prompt caching on bible + volume state + prior-chapter summary (no savings claim until measured).
* **G10 — Reader-retention ground truth (M2).** Per-chapter retention / read-through / reread is the ground-truth viral signal the offline judge is a calibrated proxy for. **v0 ships a schema + stub; the operator cannot act on it until a delivery feed exists (OQ1).**

## Non-Goals

* **NG1 — Certifying "is it viral?" as a hard gate.** The true viral signal is reader retention (G10), not an offline gate. The offline judge is a proxy to be calibrated against retention, not a final authority. **Related to G2/R28: the ceiling is a soft target, not a hard gate — a flat chapter ships with a `quality_note`, and the operator reviews it; the machine does not block on taste.**
* **NG2 — Retroactively re-minting the existing hash chain** to fit new fields. New fields are appended + versioned (migration ledger); existing rows are never mutated.
* **NG3 — Mixing pre- and post-migration populations** in one quality statistic. They are two logical populations (compliance-only vs dual-signal); the tool must say so.
* **NG4 — A per-chapter hand-tuned knob UI.** "No human input at selection" is the point; the control surface steers volumes, not hand-sets each knob.
* **NG5 — Making `verified` mean "good."** `verified` = compliance; `quality_note` = soft/flat. A verified-but-flat chapter stays distinguishable.
* **NG6 — Retroactive re-scoring on model upgrade.** Versioned verdicts (Fork 3a) keep each record stable; re-measure is an explicit batched job, never silent.

## Open Questions

* **OQ1 — Retention capture source (G10, M2).** There is no live delivery layer yet; where does per-chapter retention / read-through / reread come from (a delivery hook, a downstream reader-analytics export, a stub until a platform feed exists)? **v0 default: schema + stub; the module ships in M2.** Until answered, the judge is uncalibrated.
* **OQ2 — Gold-set sourcing (G7, M2).** Who produces the ~200-chapter human gold set and by what rubric, and is it curated before the 3,800-chapter run or grown from the first volumes? **v0 default: deferred; G7 is a goal, not shipped.** CUJ 3 is unreachable until OQ2.
* **OQ3 — Critic prompt versioning (v0).** **Default resolved: a single rubric prompt, one `prompt_ver`.** A per-dimension suite (three `prompt_ver`s) is M2.
* **OQ4 — Drift-metric paging channel (G8, M2).** Where do rolling drift metrics alert (log line, a metrics file, an external endpoint)? **v0 default: no drift metrics yet; the channel is decided when G8 M2 lands.**
* **OQ5 — `what-if` scope (M2).** Does `what-if` re-run the full critic + repair loop, or only the critic once? **v0 default: `what-if` is M2; when built, critic once, no repair, no chain/telemetry write.**

## Critical User Journeys (CUJs)

### CUJ 1: Operator runs a volume and sees quality without hand-tuning

* **Given**: a committed condensed chapter range for a volume, the book's bible, and the volume's theme + `climax_chapter`.
* **When**: the operator runs the pipeline and opens `knobs --show <volume>` and `qc <volume>`.
* **Then**: the knob pick is shown with its rationale (which bible field + which climax chapter + stage), per-chapter verdicts are shown as human-readable lines ("ch217 爽点 4/5 vs stage target 3 — PASS") via `qc`/`status`, and a flat chapter is visible as a `quality_note` (shipped-flat) vs a `blocking` finding (shipped-with-defect) without JSON spelunking. **The "84% viral-on-target / 12% shipped-flat / 4% shipped-with-defect" three-class aggregate is M2** (it needs a `quality_report` table v0 does not ship); in v0 the split is read directly from `quality_verdicts` + `quality_notes` + `manifest.quality_note`.

### CUJ 2: Operator steers a flat volume without bypassing the chain **(M2 verbs; v0 ships the read + `commit_override` plumbing only)**

* **Given**: a volume whose auto knob pick produced flat prose (shipped-flat, `quality_note` set, `verified` still true).
* **When**: the operator previews with `knobs --what-if 爽点=4` **(M2 verb)** and then commits an override via `commit_override` **(M2 verb; the v0 `store.py` plumbing — a `knob_overrides` table + `record_override` — ships in v0, the runner verb is M2)**.
* **Then**: `what-if` produces a score preview with no chain write; the operator's committed override is recorded as a new ledger commit (knob provenance + rationale), and downstream chapters use the overridden knob.

### CUJ 3: Judge drift is caught before a volume ships **(M2 — unreachable until OQ2 provides a gold set)**

* **Given**: an unattended run where the critic's prompt version or knob set has drifted and agreement with the gold set is falling.
* **When**: the sampled judge–human agreement check runs (every N + all climax chapters).
* **Then**: an agreement drop below threshold is a hash-chained suspect event on that knob-set / prompt version; rolling drift metrics page the operator (or halt the run at the volume boundary) before the whole volume ships.

### CUJ 4: A budget cap no longer stalls a run silently

* **Given**: a 3,800-chapter run against a personal-tier monthly token quota.
* **When**: the run approaches the quota ceiling.
* **Then**: the per-run/monthly token guard + cap-approach alert fires with a budget-consumption readout; the run halts cleanly (or checkpoints) with a clear "quota" reason, not a 30-second mystery halt with history=0.

## Technical Plan (Architecture)

The design adds **components** and **modifies existing files**. It stays inside the existing module layout (`novel_engine/condensed/`); no new package. Everything new is additive + versioned; the hash chain is never re-minted.

> **v0 vs M2.** **Milestones: v0 is the shippable first release; M2 is the next milestone where deferred items land.** The three resolved forks (LLM-critic verdict, the judging layer ships now, verdict hash-chained) are honored in **v0**. v0 is the shippable unit: a critic verdict, a versioned chain leaf, a deterministic per-stage knob set, an **uncalibrated critic (G7)**, the `retention` DDL + `knob_overrides` staging table (v0 schema; their M2 modules), and a CLI control surface (`qc`/`knobs --show/--history`/`note-quality`/`status`). The items marked **(M2)** below are scoped now but implemented later: the repair loop, the knob rules engine, the `commit_override` **verb**, the `quality-report`/`dashboard` aggregate, prompt caching, drift paging, calibration, the retention feed, `what-if`, and the `migration_ledger` table. Deferring them does not reopen any fork — the critic verdict and the chained leaf still ship. **v0 is shippable when: (1) one critic call/chapter (≤2 incl. retry) produces a bounded verdict + `prose_sha256`; (2) the verdict is a versioned hash-chained leaf off `chapters.hash`; (3) `select_knobs` is deterministic per-stage with a rationale record; (4) `qc`/`knobs --show/--history`/`note-quality`/`status` read the leaf without writing; (5) the budget guard emits a cap-approach alert + clean `"quota"` halt.**

> *Glosses for a first-time reader:* a **fork** is one of the open design decisions the brainstorm raised, each with candidate options; the author picked option (a) for each of Forks 1–3. The seven review **lenses** were Cost, Tech-Debt, Migration, Observability (SRE), Inventive, UX, and Contrarian. A **leaf** is the verdict record chained *onto* a chapter (an add-on row, not a modification of the chapter row). A **shingle** is an overlapping 2-character chunk of a beat's action (used by the existing beat-coverage check; not part of this layer); **beat coverage** is the fraction of those chunks that appear in the prose (below 60% means the beat's content was likely paraphrased away). **Cognition-boundary** = the rule that prose must not use terms the character could not yet know (stage-locked vocabulary). A **climax anchor** is a volume's peak-tension chapter (`volumes.json:climax_chapter`) that biases how much 爽点/钩子 the knobs demand. A **gold set** is a reference set of chapters humans scored by hand, used to check the judge. A **suspect event** is a hash-chained alert record that a knob-set/prompt version is possibly miscalibrated.

### New components

1. **`quality_judge.py`** — the LLM critic (Fork 1a).
   - `judge_prose(prose, spec, provider, trace_id) -> verdict`: **one** critic call (temperature = `quality_config.json:judge.temperature` = 0, reasoning_effort none — **not** the polish phase's 0.85; `provider` is the 4-tuple `(base_url, api_key_env, model, opts)` from `load_provider_full` — note element 2 is the **env-var name**, so the HTTP call does `os.environ.get(api_key_env)` first; and `judge_model = provider[2]`) that returns the bounded verdict above. Parse/timeout failure → retry **once** (≤2 critic calls/chapter total), then a `verdict_error` leaf (`verdict_error={"type":"parse"|"timeout"}`) — not a crash. A budget halt is a run-level `failure.json` event, **not** a leaf value.
   - **v0 ships the critic only.** A per-chapter **repair + re-judge loop is (M2)** (R21): a flat verdict is a non-blocking `quality_note` (G5); repair ships after G7 proves the judge's scores correlate with a reference.
   - `spec` = the per-dimension thresholds **derived from the same per-stage knob set** (G2), e.g. knob_set `{爽点:4,钩子:3,压抑:2}` → each dimension must be ≥ its knob. One canonical `knob_set` JSON (hash = its digest) is the single source of truth.
   - **`prose_sha256`**: the verdict row carries `sha256(final assembled expanded chapter text)` — the leaf is bound to the exact text it scored, so an in-place re-expansion that diverges the prose is caught (zoom-out F4).

2. **`volume_knobs.py`** — deterministic per-volume knob selection (G3).
   - **v0 = fixed per-stage `knob_set`** read from `quality_config.json` (stage 1 vs stage 2). `select_knobs(volume, stage) -> (knob_set, rationale)` where rationale = `{bible_field, climax_chapter, stage}`. **Fully deterministic** — the real stage path (`accept_check.STAGE1_MAX_CHAPTER`, `concept_terms.forbidden_for_chapter`) is a pure JSON lookup with **no LLM** (codebase F4); the draft's earlier "versioned LLM edge" is retracted.
   - **Stage-aware at every concept-unlock boundary**, not only ch317: `regimes_for(chapter_range) -> list[int]` reads `concept_unlocks.json` and returns the sorted in-range concept-unlock chapter numbers strictly inside `(start, end]` (600–700 → `[653]`; 300–500 → `[317]`; none → `[]`); `select_knobs_for_volume(volume) -> list[(knob_set, rationale, sub_range)]` emits one knob set per regime (`len(regimes)+1` segments), each scoped to its own sub_range + the stage of its first chapter, so a knob never applies across two forbidden-term regimes. The single-stage `select_knobs(volume, stage)` and the per-chapter `read_active_knobs(volume)` remain the simpler read APIs for the non-straddling case. With current volume boundaries no volume crosses 317/653/988, so this path is a guard against future re-cuts, not exercised by today's data.
   - **The knob rules engine + LLM climax classification + `volume_reopen` are (M2)** (R22): v0's `select_knobs` is the thin override point a rules engine replaces later.

3. **`quality_store.py`** — versioned chain leaf (Fork 3a). A new table **`quality_verdicts`** in `manuscript.sqlite3` (DDL above):
   - `quality_verdict_hash = digest(the verdict JSON above + prose_sha256)` — exactly as defined in Data contracts (`quality_verdicts` DDL note); `parent_hash` = the `chapters.hash` of the condensed row; **`version=1` on first `record_verdict`**, incremented by `re_verify` (R1, R11).
   - `record_verdict(chapter, verdict, prose_sha256, trace_id)` is **append-idempotent**: same verdict content as the latest version returns the existing leaf hash (no duplicate); different content (a re-judge after prose re-expansion) appends `version=N+1` — additive, never re-minted. `judge_model`/`prompt_ver`/`knob_set_hash` default to the values the verdict record carries (a leaf always reflects the model that produced the verdict; bare `None` → `"stub"`). `prose_sha256` is the hash input (NULL when `verdict_error` is set); `trace_id` is the expansion trace (from `expand_until_range`'s frame) and is written to the leaf's `trace_id` column.
   - `verdict_for(chapter) -> dict | None`: returns the **latest** version; raises `ValueError` if `quality_verdict_hash != digest(stored)` or `parent_hash != store.chapter(chapter)["hash"]` (so an invalidated/re-committed chapter is caught).
   - `re_verify(chapters, new_model)`: explicit batched re-measure over every chapter in `chapters` whose latest leaf has `judge_model != new_model`; writes new leaf versions, leaves same-model leaves untouched (NG6).
   - **`invalidate_from` must `DELETE FROM quality_verdicts WHERE chapter >= ?`** in the same transaction as the `chapters` delete (R2); the archived-DB snapshot already includes the table.
   - `validate_quality_chain(store) -> list[dict]` (empty = clean); raises `ValueError` on any `quality_verdict_hash` / `parent_hash` / `prose_sha256` mismatch; called by `runner.verify`.
   - Additive DDL is registered in `Store.__init__` as a **second, `enable_quality`-gated `executescript`** (existing projects keep the single unconditional script); `quality_store` is a module that receives the `Store.db` handle (no separate connection). **`migration_ledger` as a table is (M2)** (R26) — v0 uses the `schema_version` marker.

4. **`observability.py`** — v0 = **trace_id + budget guard only** (R24).
   - `new_trace_id()` (UUID); `record_call(trace_id, kind, tokens)` → a row in the `telemetry` table. **Budget + status read the real `usage` table, not `telemetry`** — so the guard and the operator's `status` never diverge (codebase N-2.1).
   - `record_expansion_call(trace_id, run_id, total_tokens, model, prompt_ver) -> None` (in `observability.py`): writes a `telemetry` row `kind="expansion"` **and** a `usage` row (the same INSERT/UPDATE-MAX shape as `record_trace`) so expansion-layer calls are accounted. If the provider response omits `usage`, estimate `total_tokens` (CJK≈1/char, ASCII≈4/char, same rule as `llm_client._estimate_usage`) and flag the row `usage_estimated=1`.
   - **Budget guard**: `budget_guard(cap, alert_pct=90)` reads `SUM(usage.total_tokens)` over the `captured_at` monthly window; at `alert_pct` it writes a cap-approach `telemetry` alert row; at 100% it signals `halt=True`/`reason="quota"` and the runner halts cleanly with reason "quota" (closes the round-2 mystery halt). **Two surfaces:** `budget_guard` returns the readout dict (what `runner.expand` consumes before the LLM calls); `budget_guard_raises` raises the `QuotaExceeded` exception at 100% for callers that want the raise form. `QuotaExceeded` carries the guard readout so the halt event is self-describing; a quota halt is a run-level `failure.json` event, never a verdict leaf. The monthly cap is a **hard stop in v0**; the existing `extend-budget` verb extends the per-chapter LLM-call budget, **not** the monthly token cap (extending the monthly cap is M2).
   - **Prompt cache, rolling drift metrics, and the paging channel are (M2)** (R23, R24); the "50–70% prompt-side savings" figure is dropped as unverifiable.

5. **`retention.py`** — **(M2)**. v0 ships **schema only** (a `retention` DDL row) + a stub ingest; the module and `judge_calibration` ship when OQ1 (a delivery/reader-analytics feed) has an answer (R24). Until then the judge is **uncalibrated** — stated, not assumed.

### Modified files (five existing files gain behavior; none lose it)

| File | Change (v0) |
|---|---|
| `accept_check.py` | `check_prose(prose, card, manifest, spec=None)` → when `spec` present, returns a **new top-level key `"quality_notes"`** (dim < knob → a soft finding, **never blocking**; absent when `spec=None`). word_count/metadata/beat stay hard (default 0.6, **log-only** per-chapter confidence; a real threshold change is a separate versioned migration). `INFANT_SELF_AWARENESS`/cognition unchanged — note the list is a **frozen authority shared with `expand_run` and `synonym_repair`**. |
| `mark_verified.py` | Gains an optional `--store` (**root path, read-only** — never a fresh `Store`): when present it calls `verdict_for(chapter)` and **refuses to stamp only on an integrity failure** (leaf hash/parent/`prose_sha256` mismatch — a `quality_notes`-only report does **not** trigger the refusal), then records `verification.verdict_record_hash` (the leaf's `quality_verdict_hash`) **into the manifest**; when omitted it is not set. A `verdict_error` leaf → still stamp compliance, set `verdict_record_hash=null` + log. New `note_quality(package, --chapter)` stamps the **soft class** (`manifest.quality_note`). Waiver stays compliance-only. **Manual steering-as-chain-commit is the M2 `commit_override` verb, not a `mark_verified` change** (codebase F1). |
| `store.py` | `Store.__init__` gains a **second, `enable_quality`-gated `executescript`** for `quality_verdicts` + `schema_version` (existing projects keep the single unconditional script); `quality_store` is a module receiving the `Store.db` handle. Delegate `record_verdict`/`verdict_for`/`re_verify`/`validate_quality_chain`/`budget_status`; **`knob_overrides` table + `record_override` ship in v0 (plumbing; the runner verb is M2)**; `invalidate_from` deletes `quality_verdicts` rows `>= N` in the same transaction; `usage` gains `captured_at` (`record_trace` → 4-column). `chapters`/`operations`/`allowances` core unchanged. |
| `expand_run.py` | `_llm` → returns `(content, usage_dict)` at all three call sites (if the provider omits `usage`, estimate + flag `usage_estimated=1`); gains `trace_id`; `record_expansion_call` after each call (writes `telemetry` + `usage`); call `judge_prose` **once** after the loop (M2 adds repair; a parse retry makes ≤2); pass the volume `spec` into the **final** `check_prose` only (intermediate rounds `spec=None`); `expand_until_range` gains `trace_id`/`spec`/`store` + returns `trace_id`/`total_expansion_tokens`. Target-aware continuation / overshoot trim / `deterministic_repair` unchanged. |
| `runner.py` | New verbs `qc`, `knobs` (`--show/--history`), `note-quality`; `status` += tokens/calls/wall + `verdict_pass_rate` (denominator excludes `verdict_error` leaves) + `verdict_error_rate` (¥/retries/ETA are M2). **`commit-override`, `what-if`, `quality-report` are M2** (verb space reserved). `verify` → `validate_quality_chain()`. `dual-check` reads `spec` from `read_active_knobs` and its merged dict gains `quality_notes` (codebase F8). |

### Data flow

This is the full per-chapter flow, top to bottom: a knob set is picked for the volume's stage, the condensed chapter is expanded, checked, judged once, and the verdict is chained; nothing on the left (bible/theme) is written back.

```
[volumes.json: chapter_range + theme + climax_chapter;  stage derived from chapter_range via STAGE1_MAX_CHAPTER]
        │  volume_knobs.select_knobs (v0: fixed per-stage knob_set from quality_config.json; fully deterministic)
        ▼
   {爽点,钩子,压抑} + rationale{bible_field, climax_chapter, stage}
        │  → derived per-dimension spec (G2; one canonical knob_set JSON, hash = its digest)
        ▼
condensed chapter (store.chapters, hash-chained, UNCHANGED)
        │  expand_run.expand_until_range (trace_id + usage returned by _llm; record_expansion_call)
        ▼
   expanded prose  ──accept_check.check_prose(prose, card, manifest, spec)   [final call only]
        │             compliance: word_count/metadata/beat/forbidden/cognition (hard; pass = from blocking only)
        │             "quality_notes": dim < knob → soft finding (never blocking)
        │  quality_judge.judge_prose (LLM critic, ≤2 calls incl. 1 retry; verdict_error on parse/timeout)
        ▼
   verdict {爽点,钩子,压抑:int, rationale≤80, judge_model, prompt_ver, knob_set_hash, verdict_error}
        │  quality_store.record_verdict (leaf v1, hash-chained off chapters.hash; prose_sha256 column binds it to the text)
        ▼
   mark_verified (compliance stamp; --store verifies the leaf, then manifest verdict_record_hash)
   note_quality  (soft stamp, separate manifest field)   [v0 flow ends here]
   commit_override (M2: manual steering as a new chain commit)
        │
   observability v0: trace_id correlation, usage+captured_at (guard + status both read usage), budget guard ("quota" halt)
   observability M2: prompt cache, rolling drift metrics, paging
```

### Why this shape

In one line: we attach a quality verdict to a chapter without changing the chapter, tie the pass/fail bar to the same knob set, keep knob-picking reproducible, keep "flat" separate from "broken", and calibrate the judge against real readers — while keeping every new piece additive so the hash chain is never re-minted.

- **Verdict is a leaf off the chapter, not a mutation of it** (3a + Migration): the condensed `chapters` row stays final; `re_verify` writes new leaf **versions** (`(chapter, version)` PK) and is batched/never silent (G1, NG6). `prose_sha256` binds each leaf to the exact text it scored, so an in-place re-expansion that diverges the prose is caught.
- **Spec derives from the same knob set** (G2 + Inventive): the acceptance criterion and the quality target are one vector, so "standard" and "knob" can never disagree.
- **Deterministic knob selection** (Tech-Debt): v0's knob pick is a fixed per-stage rule with a provenance record (bible field + climax chapter + stage); the real stage path has no LLM, so nothing is silently non-reproducible. A rules engine / LLM climax classification is a later, explicitly-versioned addition.
- **Soft class separated from compliance** (G5 + UX): `quality_notes` is a new top-level key (never `blocking`); `mark_verified`/`dual_check` read it, so an operator can answer "shipped-flat vs shipped-with-defect" without JSON spelunking.
- **Ground-truth retention + calibrated judge** (G10 + G7 + Inventive): the LLM critic is a meter, not an oracle; v0 states it is **uncalibrated** until OQ1/OQ2 provide a retention feed + gold set.

### Milestone-2 placeholders (scoped, not built in v0)

* Per-chapter **repair + re-judge loop** (after G7 proves the judge correlates with a reference).
* **Knob rules engine** + LLM climax classification (beside, not replacing, `concept_terms`) + `volume_reopen`.
* **Prompt cache** (no savings claim) + rolling **drift metrics** + paging channel.
* **`judge_calibration`** + `retention.py` module (when OQ1/OQ2 land).
* **`quality-report` / `dashboard`** (a `quality_report` table that aggregates the "84% viral-on-target / 12% shipped-flat / 4% shipped-with-defect" three-class split; v0's report surface is `qc`/`status` reading `quality_verdicts` + `quality_notes` + `manifest.quality_note`) + `status` ¥/chapter, retries-remaining, ETA.
* **`commit_override`** (manual steering as a new chain commit; the v0 `store.py` plumbing — a `knob_overrides` table + `record_override` — ships in v0, but the runner **verb** is M2, so G4's "no human input" promise is honored by v0's read/steer surface only).
* `what-if` verb + `migration_ledger` table + real beat-coverage threshold migration.

---

## Alternatives Considered

### Alternative A: Reader-retention funnel as the *only* acceptance signal (no offline judge)

* **Pros**: the only true "viral" signal; no proxy-certification; zero added LLM calls.
* **Cons**: needs a live delivery/reader-analytics feed that does not exist yet (OQ1); cannot gate an un-published chapter at expand time; no per-dimension steering before publish.
* **Reason for Rejection**: Rejected by author as the *primary* gate (Fork 1a picks the LLM critic), but **kept as the ground-truth calibration target** (G10) and the eventual end-state signal. The offline judge is a calibrated proxy for retention, not a replacement.

### Alternative B: Fine-tuned classifier / reward model for the viral signal

* **Pros**: ~half the steady-state token cost of an LLM critic; fast; no per-chapter repair loop.
* **Cons**: requires a curated viral-sample training set + one-time training cost; a static model cannot track platform taste drift; weak per-dimension rationale (no "why").
* **Reason for Rejection**: Rejected by author (Fork 1a chose LLM critic + repair). Revisit if token cost (G9 prompt caching) still makes the run unaffordable on a paid key.

### Alternative C: Deterministic proxy / heuristics for "viral quality"

* **Pros**: cheapest; fully reproducible; no soft judgment.
* **Cons**: cannot actually detect "viral"; measures style heuristics, not taste; the quality-ceiling goal is not met.
* **Reason for Rejection**: Rejected by author — a proxy that can't detect viral quality does not serve priority (b) ceiling.

### Alternative D: YAGNI minimum first (fix shingle + 1 logged judge call + retention + frozen knobs), defer the auto-knob system

* **Pros**: smallest delta; the 20/20-clean baseline says the machine works; avoids building a second-order control system before an ablation proves benefit.
* **Cons**: no per-volume adaptation; no control surface; the auto-knob + judging + dashboard ship later, re-opening migration scope twice.
* **Reason for Rejection**: Rejected by author (Fork 2a = full layer now). The shingle-threshold fix and the retention stub are **kept regardless** (they are in G8/G10).

### Alternative E: Verdict outside the chain, re-scored on demand

* **Pros**: always reflects the latest judge; simplest.
* **Cons**: "stable" not guaranteed; re-scoring 3,800 chapters on every model/prompt upgrade; a wrong auto-knob volume is re-scored (not re-opened), so it stays silently off.
* **Reason for Rejection**: Rejected by author (Fork 3a = verdict hash-chained). Re-measure is an explicit batched `re_verify` job, not silent.

### Alternative F: Re-chain / mutate existing rows to add the quality field

* **Pros**: one uniform population.
* **Cons**: breaks the hash chain; orphans already-verified rows; the chain is a tombstone, not a column.
* **Reason for Rejection**: Rejected by author — append + version + migration ledger instead (3a + Migration lens).

### Alternative G: Replace numeric 0–5 verdicts with pass/fail + evidence quotes (zoom-out F1)

* **Pros**: two critic models (or two runs of one) do not produce comparable 0–5 floats; a pass/fail + evidence record is more stable under model upgrade and matches the repo's house style (`dual_check.py` "不得以主观评分替代事实核对").
* **Cons**: loses the per-dimension magnitude the per-volume knobs key on; the "ceiling" is harder to express as a scalar when the knob is 爽点=4 (a float makes "how far below 4" visible).
* **Reason for Rejection**: Rejected by author (Fork 1a keeps the numeric 0–5 verdict) — but its **prose-binding** demand (`prose_sha256`) is adopted (R1), and v0 drops the repair loop (R21) so the numeric verdict stays bounded to one critic call. Revisit G if the judge's scores prove non-comparable across model upgrades.

### Alternative H: Drop the per-chapter repair loop entirely, even in M2 (zoom-out F12)

* **Pros**: removes the most expensive + least verifiable behavior (a repair triggered by an LLM's 0–5 opinion can *worsen* prose); honors "ceiling" as a soft review target instead of a hard fix loop.
* **Cons**: a genuinely flat chapter ships flat (only `quality_note`-tagged); the "ceiling" is enforced by operator review, not by the machine.
* **Reason for Rejection**: Rejected by author as the permanent shape — repair stays **deferred to M2** (R21), gated on G7 proving the judge correlates with a reference, so the expensive path is not built blind. v0 is repair-free either way.

---

## Non-Functional Requirements

* **Performance & token economics (Cost lens).** Plain version: each chapter costs on the order of a handful of existing LLM calls (≤6 expansion calls: ≤3 continuation + ≤2 repair + 1 initial, per `expand_until_range`) plus 1 critic call (v0; no repair loop until M2). The run must warn before it hits the monthly token cap and stop cleanly with reason `"quota"` instead of the previous unexplained 30-second freeze. The "50–70% prompt-side savings" claim is dropped as unverifiable; prompt caching is M2.
* **Reproducibility & stability (Tech-Debt lens).** Knob selection in v0 is a deterministic per-stage rule with a provenance record (bible field + climax chapter + stage); there is no LLM in the v0 knob path. Model/prompt upgrades never silently re-score committed verdicts; `re_verify` is an explicit batched job that writes new leaf versions.
* **Rollout & rollback (Migration lens).** The migration is additive + versioned: `chapters.schema_version` (backfilled 0 on existing rows), `quality_verdicts.accept_check_version`, and a bootstrap `schema_version=1` marker row (the `migration_ledger` **table** is M2; the v0 marker lives in `schema_version`). Pre-migration chapters are compliance-only; post-migration are dual-signal — the tool reports the two populations separately (split on `accept_check_version >= 1`). A **volume re-open** is M2 and, when built, produces new row versions, never a mutation. **All concept-unlock boundaries** (not just ch317) are recorded so a knob never silently spans two regimes.
* **Observability (SRE lens).** v0: every LLM call (condensed and expansion) carries a `trace_id`; `usage` gains a `captured_at` timestamp so a monthly window is computable; the verdict leaf carries dimension scores + judge prompt id + model + `prose_sha256`; the budget guard emits a cap-approach alert. M2: rolling drift metrics + paging.
* **Security.** The critic and knob path read bible/theme/climax; no secrets in verdict records; `trace_id` is an opaque UUID, not PII.

---

## Detailed Implementation Plan

Base dir (call it `<NE>`): `packages/coding-agent/skills/novel-engine/src/novel_engine/condensed/`.
All new code is inside `<NE>`; tests inside `src/novel_engine/tests/` (absolute from skill root). No new package.

### Data contracts (define these first — every module shares them)

**`knob_set` (canonical, one JSON per stage):** `{"爽点": int 1–5, "钩子": int 1–5, "压抑": int 1–5}`.
`knob_set_hash = digest(knob_set)` (ordered keys, `contracts.digest`). The single source of truth; `spec` is not stored separately.

**`spec` (derived, not persisted):** `spec_for(knob_set)` → same dict; the accept rule is `score[dim] >= knob_set[dim]` for every dim.

**`verdict` record (the chained unit):**
```
{"爽点": int 0–5, "钩子": int 0–5, "压抑": int 0–5,
 "rationale": str (≤80 字, truncate to whole sentence + "…"),
 "judge_model": str, "prompt_ver": str, "knob_set_hash": str,
 "verdict_error": null | {"type": "parse" | "timeout"}}
```
`quality_verdict_hash = digest(the dict above + prose_sha256)` (ordered). `prose_sha256 = sha256(final assembled expanded chapter text)` — stored as a **column** and included in the hash input; it is **not** repeated inside the `verdict` JSON. **`record_verdict(chapter, verdict, prose_sha256, trace_id)`** takes `prose_sha256` as an explicit 3rd argument (the judge computes it from the assembled text; it is a hash input, never inside the `verdict` JSON). When `verdict_error` is set, `prose_sha256` is `NULL` and the hash omits it. Scores are **int 0–5**, clamped. `verdict_error.type` is `{parse, timeout}` only — a **budget** halt is a run-level `failure.json` event (the critic never runs after a quota halt), not a leaf value.

**`quality_verdicts` DDL (canonical):**
```
CREATE TABLE quality_verdicts(
  chapter INTEGER NOT NULL, version INTEGER NOT NULL DEFAULT 1,
  verdict TEXT NOT NULL, knob_set_hash TEXT NOT NULL,
  judge_model TEXT NOT NULL, prompt_ver TEXT NOT NULL,
  prose_sha256 TEXT, verdict_error TEXT,
  quality_verdict_hash TEXT NOT NULL, parent_hash TEXT NOT NULL,
  trace_id TEXT NOT NULL, accept_check_version INTEGER NOT NULL DEFAULT 1,
  PRIMARY KEY(chapter, version));
CREATE INDEX idx_qv_chapter ON quality_verdicts(chapter, version DESC);
ALTER TABLE chapters ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 0;   -- backfill existing rows to 0
-- bootstrap: a schema_version=1 marker row (the migration_ledger table is M2)
```
Notes: `parent_hash` = `chapters.hash` **for the same chapter number** (the content hash of the condensed row this verdict is bound to); it is **not** `chapters.parent_hash` (the previous-chapter link in the main chain). When `verdict_error` is set, `prose_sha256` is `NULL` and `quality_verdict_hash = digest({verdict, knob_set_hash, judge_model, prompt_ver, verdict_error})`. `spec` is **not** a column — it is recomputed from `knob_set_hash` + `quality_config.json` (`spec_for`), so the criterion and the knob can never drift.

**`telemetry` DDL (new table; drift/budget only — budget + status read the real `usage` table, not this one):**
```
CREATE TABLE telemetry(
  id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, run_id TEXT,
  kind TEXT NOT NULL, tokens INTEGER NOT NULL, model TEXT, prompt_ver TEXT, payload TEXT,
  captured_at TEXT NOT NULL);
-- `payload` carries per-row context the spec left open: an expansion row stores the
`usage_estimated` 0/1 flag; a budget-alert row stores the `{"pct":…, "alert_pct":…}` JSON.
```
**`usage` migration (UNCONDITIONAL — in the first `executescript`, NOT gated by `enable_quality`):** `ALTER TABLE usage ADD COLUMN captured_at TEXT;` (existing rows backfill `NULL` = legacy/unaccounted; the monthly window treats `NULL` as included in the running total). `record_trace`'s INSERT becomes 4-column, unconditionally. **Only the `quality_verdicts` DDL is gated behind `enable_quality`** (second, gated `executescript`), so an `enable_quality=false` project still has the 4-column `usage` and never breaks `record_trace`.

**`retention` DDL row (v0 ships the table + stub only; the ingest module is M2):**
```
CREATE TABLE retention(chapter INTEGER PRIMARY KEY, read_through REAL, reread REAL,
                       abandon_rate REAL, captured_at TEXT, source TEXT);
```
`ingest_retention` (M2, `retention.py`) is a no-op until OQ1 lands; v0 creates the table so the schema is stable and `judge_calibration` (M2, G7) has a target.

**`knob_overrides` DDL row (v0 plumbing only; the chain link is M2 `commit_override`):**
```
CREATE TABLE knob_overrides(chapter INTEGER NOT NULL, dim TEXT NOT NULL, new_value INTEGER NOT NULL,
                            rationale TEXT, override_hash TEXT, created_at TEXT,
                            PRIMARY KEY(chapter, dim));
```
v0 rows are an **un-chained staging table** (no `parent_hash`); the M2 `commit_override` verb is what folds them into the chain as a new commit. `chapter` is `NOT NULL` (an override is always per-chapter; a volume-wide override is expressed as one row per chapter, not a NULL chapter).

**`quality_config.json` schema (exact keys):**
```
{"enable_quality": true,
 "knob_sets": [{"stage": 1, "knob_set": {"爽点":3,"钩子":3,"压抑":2}},
               {"stage": 2, "knob_set": {"爽点":4,"钩子":4,"压抑":3}}],
 "token_cap": 5000000, "alert_pct": 90,
 "rationale_max_chars": 80, "beat_coverage_threshold": 0.6,
 "judge": {"temperature": 0, "reasoning_effort": "none"}}
```
`stage` is **derived** from `chapter_range` via `STAGE1_MAX_CHAPTER` (ch ≤316 → stage 1; ch ≥317 → stage 2) — it is **not** a `volumes.json` field. `enable_quality` gates the DDL: when `false`, `Store.__init__` skips the `quality_verdicts` script, `record_verdict`/`verdict_for`/`re_verify` raise `RuntimeError("quality layer not enabled")`, and `check_prose` is called with `spec=None` (so `quality_notes` is absent). Flipping `false → true` on a populated project runs the additive DDL + `schema_version` backfill + bootstrap row once; flipping `true → false` is refused (leaves are already chained; un-registering would re-mint the chain). This file **coexists** with the vestigial `quality_thresholds.json` (no code consumer; the active threshold source is `core/quality_policy.py` reading `runtime_config.json`); deleting that file is a separate migration, not this design's.

**`volume` type:** a dict from `config/planning/volumes.json` (`id, title, chapter_range:[start,end], theme, core_conflicts[], climax_chapter, climax_description`). `bible` = the `load_canon` dict. "climax anchors" = the single `climax_chapter` (rationale may also cite `climax_description`/`core_conflicts`). The `volume_knobs` rationale record field is **`bible_field`** (one `load_canon` key, e.g. `world`).

### Build order (must-exist-before edges; smoke checkpoint per phase)

1. `config/quality_config.json` (per-stage `knob_set`, token cap, alert %, `rationale_max_chars`=80, `judge.temperature`=0; exact schema in Data contracts; drift window is M2) → **checkpoint: import + JSON schema test.**
2. `quality_store.py` DDL + `record_verdict(chapter, verdict, prose_sha256, trace_id)`/`verdict_for`/`re_verify`/`validate_quality_chain`/`budget_status`; **`new_trace_id()` ships here (so step 4 can test with `trace_id=None` generating internally)**; `store.py` first `executescript` adds `usage.captured_at` **unconditionally** (4-column `record_trace`), and the **`enable_quality`-gated** second `executescript` adds `quality_verdicts` + `schema_version` + `retention` + `knob_overrides`; `invalidate_from` deletes the leaf rows → **checkpoint: insert/verify leaf, `verdict_for` latest-version, invalidate deletes rows, `validate_quality_chain` clean, and an existing populated DB opens + runs the unconditional `usage` ALTER without breaking `record_trace`.**
3. `volume_knobs.py` (`select_knobs` deterministic per-stage + `regimes_for` over `concept_unlocks.json` + `read_active_knobs` read API) → **checkpoint: same volume → same knob_set; a volume straddling a regime shift yields 2 sets (guards future re-cuts).**
4. `quality_judge.py` (`judge_prose` ≤2 calls incl. 1 retry, bounded verdict, `verdict_error` path; `trace_id=None` → internal) → **checkpoint: stubbed provider → verdict record + `prose_sha256` + hash; forced parse-fail → `verdict_error` leaf, no crash.**
5. `accept_check.py` `spec` arg + `quality_notes` top-level key (`run()` passes it through; `pass` stays `blocking`-only); `mark_verified.py` `--store` (read-only; verifies the leaf via `verdict_for` before stamping `verdict_record_hash`) + `note_quality`; `dual_check.py` reads `spec` from `read_active_knobs` + merges `quality_notes` at both return sites → **checkpoint: `quality_notes` never in `blocking`; `mark_verified` proceeds on a `quality_notes`-only report; waiver still compliance-only; fast-path `spec=None` → `[]`.**
6. `expand_run.py` `_llm` returns `(content, usage_dict)` at all three call sites + `trace_id` + `record_expansion_call` (writes `telemetry` + `usage`); `expand_until_range` gains `trace_id`/`spec`/`store`; judge hook after loop (≤1 critic call, retry makes 2); final `check_prose` gets `spec` → **checkpoint: ≤2 critic calls/chapter; expansion tokens land in `usage`; chain parent_hash intact.**
7. `observability.py` v0: `telemetry` table + `record_call` + `record_expansion_call` + `budget_guard` (`usage.captured_at` window, 90% alert row, `QuotaExceeded` → `"quota"` halt). **Guard and `status` both read `usage`.** → **checkpoint: synthetic cap breach → clean `"quota"` signal + alert row; `usage` row written.**
8. `runner.py` verbs `qc`/`knobs --show/--history`/`note-quality` + `status` (tokens/calls/wall + `verdict_pass_rate` excluding `verdict_error` leaves + `verdict_error_rate`) + `verify` → `validate_quality_chain()`. **`commit-override` + `what-if` are M2.** → **checkpoint: `qc` reads a finished package without committing (incl. a `verdict_error` leaf); `status` pass-rate excludes errors; `verify` recomputes every leaf hash.**
9. Tests (below). **(M2):** repair loop, knob rules engine, `commit_override` verb, `quality-report`/`dashboard`, prompt cache, drift+pager, `judge_calibration`, `retention.py`, `what-if`, `migration_ledger` table.

### Target files to create/modify (v0)

#### 1. `<NE>/quality_judge.py` (Create)
* **Type/Scope:** Python module — LLM critic, 1 call (Fork 1a).
* **Proposed changes:** `JUDGE_PROMPT` (single rubric, `prompt_ver` constant; OQ3 default = one prompt, one version). `judge_prose(prose, spec, provider, trace_id) -> verdict` (bounded JSON as above; parse/timeout → 1 retry → `verdict_error`). **No repair loop in v0** (R21).

#### 2. `<NE>/volume_knobs.py` (Create)
* **Type/Scope:** deterministic knob selection (G3).
* **Proposed changes:** `select_knobs(volume, stage) -> (knob_set, rationale)` reads the per-stage `knob_set` from `quality_config.json` (fully deterministic; **no LLM** — codebase F4). `regimes_for(chapter_range)` reads `concept_unlocks.json` for all active regime boundaries. `read_active_knobs(volume)` is the read API `expand_run` uses. **(M2):** rules engine + LLM climax classification (a new authority beside `concept_terms`, versioned `rationale.llm_classify_ver`) + `volume_reopen`.

#### 3. `<NE>/quality_store.py` (Create)
* **Type/Scope:** chain leaf (Fork 3a) — additive, versioned, no row mutation.
* **Proposed changes:** the canonical `quality_verdicts` DDL above; `record_verdict(chapter, verdict, prose_sha256, trace_id)` inserts `version=1` (idempotent: same verdict content returns existing hash); `verdict_for(chapter)` → latest version, verifies `quality_verdict_hash` vs `parent_hash` + `prose_sha256`; `re_verify(chapters, new_model)` inserts `version=N+1` (no-op when the latest leaf's `judge_model == new_model`); `budget_status()` → `SUM(usage.total_tokens)` (with `captured_at` window) vs cap.

#### 4. `<NE>/observability.py` (Create, v0 subset)
* **Type/Scope:** telemetry (G8, G9).
* **Proposed changes:** `new_trace_id()`; `telemetry` table (DDL above) + `record_call(trace_id, kind, tokens)` + `record_expansion_call(trace_id, run_id, total_tokens, model, prompt_ver) -> None` (writes a `telemetry` row `kind="expansion"` **and** a `usage` row); `budget_guard(cap, alert_pct=90)` → at `alert_pct` a cap-approach `telemetry` alert row, at 100% raise `QuotaExceeded` → runner halts with reason `"quota"`. **Guard + `status` read `usage`, not `telemetry`.** **(M2):** prompt cache, `drift_window`/`drift_alert` + paging.

#### 5. `src/novel_engine/config/quality_config.json` (Create)
* **Type/Scope:** versioned config (exact keys in Data contracts).
* **Proposed changes:** `enable_quality`; per-stage `knob_set`; `token_cap` + `alert_pct`; `rationale_max_chars=80`; `beat_coverage_threshold=0.6`; `judge{temperature:0, reasoning_effort:none}`. **Coexists** with the vestigial `quality_thresholds.json` (no code consumer; active source is `core/quality_policy.py` reading `runtime_config.json`); that file's deletion is a separate migration.

#### 6. `<NE>/accept_check.py` (Modify)
* `check_prose(prose, card, manifest, spec=None)` → new top-level key `"quality_notes"` (dim < knob → soft, **never blocking**; absent when `spec=None`). `run()` passes it through verbatim; `pass`/`verdict` stay computed from `blocking` only. word_count/metadata/beat unchanged (0.6 default, log-only confidence). `INFANT_SELF_AWARENESS` is a frozen authority shared with `expand_run` + `synonym_repair`.

#### 7. `<NE>/mark_verified.py` (Modify)
* Optional `--store` (a **root path**, opened read-only — never a fresh `Store`): when present, `mark_verified` calls `verdict_for(chapter)` and **refuses to stamp if the leaf hash/parent/`prose_sha256` fails**, then records `verification.verdict_record_hash` (the leaf's `quality_verdict_hash`) into the manifest; when omitted, `verdict_record_hash` is not set (the manifest still gets `external_expansion_verified=true`). If the latest leaf is a `verdict_error`, it still stamps compliance but sets `verdict_record_hash=null` + a log line. New `note_quality(package, --chapter)` stamps `manifest.quality_note` from the verdict leaf. Waiver unchanged (compliance-only). **`commit_override` is M2.**

#### 8. `<NE>/store.py` (Modify)
* **First `executescript` (unconditional, both new + existing DBs):** `ALTER TABLE usage ADD COLUMN captured_at` + `record_trace` → 4-column INSERT. **Second, `enable_quality`-gated `executescript`:** `quality_verdicts` + `chapters.schema_version` + the `retention` + `knob_overrides` DDL rows + bootstrap `schema_version` marker. `quality_store` is a module receiving the `Store.db` handle (no separate connection). Delegate `record_verdict(chapter, verdict, prose_sha256, trace_id)`/`verdict_for`/`re_verify`/`validate_quality_chain`/`budget_status`; `invalidate_from` adds `DELETE FROM quality_verdicts WHERE chapter >= ?` in the same transaction. **The `commit_override` `knob_overrides` table + `record_override` ship in v0 (un-chained staging; the runner verb is M2).** Core `chapters`/`operations`/`allowances` unchanged.

#### 9. `<NE>/expand_run.py` (Modify)
* `_llm` → returns `(content, usage_dict)` where `usage_dict = {total_tokens, prompt_tokens, completion_tokens, usage_estimated}` (if the provider omits `usage`, estimate CJK≈1/char, ASCII≈4/char + flag); all three internal call sites unpack the tuple; `record_expansion_call` after each. `expand_until_range` gains `trace_id` (caller-generated; `None` → internal), `spec` (the `knob_set`), and `store` (required when `spec` is not `None`); public return dict gains `trace_id` + `total_expansion_tokens`. Judge hook after loop (≤1 critic call; a parse retry makes 2). Final `check_prose` gets `spec` (intermediate rounds `spec=None`). Continuation / overshoot trim / `deterministic_repair` unchanged.

#### 10. `<NE>/runner.py` (Modify)
* Verbs `qc`, `knobs --show/--history`, `note-quality`; `status` += tokens/calls/wall + `verdict_pass_rate` (denominator excludes `verdict_error` leaves) + `verdict_error_rate`; `verify` → `validate_quality_chain()`. **`commit-override`, `what-if`, `quality-report` are M2** (listed only so the verb space is reserved).

#### 11. `<NE>/dual_check.py` (Modify)
* Reads `spec` from `read_active_knobs(volume)` and passes it to `check_prose`; the merged dict gains `"quality_notes": program_report.get("quality_notes", [])` at **both** return sites (fast-path `spec=None` → `[]`; LLM path `spec=knob_set` → may be present). `pass`/`verdict` remain computed from `blocking` only.

#### 12. `../tests/test_quality_layer.py` (Create)
* **Pinned assertions (stubbed provider, `tmp_path` fixtures, no real generated artifacts — per the superpowers test rule):**
  - `test_verdict_schema_hash`: `judge_prose` (stub) → verdict dict has all keys; `quality_verdict_hash == digest(record)`; `prose_sha256 == sha256(text)`.
  - `test_verdict_error_parse`: forced bad critic output → leaf `verdict_error.type="parse"`, no crash, `record_verdict` accepts it; `prose_sha256` is NULL for an error leaf.
  - `test_verdict_error_not_in_pass_rate`: a chapter whose latest leaf is a `verdict_error` → excluded from `status` `verdict_pass_rate` denominator, counted in `verdict_error_rate`; readable by `qc`.
  - `test_quality_notes_never_blocking`: a dim < knob → `quality_notes` non-empty, `blocking` empty; `mark_verified` succeeds (compliance clean).
  - `test_waiver_still_compliance_only`: a `cognition_boundary` block + waiver → `WAIVED`; a `quality_notes`-only report never needs a waiver.
  - `test_re_verify_new_version`: `re_verify` inserts `version=2`, `verdict_for` returns v2, v1 intact.
  - `test_invalidate_deletes_leaf`: `invalidate_from(N)` removes `quality_verdicts` rows `>= N`.
  - `test_knob_determinism_stage`: same volume+stage → identical `knob_set`; a **synthetic** volume straddling 仙门@653 (fixture, not a real `volumes.json` entry) → 2 knob sets (guards future re-cuts; current volumes do not cross a regime boundary).
  - `test_budget_guard_quota`: synthetic `usage` (the unconditional first `executescript` auto-runs the `captured_at` ALTER on any `Store(root)`) over cap at 90% → alert telemetry row; at 100% → `QuotaExceeded` → `"quota"` signal.
  - `test_chain_parent_intact`: after `record_verdict`, `store.prefix()`/`head()` still validate; leaf `parent_hash == chapters.hash`.

#### 13. `../tests/test_migration_additive.py` (Create)
* `test_two_populations`: existing `chapters` rows (`schema_version=0`, no leaf) + new dual-signal rows coexist; report splits on `accept_check_version >= 1`.
* `test_additive_no_row_mutation`: applying the quality DDL to a populated DB leaves every `chapters` row hash-identical; `schema_version` backfilled to 0 + a bootstrap `schema_version=1` marker written (the `migration_ledger` table is M2).
* `test_migration_existing_populated_db`: open an existing 3-column `usage` + populated `chapters` DB; the **unconditional** first `executescript` runs `ALTER TABLE usage ADD COLUMN captured_at` (4-column `record_trace` then works); the gated second `executescript` adds `quality_verdicts` only when `enable_quality=true`; no `chapters` row changes hash.
* `test_reopen_new_row_versions` (M2-gated, marked skip until `volume_reopen` lands).

### Verification Plan

* **Build/import:** `python -m compileall <NE>`; import each new module.
* **Unit tests:** `pytest src/novel_engine/tests/test_quality_layer.py src/novel_engine/tests/test_migration_additive.py -q` (assertions above).
* **Regression (additive-only guarantee):** full `pytest src/novel_engine/tests/ -q` — the condensed pipeline + `accept_check`/`mark_verified`/`store`/`dual_check` stay green. **Per the superpowers rule, the full suite must run before commit, not just the new tests.**
* **Contract/prompt consistency:** `pytest src/novel_engine/tests/test_contract_prompt_consistency.py`.
* **E2E smoke:** 1-chapter expand → `qc` → `note-quality` → `mark-verified` against a fixture package; assert: verdict leaf recorded (v1), `quality_notes` separate from `verified`, chain `parent_hash` intact, `usage` row written with `captured_at`, `trace_id` correlates the expand + critic calls, and a forced `verdict_error` leaf is readable by `qc` + excluded from `verdict_pass_rate`.
* **Calibration (deferred, M2):** judge–human agreement against the gold set (G7) is verified only after OQ1/OQ2 produce the retention feed + gold set; until then the judge runs as an uncalibrated meter with recorded `prompt_ver`.

---

*Drafted with EGM*
