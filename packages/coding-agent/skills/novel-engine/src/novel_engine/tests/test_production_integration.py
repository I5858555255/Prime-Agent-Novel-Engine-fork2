"""Tests for chapter commit gate, HALT, and metrics integration."""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.pipeline.chapter_status import (
    set_status, get_status, last_committed, write_halt_reason,
    COMMITTED, HALTED, clear_halt_reason
)
from novel_engine.core.call_metrics import reset as reset_metrics, snapshot as get_metrics_snapshot, record_call


def test_gate_blocks_when_prev_not_committed(tmp_path):
    """Simulate the gate check: prev chapter not COMMITTED → halt."""
    from novel_engine.pipeline.production_runner import is_chapter_committed
    from novel_engine.pipeline.chapter_status import get_status, COMMITTED

    project_root = tmp_path / "project"
    (project_root / "runtime").mkdir(parents=True)

    # Chapter 1 is GENERATING (not committed)
    set_status(project_root, 1, "GENERATING")

    # Gate check: for i=2, prev=1, status=GENERATING → not committed
    prev_status = get_status(project_root, 1)
    prev_committed = (prev_status == COMMITTED)
    if prev_status is None:
        prev_committed = is_chapter_committed(project_root, 1)

    assert prev_committed is False, "Chapter 1 should NOT be considered committed"


def test_gate_allows_when_prev_committed(tmp_path):
    """Simulate the gate check: prev chapter COMMITTED → proceed."""
    from novel_engine.pipeline.chapter_status import get_status, COMMITTED

    project_root = tmp_path / "project"
    (project_root / "runtime").mkdir(parents=True)

    # Chapter 1 is COMMITTED
    set_status(project_root, 1, COMMITTED)

    prev_status = get_status(project_root, 1)
    prev_committed = (prev_status == COMMITTED)

    assert prev_committed is True, "Chapter 1 should be considered committed"


def test_halt_reason_written_on_failure(tmp_path):
    """When a chapter fails, HALT_REASON.json is written."""
    from novel_engine.pipeline.chapter_status import write_halt_reason, load_halt_reason

    project_root = tmp_path / "project"
    (project_root / "runtime").mkdir(parents=True)

    write_halt_reason(project_root, 2, "review_exhausted", detail="score too low")
    halt = load_halt_reason(project_root)

    assert halt is not None
    assert halt["failed_chapter"] == 2
    assert halt["reason"] == "review_exhausted"
    assert halt["detail"] == "score too low"
    assert "timestamp" in halt


def test_metrics_accumulator_in_report(tmp_path):
    """Metrics accumulator should reflect recorded calls."""
    reset_metrics()

    record_call(phase="scenes", model="m1", success=True, failover=False,
                prompt_tokens=100, completion_tokens=50, reasoning_tokens=10,
                latency_s=0.5, cost_usd=0.01)
    record_call(phase="review", model="m1", success=True, failover=False,
                prompt_tokens=80, completion_tokens=30, reasoning_tokens=5,
                latency_s=0.3, cost_usd=0.005)

    snap = get_metrics_snapshot()
    # total_tokens = prompt + completion (not including reasoning)
    assert snap["calls"] == 2
    assert snap["prompt_tokens"] == 180  # 100 + 80
    assert snap["completion_tokens"] == 80  # 50 + 30
    assert snap["total_tokens"] == 260  # 180 + 80
    assert snap["cost_usd"] == 0.015


def test_float_score_boundary(tmp_path):
    """Score comparisons use float, not int truncation."""
    from novel_engine.pipeline.quality_gate import evaluate_publish
    from novel_engine.core.quality_policy import DEFAULT_POLICY

    # score=88.3 >= publication_line=88 → publish
    r = evaluate_publish(score=88.3, reviewer_issues=[], det_hard=[], leak=[],
                         violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True

    # score=87.9 < publication_line=88 → no publish
    r = evaluate_publish(score=87.9, reviewer_issues=[], det_hard=[], leak=[],
                         violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is False

    # score=88.0 == publication_line=88 → publish (boundary)
    r = evaluate_publish(score=88.0, reviewer_issues=[], det_hard=[], leak=[],
                         violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True

    # score=82.5 in 82-87 range → pending_human_review (not force draft)
    # This is tested indirectly via the orchestrator logic


def test_last_committed_with_gap(tmp_path):
    """When chapters 1,3 are COMMITTED but 2 is not, last_committed=3."""
    from novel_engine.pipeline.chapter_status import last_committed, set_status, COMMITTED

    project_root = tmp_path / "project"
    (project_root / "runtime").mkdir(parents=True)

    set_status(project_root, 1, COMMITTED)
    set_status(project_root, 2, HALTED)
    set_status(project_root, 3, COMMITTED)

    assert last_committed(project_root) == 3


def test_compatible_migration_no_status_file(tmp_path):
    """When no chapter_status.json exists, get_status returns None."""
    from novel_engine.pipeline.chapter_status import get_status

    project_root = tmp_path / "project"
    (project_root / "runtime").mkdir(parents=True)

    # No status file exists
    assert get_status(project_root, 1) is None
    assert get_status(project_root, 5) is None


def test_atomic_write_preserves_other_chapters(tmp_path):
    """Atomic write should not lose other chapters' status."""
    from novel_engine.pipeline.chapter_status import set_status, get_status, COMMITTED, GENERATING

    project_root = tmp_path / "project"
    (project_root / "runtime").mkdir(parents=True)

    # Set multiple chapters
    set_status(project_root, 1, COMMITTED)
    set_status(project_root, 2, GENERATING)
    set_status(project_root, 3, COMMITTED)

    # Update chapter 2
    set_status(project_root, 2, COMMITTED)

    # All should still be present
    assert get_status(project_root, 1) == COMMITTED
    assert get_status(project_root, 2) == COMMITTED
    assert get_status(project_root, 3) == COMMITTED
