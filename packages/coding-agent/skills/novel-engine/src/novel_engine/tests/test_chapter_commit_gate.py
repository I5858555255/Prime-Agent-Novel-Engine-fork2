"""Tests for pipeline/chapter_status.py — atomic writes, gate, HALT, resume."""
import json
import time
from pathlib import Path
from novel_engine.pipeline.chapter_status import (
    get_status, set_status, last_committed, write_halt_reason,
    load_halt_reason, clear_halt_reason, COMMITTED, HALTED, PENDING, GENERATING
)


def _make_tmp_root(tmp_path: Path) -> Path:
    """Create a minimal project root with runtime dir."""
    root = tmp_path / "project"
    (root / "runtime").mkdir(parents=True)
    return root


def test_set_status_atomic_write(tmp_path):
    root = _make_tmp_root(tmp_path)
    set_status(root, 1, COMMITTED)
    assert get_status(root, 1) == COMMITTED


def test_multiple_sets_preserve_other_chapters(tmp_path):
    root = _make_tmp_root(tmp_path)
    set_status(root, 1, COMMITTED)
    set_status(root, 2, GENERATING)
    set_status(root, 3, HALTED)
    assert get_status(root, 1) == COMMITTED
    assert get_status(root, 2) == GENERATING
    assert get_status(root, 3) == HALTED


def test_last_committed_returns_highest(tmp_path):
    root = _make_tmp_root(tmp_path)
    set_status(root, 1, COMMITTED)
    set_status(root, 3, COMMITTED)
    set_status(root, 2, GENERATING)
    assert last_committed(root) == 3  # Chapter 3 is the highest COMMITTED
    set_status(root, 2, COMMITTED)
    assert last_committed(root) == 3  # Still 3 (highest)
    set_status(root, 3, HALTED)  # Change 3 to HALTED
    assert last_committed(root) == 2


def test_last_committed_none_when_nothing_committed(tmp_path):
    root = _make_tmp_root(tmp_path)
    set_status(root, 1, GENERATING)
    assert last_committed(root) is None


def test_halt_reason_write_and_load(tmp_path):
    root = _make_tmp_root(tmp_path)
    write_halt_reason(root, 2, "review_exhausted", detail="score too low")
    reason = load_halt_reason(root)
    assert reason is not None
    assert reason["failed_chapter"] == 2
    assert reason["reason"] == "review_exhausted"
    assert reason["detail"] == "score too low"
    assert "timestamp" in reason
    assert reason["last_committed"] is None  # No committed chapters yet
    clear_halt_reason(root)
    assert load_halt_reason(root) is None


def test_compatible_migration_from_checkpoint(tmp_path):
    """When chapter_status.json is absent, verify_integrity should be used.
    get_status returns None for missing entries — caller must fall back."""
    root = _make_tmp_root(tmp_path)
    # No status file exists
    assert get_status(root, 1) is None
    # After writing one, it's tracked
    set_status(root, 1, COMMITTED)
    assert get_status(root, 1) == COMMITTED


def test_status_file_is_valid_json(tmp_path):
    root = _make_tmp_root(tmp_path)
    set_status(root, 1, COMMITTED)
    status_file = root / "runtime" / "chapter_status.json"
    data = json.loads(status_file.read_text(encoding="utf-8"))
    assert "chapters" in data
    assert data["chapters"]["1"] == {"status": COMMITTED}
