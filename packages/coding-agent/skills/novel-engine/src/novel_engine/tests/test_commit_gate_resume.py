# -*- coding: utf-8 -*-
"""F atomic-commit gate regressions.

A chapter only counts as done / advances the resume cursor when it verifies on
disk (checkpoint + published-file hashes). A below-publication-line forced
draft sets result.success=True but never builds a checkpoint; it must NOT move
the cursor, otherwise resume would skip a chapter missing from the novel.
"""
import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.pipeline.production_runner import (
    reconcile_resume_to_committed, is_chapter_committed, run_production,
)
from novel_engine.pipeline.chapter_status import set_status, COMMITTED


def _make_minimal_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    for d in ("config", "runtime", "chapters/draft", "chapters/state",
              "chapters/novel", "chapters/outline", "chapters/synopsis", "audit"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "config" / "cost_sandbox.json").write_text(json.dumps({
        "budget": {"full_production_max": 400.0, "total": 500.0},
        "currency_conversion": {"api_pricing": {
            "input_per_1m_tokens": 0.05, "output_per_1m_tokens": 0.15}},
    }), encoding="utf-8")
    return root


def _commit_chapter(root: Path, ch: int) -> None:
    from novel_engine.core.checkpoint import CheckpointManager
    cm = CheckpointManager(root)
    content = f"Chapter {ch} novel text."
    syn = json.dumps({"chapter_num": ch, "synopsis": f"s{ch}"})
    out = json.dumps({"chapter_num": ch, "core_goal": f"g{ch}"})
    ws = {"characters": {}, "factions": {}, "power_system": {}}
    cm.create_checkpoint(ch, content, syn, out, ws)
    (root / "chapters" / "novel" / f"chapter_{ch}.txt").write_text(content, encoding="utf-8")
    (root / "chapters" / "synopsis" / f"chapter_{ch}.txt").write_text(syn, encoding="utf-8")
    (root / "chapters" / "outline" / f"chapter_{ch}.json").write_text(out, encoding="utf-8")
    set_status(root, ch, COMMITTED)


def test_reconcile_drops_uncommitted_and_pulls_cursor_back(tmp_path):
    root = _make_minimal_project(tmp_path)
    _commit_chapter(root, 1)
    # chapters 2 and 3 are in a poisoned done cursor but never committed on disk
    assert is_chapter_committed(root, 1)
    assert not is_chapter_committed(root, 2)

    rec = reconcile_resume_to_committed(root, [1, 2, 3], effective_resume=3)
    assert rec["done"] == [1]
    assert rec["dropped"] == [2, 3]
    assert rec["effective_resume"] == 1


def test_reconcile_keeps_fully_committed_cursor(tmp_path):
    root = _make_minimal_project(tmp_path)
    _commit_chapter(root, 1)
    _commit_chapter(root, 2)
    rec = reconcile_resume_to_committed(root, [1, 2], effective_resume=2)
    assert rec["done"] == [1, 2]
    assert rec["dropped"] == []
    assert rec["effective_resume"] == 2


def test_run_loop_gates_cursor_advancement_on_real_commit():
    """Source contract: the fresh-generation path must advance passed/done only
    when result.success AND is_chapter_committed(); a success-without-commit
    forced draft must isolate/HALT and never call save_resume_state with the
    chapter appended."""
    src = inspect.getsource(run_production)
    assert "committed = is_chapter_committed(project_root, i)" in src
    assert 'if result.get("success") and committed:' in src
    # explicit forced-draft branch that does not advance
    assert 'elif result.get("success") and not committed:' in src
    assert "review_below_publication_line" in src
