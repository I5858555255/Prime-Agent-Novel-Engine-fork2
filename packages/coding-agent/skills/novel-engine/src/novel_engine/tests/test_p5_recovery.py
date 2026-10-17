"""P5: Checkpoint recovery, idempotency, and resume semantics tests."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.core.checkpoint import CheckpointManager, novel_chapter_path
from novel_engine.pipeline.chapter_status import set_status, get_status, COMMITTED, HALTED


class TestCheckpointManager:
    """Checkpoint write/read/restore semantics."""

    def test_load_returns_empty_when_no_file(self, tmp_path):
        mgr = CheckpointManager(project_root=tmp_path)
        data = mgr.load()
        assert data == {"checkpoints": [], "last_complete": None}

    def test_create_and_load_checkpoint(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        mgr = CheckpointManager(project_root=tmp_path)
        cp = mgr.create_checkpoint(
            chapter=1,
            novel_content="Chapter 1 content\n",
            synopsis_content="Synopsis\n",
            outline_content="{}",
            world_state_snapshot={"characters": {}},
        )
        assert "novel_hash" in cp
        assert cp["chapter"] == 1

        data = mgr.load()
        assert len(data["checkpoints"]) == 1
        assert data["checkpoints"][0]["chapter"] == 1

    def test_checkpoint_is_atomic_write(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        mgr = CheckpointManager(project_root=tmp_path)
        mgr.create_checkpoint(
            chapter=1, novel_content="x", synopsis_content="y",
            outline_content="z", world_state_snapshot={},
        )
        tmp_files = list(tmp_path.rglob("*.tmp"))
        assert len(tmp_files) == 0

    def test_multiple_checkpoints_accumulate(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        mgr = CheckpointManager(project_root=tmp_path)
        for ch in range(1, 4):
            mgr.create_checkpoint(
                chapter=ch, novel_content=f"content_{ch}",
                synopsis_content="", outline_content="", world_state_snapshot={},
            )
        data = mgr.load()
        assert len(data["checkpoints"]) == 3
        assert data["checkpoints"][-1]["chapter"] == 3

    def test_novel_chapter_path_construction(self, tmp_path):
        p = novel_chapter_path(tmp_path, 5)
        assert p == tmp_path / "chapters" / "novel" / "chapter_5.txt"


class TestChapterStatus:
    """COMMITTED/HALTED status tracking."""

    def test_set_and_get_committed_status(self, tmp_path):
        set_status(tmp_path, 1, COMMITTED)
        s = get_status(tmp_path, 1)
        assert s == COMMITTED

    def test_get_unset_chapter_returns_none(self, tmp_path):
        s = get_status(tmp_path, 999)
        assert s is None

    def test_halt_and_resume(self, tmp_path):
        set_status(tmp_path, 1, HALTED)
        assert get_status(tmp_path, 1) == HALTED
        set_status(tmp_path, 1, COMMITTED)
        assert get_status(tmp_path, 1) == COMMITTED


class TestIdempotency:
    """Duplicate operations should not corrupt state."""

    def test_double_commit_is_safe(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        mgr = CheckpointManager(project_root=tmp_path)
        cp1 = mgr.create_checkpoint(
            chapter=1, novel_content="same content",
            synopsis_content="", outline_content="", world_state_snapshot={},
        )
        cp2 = mgr.create_checkpoint(
            chapter=1, novel_content="same content",
            synopsis_content="", outline_content="", world_state_snapshot={},
        )
        assert cp1["novel_hash"] == cp2["novel_hash"]

    def test_different_content_different_hash(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        mgr = CheckpointManager(project_root=tmp_path)
        cp1 = mgr.create_checkpoint(
            chapter=1, novel_content="version A",
            synopsis_content="", outline_content="", world_state_snapshot={},
        )
        cp2 = mgr.create_checkpoint(
            chapter=1, novel_content="version B",
            synopsis_content="", outline_content="", world_state_snapshot={},
        )
        assert cp1["novel_hash"] != cp2["novel_hash"]

    def test_resume_skips_done_chapters(self, tmp_path):
        from novel_engine.pipeline.production_runner import load_resume_state
        resume_path = tmp_path / "runtime" / "resume_state.json"
        resume_path.parent.mkdir(parents=True, exist_ok=True)
        resume_path.write_text(json.dumps({"done": [1, 2, 3], "last_success_chapter": 3}))
        state = load_resume_state(tmp_path)
        assert state["done"] == [1, 2, 3]
        assert state["last_success_chapter"] == 3


class TestCheckpointRecovery:
    """Verify checkpoint-based recovery after interruption."""

    def test_load_last_complete(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        mgr = CheckpointManager(project_root=tmp_path)
        mgr.create_checkpoint(
            chapter=5, novel_content="ch5", synopsis_content="",
            outline_content="", world_state_snapshot={},
        )
        data = mgr.load()
        assert data["last_complete"] == 5

    def test_partial_checkpoint_raises_decode_error(self, tmp_path):
        """Malformed checkpoint raises JSONDecodeError — caller must handle."""
        (tmp_path / "runtime").mkdir()
        cp = tmp_path / "runtime" / "checkpoint.json"
        cp.write_text('{"checkpoints": [{"chapter": 3}', encoding="utf-8")
        mgr = CheckpointManager(project_root=tmp_path)
        with pytest.raises(json.decoder.JSONDecodeError):
            mgr.load()

    def test_empty_checkpoint_returns_empty_dict(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        cp = tmp_path / "runtime" / "checkpoint.json"
        cp.write_text("{}", encoding="utf-8")
        mgr = CheckpointManager(project_root=tmp_path)
        data = mgr.load()
        assert data == {}

    def test_checkpoint_has_complete_structure(self, tmp_path):
        (tmp_path / "runtime").mkdir()
        mgr = CheckpointManager(project_root=tmp_path)
        cp = mgr.create_checkpoint(
            chapter=1, novel_content="test", synopsis_content="",
            outline_content="", world_state_snapshot={},
        )
        assert "chapter" in cp
        assert "novel_hash" in cp
        assert "synopsis_hash" in cp
        assert "outline_hash" in cp
        assert "world_state_hash" in cp
        assert "timestamp" in cp
        assert cp["complete"] is True
