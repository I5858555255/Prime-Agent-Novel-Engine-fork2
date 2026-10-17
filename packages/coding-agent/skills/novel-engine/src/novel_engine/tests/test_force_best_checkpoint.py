# -*- coding: utf-8 -*-
"""Regression test for force-best draft not writing checkpoint.

Bug: force-best branch (pipeline_orchestrator L1175) saved to draft/ but
never wrote a checkpoint entry, causing run_volume --resume to re-run the
chapter on the next pass.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.core.checkpoint import CheckpointManager


def _make_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for d in ("config", "runtime", "chapters/draft", "chapters/novel",
              "chapters/outline", "chapters/synopsis"):
        (root / d).mkdir(parents=True, exist_ok=True)
    return root


def test_force_best_draft_checkpoint_written(tmp_path):
    """force-best path writes a checkpoint with mode=force_best_draft."""
    root = _make_project(tmp_path)
    cm = CheckpointManager(root)

    draft_content = "Chapter 5 force-best draft text."
    cm.create_draft_checkpoint(chapter=5, draft_content=draft_content)

    entry = cm.get_checkpoint_by_chapter(5)
    assert entry is not None
    assert entry["chapter"] == 5
    assert entry["complete"] is True
    assert entry["mode"] == "force_best_draft"
    assert entry["synopsis_hash"] == ""
    assert entry["outline_hash"] == ""
    assert entry["world_state_hash"] == ""


def test_force_best_draft_checkpoint_skips_resume(tmp_path):
    """run_volume._get_committed_chapters should include force_best_draft entries."""
    from novel_engine.scripts.run_volume import _get_committed_chapters
    import logging

    root = _make_project(tmp_path)
    cm = CheckpointManager(root)
    cm.create_draft_checkpoint(chapter=3, draft_content="draft content")

    # Normal commit also works
    cm.create_checkpoint(
        chapter=1,
        novel_content="novel text",
        synopsis_content=json.dumps({"chapter_num": 1}),
        outline_content=json.dumps({"chapter_num": 1}),
        world_state_snapshot={},
    )

    logger = logging.getLogger("test_force_best")
    committed = _get_committed_chapters(logger, root=root)
    assert 1 in committed
    assert 3 in committed


def test_verify_integrity_force_best_draft_passes(tmp_path):
    """verify_integrity passes for force_best_draft when draft file exists."""
    root = _make_project(tmp_path)
    cm = CheckpointManager(root)
    draft_content = "force-best draft chapter"
    cm.create_draft_checkpoint(chapter=7, draft_content=draft_content)

    assert cm.verify_integrity(7) is True


def test_verify_integrity_force_best_draft_fails_when_draft_missing(tmp_path):
    """verify_integrity fails for force_best_draft when draft file is missing."""
    root = _make_project(tmp_path)
    cm = CheckpointManager(root)
    cm.create_draft_checkpoint(chapter=9, draft_content="some content")

    # Remove the draft file
    draft_path = root / "chapters" / "draft" / "chapter_9.txt"
    draft_path.unlink()

    assert cm.verify_integrity(9) is False


def test_verify_integrity_normal_still_works(tmp_path):
    """Normal checkpoint verify_integrity is unchanged."""
    root = _make_project(tmp_path)
    cm = CheckpointManager(root)
    content = "normal novel text"
    cm.create_checkpoint(
        chapter=2,
        novel_content=content,
        synopsis_content=json.dumps({"chapter_num": 2}),
        outline_content=json.dumps({"chapter_num": 2}),
        world_state_snapshot={"characters": {}},
    )
    # Write the novel file
    (root / "chapters" / "novel" / "chapter_2.txt").write_text(content, encoding="utf-8")
    (root / "chapters" / "synopsis" / "chapter_2.txt").write_text(
        json.dumps({"chapter_num": 2}), encoding="utf-8")
    (root / "chapters" / "outline" / "chapter_2.json").write_text(
        json.dumps({"chapter_num": 2}), encoding="utf-8")

    assert cm.verify_integrity(2) is True


def test_old_checkpoints_without_mode_still_verify(tmp_path):
    """Old checkpoint entries without 'mode' field behave as normal."""
    root = _make_project(tmp_path)
    cm = CheckpointManager(root)
    # Manually create an old-style entry (no 'mode' key)
    cp_data = cm.load()
    cp_data["checkpoints"].append({
        "chapter": 4,
        "novel_hash": "abc123",
        "synopsis_hash": "def456",
        "outline_hash": "ghi789",
        "world_state_hash": "jkl012",
        "timestamp": "2024-01-01T00:00:00+00:00",
        "complete": True,
    })
    cm.save(cp_data)

    # Should fail because novel/synopsis/outline files don't exist
    assert cm.verify_integrity(4) is False
