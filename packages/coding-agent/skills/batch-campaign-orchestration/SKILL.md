---
name: batch-campaign-orchestration
description: Orchestrate a long LLM batch campaign (thousands of chapters) with idempotent resume, head-watermark monitoring, orphan-process recovery, and a stop-fix-restart protocol. Embeds the validator-driven auto-repair methodology and an optional plan-batch-synthesis pattern. Use when launching or recovering a multi-hour LLM campaign that must survive session gaps.
---

# Batch Campaign Orchestration

Run a long batch (the 3,800-chapter condensed pipeline, or any 1000+ LLM-call
campaign) so it survives session gaps, silent process deaths, and
validation-block stalls without losing committed work.

## Invariants

- **Committed prefix is immutable.** The campaign resumes from `MAX(number)`
  in `manuscript.sqlite3`; it never regenerates a committed chapter.
- **Progress lives in files, not in job output.** A running job's stdout is
  not a reliable progress signal (observed: 21h and 4h silent gaps).
  Monitor `status.json` mtime + `MAX(number)` instead.
- **Every block is stop-fix-restart.** The driver stops on the first
  unresolvable block, writes `failure.json`, and the operator extends the
  fix-table (see `novel-engine-repair`), then restarts. The restart is
  idempotent from the head.

## Head-watermark monitoring

Do **not** poll a running job's output to measure progress. Instead:

```python
import sqlite3, json
from pathlib import Path

DB = Path("manuscript.sqlite3")
def head():
    c = sqlite3.connect(str(DB))
    try:
        r = c.execute("SELECT MAX(number) FROM chapters").fetchone()
        return r[0] or 0
    finally:
        c.close()

# A "live" campaign = a running runner process AND a rising head.
# A "stalled" campaign = no runner process OR head unchanged for 30+ min.
# The watchdog relaunches on stall.
```

The watermark is the single source of truth. If a job dies mid-chapter, the
half-written draft stays in `drafts/chapter_NNNN.json` and the next restart
resumes from the head — no committed work is lost.

## Orphan-process recovery

A runner can die silently (session gap, OOM, timeout kill). Recovery:

1. `Get-CimInstance Win32_Process -Filter "Name like '%python%'"` filtered on
   the runner script name → is a live process there?
2. `SELECT MAX(number) FROM chapters` → has the head advanced in the last
   30 minutes?
3. Neither → relaunch the runner detached (`subprocess.Popen` with
   `stdout=log`), appending to a heartbeat log.

A watchdog (`watchdog.py <end> --loop 300`) does this every 5 minutes, so
the recovery is automatic and the operator only needs to notice when a block
keeps recurring.

## Stop-fix-restart protocol

The campaign driver stops on the first block it cannot auto-resolve:

```python
ok, tail = fix_chapter(n)
if ok:
    record_done(n)
else:
    write_failure_json(n, tail)   # persists for the operator
    print(f"ch{n} BLOCKED; extend the fix table, then rerun.")
    return 1
```

The operator (or a later run) extends the fix-table and restarts:

```bash
python auto_run.py 3800   # idempotent from head
```

The restart is cheap: already-fixed chapters are skipped, the scrub table is
reloaded fresh, and only the blocking chapter re-runs. This is the
convergent path — a new LLM-invented term is learned once and never blocks
again.

## Validator-driven auto-repair methodology

The campaign embeds a *validator-first* loop: generate → validate → repair
only the specific errors the validator named, not a whole-chapter
regeneration.

- **Generate** the candidate (plan → draft).
- **Validate** against the hard gates (title, story_day, 2–6 scenes, scene
  fields non-empty, cast ⊆ plan cast, dialogue_intents non-empty,
  `expanded_han` ≥ 300 and Σ == plan budget, beats non-empty, event refs in
  plan, no forbidden terms in `expansion_guidance`).
- **Repair** the named errors with deterministic actions (strip invalid
  refs, fill missing fields, scrub forbidden terms, resynthesize
  structural gaps).
- **Re-validate** once after a forbidden-term fix (the convergent retry).
- **Commit** with event + milestone evidence from the plan.

The validator is the oracle; the fixer is a deterministic translator from
validator errors to candidate edits. This is what makes the campaign
convergent at scale: the LLM invents new words, the fixer learns them, and
the table grows monotonically.

## Plan-batch-synthesis (optional)

When the plan LLM is slow or the campaign stalls on planning, synthesize
plans in aligned batches of 10 (the engine's `plan_batch`):

```python
def plan_slot(n, plan_batch=10):
    return ((n - 1) // plan_batch) * plan_batch   # 1-based start of the batch
```

Each plan row covers `plan_batch` chapters. If a chapter's plan row is
missing or its chapter entry is absent, resynthesize the whole batch from
the canon + context, align the budget, and commit the plan row. The
chapter-level draft then proceeds against the synthesized plan. This
decouples planning from drafting so a slow plan LLM does not block the
drafting pipeline.

Use this only when the plan LLM is the bottleneck; the normal path is the
plan LLM producing aligned 10-chapter batches.

## Resumable campaign template

```python
for n in range(from_ch, end + 1):
    if n in done:                     # already completed this campaign
        continue
    ok, tail = process(n)
    if ok:
        done.add(n); save_done(done)
    else:
        write_failure(n, tail)
        break                         # stop-fix-restart; restart is idempotent
```

`done` is persisted to a progress file (`expand_progress.json` or
`campaign_progress.json`) so a restarted driver skips finished work.

## Watchdog template

```python
while True:
    if all(n in done for n in range(frm, end+1)):
        log("campaign complete"); break
    if live_runner_pids():
        log("live"); time.sleep(loop); continue
    log("stalled; relaunching")
    relaunch_runner(frm, end)         # detached, heartbeat-logged
    time.sleep(loop)
```

The watchdog is the only thing that must survive; the runner itself can
die freely because restart is idempotent.

## Verification

After the campaign:

- `MAX(number)` == target (3800).
- `exports` row count == head (every committed chapter exported).
- `verify --output <root>` runs clean.
- `status.json` `worker.state == "complete"`.
