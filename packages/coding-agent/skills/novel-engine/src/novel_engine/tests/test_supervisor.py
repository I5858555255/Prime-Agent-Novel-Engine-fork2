# -*- coding: utf-8 -*-
"""Supervisor policy + orchestration tests (no API calls)."""
from pathlib import Path

from novel_engine.pipeline.supervisor import decide_next_action, supervise
from novel_engine.pipeline.production_runner import last_success_chapter, save_resume_state
from novel_engine.pipeline.chapter_status import write_halt_reason, load_halt_reason


# --------------------------------------------------------------------------
# Pure decision policy
# --------------------------------------------------------------------------

def test_finish_when_target_reached():
    d = decide_next_action(3, 2, 3, None, {}, 0)
    assert d["action"] == "finish"
    assert d["chapter"] == 3


def test_halted_chapter_retries_with_backoff_then_circuit_breaks():
    halt = {"failed_chapter": 1, "reason": "review_below_publication_line"}
    d1 = decide_next_action(10, 0, 0, halt, {}, 0)
    assert d1["action"] == "retry" and d1["cooldown"] == 300 and d1["attempts"] == {1: 1}
    d2 = decide_next_action(10, 0, 0, halt, d1["attempts"], 0)
    assert d2["cooldown"] == 600 and d2["attempts"] == {1: 2}
    d3 = decide_next_action(10, 0, 0, halt, d2["attempts"], 0)
    assert d3["cooldown"] == 900 and d3["attempts"] == {1: 3}
    d4 = decide_next_action(10, 0, 0, halt, d3["attempts"], 0)
    assert d4["action"] == "abort"


def test_progress_resets_stale_chapter_counters():
    # ch1 had earlier failures; this batch committed ch1..2 and halted on ch3
    halt = {"failed_chapter": 3, "reason": "review_below_publication_line"}
    d = decide_next_action(10, 0, 2, halt, {1: 2, 2: 1}, 0)
    assert d["action"] == "retry"
    assert d["chapter"] == 3
    assert d["attempts"] == {3: 1}  # old counters dropped, frontier counted fresh
    assert d["stalls"] == 0


def test_premature_exit_without_halt_is_bounded():
    d1 = decide_next_action(10, 0, 0, None, {}, 0, stall_limit=3)
    assert d1["action"] == "retry" and d1["stalls"] == 1 and d1["cooldown"] == 60
    d2 = decide_next_action(10, 0, 0, None, {}, d1["stalls"], stall_limit=3)
    assert d2["stalls"] == 2
    d3 = decide_next_action(10, 0, 0, None, {}, d2["stalls"], stall_limit=3)
    assert d3["stalls"] == 3
    d4 = decide_next_action(10, 0, 0, None, {}, d3["stalls"], stall_limit=3)
    assert d4["action"] == "abort"


# --------------------------------------------------------------------------
# Orchestration with an injected fake batch
# --------------------------------------------------------------------------

def _root(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "runtime").mkdir(parents=True, exist_ok=True)
    return r


def test_supervise_finishes_in_one_batch(tmp_path):
    r = _root(tmp_path)
    calls = []

    def batch(target):
        calls.append(target)
        save_resume_state(r, list(range(1, target + 1)))
        return 0

    out = supervise(r, target=3, run_batch=batch, sleep=lambda s: None, log=lambda *a: None)
    assert out["action"] == "finish"
    assert last_success_chapter(r) == 3
    assert len(calls) == 1


def test_supervise_retries_halted_chapter_then_finishes(tmp_path):
    r = _root(tmp_path)
    sleeps = []
    state = {"n": 0}

    def batch(target):
        state["n"] += 1
        if state["n"] == 1:
            write_halt_reason(r, 1, "review_below_publication_line", detail="score=85")
        else:
            save_resume_state(r, list(range(1, target + 1)))
        return 0

    out = supervise(r, target=3, run_batch=batch,
                    sleep=lambda s: sleeps.append(s), log=lambda *a: None)
    assert out["action"] == "finish"
    assert state["n"] == 2
    assert sleeps == [300]
    # halt marker cleared on the retry path so it is never re-read
    assert load_halt_reason(r) is None


def test_supervise_circuit_breaks_on_repeated_same_chapter(tmp_path):
    r = _root(tmp_path)
    count = {"n": 0}

    def batch(target):
        count["n"] += 1
        write_halt_reason(r, 1, "review_below_publication_line", detail="score=85")
        return 0

    out = supervise(r, target=3, run_batch=batch,
                    sleep=lambda s: None, log=lambda *a: None,
                    backoff=(1, 2, 3), max_per_chapter=3)
    assert out["action"] == "abort"
    assert count["n"] == 4  # initial + 3 retries
    # on abort the marker is retained for human inspection
    assert load_halt_reason(r) is not None
    # cursor never advanced past 0
    assert last_success_chapter(r) == 0
