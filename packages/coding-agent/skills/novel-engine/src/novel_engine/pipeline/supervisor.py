"""Unattended production supervisor (CC pkg C3).

The runner exits with status 0 both on clean completion and on a mid-batch HALT
("COMPLETED WITH ISSUES"), so a watchdog cannot rely on the process exit code.
This supervisor treats the runner-owned resume cursor (which only ever records
checkpoint-verified chapters) plus HALT_REASON.json as the source of truth:

- target reached (last committed chapter >= target)  -> stop, success
- batch halted on a chapter below the publication line / a transient error
  -> wait a backoff, then re-invoke the runner; resume is idempotent so the
     failed chapter is regenerated and committed chapters are skipped
- the same chapter keeps halting -> circuit break after max_per_chapter tries
- process ends without a HALT marker and without progress -> bounded retries,
  then circuit break (guards against silent crashes / cost runaway)

The decision is a pure function (decide_next_action) so the policy is unit
tested without any API calls; the subprocess loop lives in supervise().
"""
from __future__ import annotations

import os
import sys
import subprocess
from pathlib import Path

from novel_engine.pipeline.chapter_status import load_halt_reason, clear_halt_reason
from novel_engine.pipeline.production_runner import last_success_chapter

DEFAULT_BACKOFF = (300, 600, 900)  # seconds: 1st, 2nd, 3rd retry of the same chapter
DEFAULT_STALL_COOLDOWN = 60        # premature exit without a HALT marker


def decide_next_action(target, last_before, last_after, halt, attempts, stalls,
                       max_per_chapter=3, backoff=DEFAULT_BACKOFF,
                       stall_limit=3):
    """Pure policy. Returns a dict describing the next move.

    Returns keys: action ("finish"|"retry"|"abort"), chapter, reason,
    cooldown (seconds, for retry), attempts (updated), stalls (updated).
    """
    attempts = dict(attempts or {})
    stalls = int(stalls or 0)
    progressed = last_after > last_before
    if progressed:
        # Chapters that committed during this batch are done; only a halt on the
        # current frontier chapter may still carry a retry counter.
        frontier = last_after + 1
        attempts = {k: v for k, v in attempts.items() if int(k) == frontier}

    if last_after >= int(target):
        return {"action": "finish", "chapter": last_after,
                "reason": f"reached target {target} (last committed {last_after})",
                "cooldown": 0, "attempts": attempts, "stalls": 0}

    halted_chapter = int(halt["failed_chapter"]) if halt else last_after + 1
    halt_reason = str(halt.get("reason", "")) if halt else ""

    if halt:
        n = attempts.get(halted_chapter, 0) + 1
        attempts[halted_chapter] = n
        if n > int(max_per_chapter):
            return {"action": "abort", "chapter": halted_chapter,
                    "reason": f"chapter {halted_chapter} halted {n} times "
                              f"(last reason: {halt_reason}); circuit breaker",
                    "cooldown": 0, "attempts": attempts, "stalls": 0}
        cooldown = tuple(backoff)[min(n - 1, len(tuple(backoff)) - 1)]
        return {"action": "retry", "chapter": halted_chapter,
                "reason": f"chapter {halted_chapter} halted ({halt_reason}); retry {n}/{max_per_chapter}",
                "cooldown": int(cooldown), "attempts": attempts, "stalls": 0}

    # Runner ended before target without writing a HALT marker (silent crash /
    # killed process). Bounded retries with no progress, then give up.
    stalls += 1
    if stalls > int(stall_limit):
        return {"action": "abort", "chapter": last_after + 1,
                "reason": f"runner exited {stalls}x without progress and without HALT marker; circuit breaker",
                "cooldown": 0, "attempts": attempts, "stalls": stalls}
    return {"action": "retry", "chapter": last_after + 1,
            "reason": "runner ended before target with no HALT marker; restarting",
            "cooldown": DEFAULT_STALL_COOLDOWN, "attempts": attempts, "stalls": stalls}


def engine_src_root() -> Path:
    # this file: <src>/novel_engine/pipeline/supervisor.py -> <src>
    return Path(__file__).resolve().parents[2]


def load_dotenv_into_environ(env_path: Path) -> None:
    """Minimal KEY=value .env loader; existing environment variables win."""
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def run_batch_subprocess(target: int) -> int:
    """Invoke one runner batch to `target` chapters in idempotent resume mode.
    Resume checkpoint 0 lets the runner self-detect verified progress on disk."""
    src = engine_src_root()
    load_dotenv_into_environ(src / "novel_engine" / ".env")
    cmd = [sys.executable, "-m", "novel_engine.pipeline.production_runner",
           str(int(target)), "1", "0", "--real"]
    proc = subprocess.run(cmd, cwd=str(src), env=dict(os.environ))
    return proc.returncode


def supervise(project_root, target, run_batch=run_batch_subprocess,
              sleep=__import__("time").sleep, log=print,
              max_per_chapter=3, backoff=DEFAULT_BACKOFF, stall_limit=3,
              max_total_loops=None):
    """Loop runner batches until target, retryable HALT, or circuit break.

    `run_batch(target) -> exit_code` is injected for unit tests. Returns the
    final decision dict. Never clears committed progress; resume is idempotent.
    """
    root = Path(project_root)
    attempts: dict = {}
    stalls = 0
    loop = 0
    while True:
        loop += 1
        if max_total_loops is not None and loop > max_total_loops:
            return {"action": "abort", "chapter": last_success_chapter(root),
                    "reason": "supervisor loop cap reached", "cooldown": 0,
                    "attempts": attempts, "stalls": stalls}
        before = last_success_chapter(root)
        log(f"[supervisor] batch {loop} start (target={target}, last_committed={before})")
        run_batch(target)
        after = last_success_chapter(root)
        halt = load_halt_reason(root)
        decision = decide_next_action(
            target, before, after, halt, attempts, stalls,
            max_per_chapter=max_per_chapter, backoff=backoff, stall_limit=stall_limit)
        attempts = decision["attempts"]
        stalls = decision["stalls"]
        log(f"[supervisor] {decision['action'].upper()}: {decision['reason']} "
            f"(last_committed={after})")
        if decision["action"] == "finish":
            return decision
        if decision["action"] == "abort":
            return decision
        clear_halt_reason(root)
        if decision["cooldown"] > 0:
            sleep(decision["cooldown"])


def default_project_root() -> Path:
    # this file: <src>/novel_engine/pipeline/supervisor.py -> <src>/novel_engine
    return Path(__file__).resolve().parents[1]


if __name__ == "__main__":
    import json as _json
    _root = default_project_root()
    if len(sys.argv) > 1:
        _target = int(sys.argv[1])
    else:
        _cfg = _json.loads((_root / "config" / "runtime_config.json").read_text(encoding="utf-8"))
        _target = int(_cfg.get("pipeline", {}).get("total_chapters", 3800))
    _result = supervise(_root, _target)
    sys.exit(0 if _result["action"] == "finish" else 2)
