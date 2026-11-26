---
name: novel-engine-repair
description: Operator methodology for repairing halted novel-engine condensed campaigns. Use when a batch generation has stopped on a validation error, a forbidden-term hit, a structural gap, or a silently-dead job, and you need to triage, fix, and restart idempotently. The skill codifies the 6-category triage decision tree, the scrub-table hot-reload protocol, and the stop-fix-restart recovery loop.
---

# Novel-Engine Repair

Repair a halted condensed campaign without losing committed progress. The core
invariant: committed chapters never change. Everything below happens on the
*next-to-commit* chapter (the head+1 slot) and on the *scrub table* that
future fixes share.

## Inputs

- `output_root` — campaign dir containing `manuscript.sqlite3`, `status.json`,
  `drafts/chapter_NNNN.json`, and `failure.json` (the last halted state).
- `source_root` — engine source, e.g.
  `.../packages/coding-agent/skills/novel-engine/src`.
- `scrub_terms.json` — persistent replacement table at the campaign root
  (created on first run; see "Hot-reload" below).

## Triage decision tree

Read `failure.json` (or `status.json` worker state) to find the failed chapter
`n` and its last error string. Classify into one of 6 buckets:

| Bucket | Trigger (regex on error string) | Fix action | Attempts |
|---|---|---|---|
| `forbidden_term` | `含禁词(\[...\])` | `learn_terms_from_errors` → re-scrub → re-validate | 12 |
| `cast` | `缺少人物` / `cast` / `对话意图` | wrap `dialogue_intents` in list, default `cast` to plan cast, drop out-of-plan extras | 12 |
| `budget` | `扩写预算` / `expansion_weight` / `expansion_guidance` missing | default `expansion_weight=2`, neutral `expansion_guidance` text | 12 |
| `title` | `标题` / `章节编号或标题错误` | default `title = f"第{n}章"` | 12 |
| `structural` | `缺少` / `场景` / `事件` / `beat` / `state_changes` / `ending` | resynthesize the missing structural pieces from `ch_plan`; resynthesize the whole candidate if draft is unreadable | 12 |
| `unknown` | anything else | generic scrub + resynthesize fallback | **2** (cap) |

The `unknown` cap is deliberate: unknown-category failures are usually a new
failure mode that no known fixer action addresses; stop fast so the operator
can read the error and extend the table, rather than burning 12 LLM attempts
on a guess.

## Hot-reload scrub table

The table is a JSON file at the campaign root:

```json
{
  "repl": [["排异", "抗拒"], ...],
  "neutral": {"气": "体感", "息": "呼吸", "频": "节奏", ...}
}
```

- `repl` — ordered `[source, replacement]` pairs; applied top-down to every
  string leaf of the candidate and (separately) to the expanded prose text.
- `neutral` — stem-keyed fallback used when a new forbidden term is learned:
  the first stem that appears inside the term wins; otherwise `现象`.

`fix_auto.py` reloads the table **fresh on every commit attempt** and
persists any newly-learned term back to the file. The campaign never
restarts between fixes; the table itself is the durable state.

The `forbidden_term` triage path in `commit_fixed` calls
`learn_terms_from_errors` → re-scrubs → re-validates once. That single retry
is the convergent path: the LLM invents a new word once, we learn it, and it
never blocks again.

## Stop-fix-restart protocol

A campaign that silently died (21h / 4h session gaps observed) is detected by
two signals:

1. `status.json` `worker.state == "interrupted"` or `halted` and no live PID.
2. `manuscript.sqlite3` `MAX(number)` has not advanced for > 30 minutes
   while the runner process is gone.

Recovery is idempotent:

```bash
# 1. Confirm the head (committed watermark) — never regenerate committed chapters.
sqlite3 manuscript.sqlite3 "SELECT MAX(number) FROM chapters;"

# 2. Restart auto_run from that head; it is a no-op on already-committed chapters.
python auto_run.py 3800
```

`auto_run.py` reads `failure.json` (if present), classifies via
`triage_category(n)`, and applies the `max_fix_attempts` budget accordingly.
A watchdog (`watchdog.py <end> --loop 300`) runs alongside and relaunches
`auto_run.py` on death, so the recovery is automatic.

## Repair action catalog (fix_auto.py)

Concrete actions the fixer applies, in order:

1. **Strip invalid event refs** from every beat's `event_ids` (must be in the
   plan's `events` set).
2. **Cover missing plan events** by appending beats to the last scene.
3. **Scrub** every beat's `action`/`cause`/`outcome` and every scene's
   `start`/`end`/`emotion`/`expansion_guidance` with the current `repl` table.
4. **Resynthesize structural gaps**:
   - <2 scenes → append a synthetic `S2` with plan cast.
   - Empty `cast` → plan cast.
   - String `dialogue_intents` → wrap in list.
   - Missing `expansion_weight` → 2.
   - Missing `expansion_guidance` → neutral text.
   - Missing `state_changes` / `ending` → synthesize from plan.
   - Missing `title` → `第{n}章`.
5. **Constrain cast** to plan cast (drop out-of-plan named extras).
6. **Re-scrub** the whole candidate after structural synthesis.
7. **Validate** (`validate_chapter` + `validate_timeline`). On `forbidden_term`
   hit: learn + re-scrub + re-validate once.
8. **Commit** with a synthetic `review` that carries event + milestone
   evidence from the plan.

The catalog is deterministic: the same failure input always produces the same
candidate, so a restart from the head is safe.

## When the campaign is still blocked after repair

If a chapter still fails after 12 attempts in a non-`unknown` bucket (or 2 in
`unknown`), the stop-fix-restart loop is correct behavior: write
`failure.json`, let the operator read it, and either:

- Extend `scrub_terms.json` manually (a new LLM-invented term not yet in the
  `neutral` stem map).
- Patch the specific chapter's draft in `drafts/chapter_NNNN.json` by hand.
- Restart `auto_run.py 3800` — the fixer will re-run from the head and the
  extended table will unblock.

Never hand-edit committed chapters; the committed prefix is the watermark.

## Verification

After a repair campaign, confirm:

- `MAX(number) FROM chapters` advanced to the expected head.
- `SELECT COUNT(*) FROM exports e JOIN chapters c ON e.number=c.number
   AND e.source_hash=c.hash` equals the head (every committed chapter is
  exported).
- `python -m novel_engine.condensed verify --output <root>` runs clean.
