# -*- coding: utf-8 -*-
"""Regression test for ch62 gray-band vs force-best state race.

Bug: gray-band final release (score >= soft_publication_line, gates pass)
published a high-score version to novel/, but a later best_score rewrite sent
the chapter down the force-best path, which overwrote draft/ and the
checkpoint with a *worse* version. Invariant fixed: once
result["gray_band_release"] is set, force-best must NOT downgrade the
chapter to draft — novel/ is the single publication source.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _make_orchestrator(tmp_path, score=87.0):
    inst = object.__new__(PipelineOrchestrator)
    inst.root = tmp_path
    inst._force_publish_best = True
    inst._last_leak_issues = []
    return inst


def _commit_policy():
    return {
        "publication_line": 88,
        "soft_publication_line": 85,
        "chapter_target_chars": 7000,
        "min_ratio": 0.4,
    }


def test_gray_band_release_short_circuits_force_best(tmp_path):
    """When gray_band_release is already set, force-best must not write draft."""
    inst = _make_orchestrator(tmp_path)
    result = {
        "gray_band_release": True,
        "gray_band_score": 86.9,
        "score": 86.9,
    }
    review = {"issues": [], "dim_scores": {}}
    # A gray-band chapter's text should go to novel, not draft
    inst.current_novel = "chapter text gray band release"
    can_publish, verdict = inst._decide_publish(
        chapter_num=62, task_card={}, synopsis={"synopsis": "s"},
        world_state={}, result=result, score=86.9, sm=None,
        review=review, final_text="chapter text gray band release",
        violations=[], det_issues=[], det_soft=[], high_list=[],
        apply_world_state=True, cur_score=86.9,
        _commit_policy=_commit_policy(),
    )
    assert can_publish is True, f"gray-band release should publish, got {verdict}"
    assert result.get("force_published") is not True
    # force-best draft must NOT be written
    draft_path = tmp_path / "chapters" / "draft" / "chapter_62.txt"
    assert not draft_path.exists(), "force-best draft must not be written after gray-band release"


def test_force_best_still_writes_draft_without_gray_band(tmp_path):
    """Without gray_band_release, force-best keeps its historical draft behavior."""
    inst = _make_orchestrator(tmp_path)
    result = {"score": 79.5, "note": "best 79.5 < 88"}
    review = {"issues": [], "dim_scores": {}}
    inst.current_novel = "force best draft text"
    can_publish, _ = inst._decide_publish(
        chapter_num=77, task_card={}, synopsis={"synopsis": "s"},
        world_state={}, result=result, score=79.5, sm=None,
        review=review, final_text="force best draft text",
        violations=[], det_issues=[], det_soft=[], high_list=[],
        apply_world_state=False, cur_score=79.5,
        _commit_policy=_commit_policy(),
    )
    assert can_publish is False
    draft_path = tmp_path / "chapters" / "draft" / "chapter_77.txt"
    assert draft_path.exists(), "force-best (no gray band) should still write draft"
    assert result.get("force_published") is True
