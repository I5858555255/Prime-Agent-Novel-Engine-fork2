# -*- coding: utf-8 -*-
"""Regression test for ch267: gray-band authorization vs final det re-check.

Bug: fix loop authorized gray-band release (85.9, gates green at that
snapshot), but the stage-6 re-check of the purified final text surfaced new
deterministic hard issues. _decide_publish silently vetoed the authorization
and the chapter fell into the 82-87 human-review queue with NO attribution —
the END log showed `best 85.9 < 88 (hard gate [])` and `errors: []`, a
85.9-score chapter FAILED for no visible reason.

Fix: when gray_band_release is set but final det has hard issues, record
result["gray_band_det_block"] so downstream (82-87 queue note / errors /
log) always carries the veto reason. Successful gray-band path is unchanged.
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent.parent))

from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


def _make_orchestrator(tmp_path):
    inst = object.__new__(PipelineOrchestrator)
    inst.root = tmp_path
    inst._force_publish_best = False
    inst._last_leak_issues = []
    inst.config = {"llm": {"use_mock": True}}  # 跳过 publish_router override 分支
    return inst


def _commit_policy():
    return {
        "publication_line": 88,
        "soft_publication_line": 85,
        "chapter_target_chars": 7000,
        "min_ratio": 0.4,
    }


def test_gray_band_with_final_det_hard_records_attribution(tmp_path):
    """ch267 case: gray-band authorized but stage-6 det re-check found hard
    issues -> can_publish False AND result carries gray_band_det_block."""
    inst = _make_orchestrator(tmp_path)
    result = {
        "gray_band_release": True,
        "gray_band_score": 85.9,
        "score": 85.9,
    }
    review = {"issues": [], "dim_scores": {}}
    det_issues = ["[重复] 段落重复出现（样例）"]
    can_publish, verdict = inst._decide_publish(
        chapter_num=267, task_card={}, synopsis={"synopsis": "s"},
        world_state={}, result=result, score=85.9, sm=None,
        review=review, final_text="text",
        violations=[], det_issues=det_issues, det_soft=[], high_list=[],
        apply_world_state=True, cur_score=85.9,
        _commit_policy=_commit_policy(),
    )
    assert can_publish is False, "gray-band with final det hard must not publish"
    assert result.get("gray_band_det_block") == det_issues, (
        "attribution must be recorded for downstream note/errors")
    reasons = verdict.get("reasons") or []
    assert any("det_hard" in r for r in reasons), f"verdict reasons must carry det detail: {reasons}"


def test_gray_band_clean_det_still_publishes(tmp_path):
    """Unchanged happy path: gray-band authorized + clean final det -> publish."""
    inst = _make_orchestrator(tmp_path)
    result = {
        "gray_band_release": True,
        "gray_band_score": 85.9,
        "score": 85.9,
    }
    review = {"issues": [], "dim_scores": {}}
    can_publish, verdict = inst._decide_publish(
        chapter_num=268, task_card={}, synopsis={"synopsis": "s"},
        world_state={}, result=result, score=85.9, sm=None,
        review=review, final_text="text",
        violations=[], det_issues=[], det_soft=[], high_list=[],
        apply_world_state=True, cur_score=85.9,
        _commit_policy=_commit_policy(),
    )
    assert can_publish is True, f"clean gray-band should publish, got {verdict}"
    assert result.get("gray_band_det_block") is None


def test_gray_band_ignored_when_score_below_soft_line(tmp_path):
    """Gray-band flag but score below soft line must not trigger gray path."""
    inst = _make_orchestrator(tmp_path)
    result = {
        "gray_band_release": True,
        "gray_band_score": 80.0,
        "score": 80.0,
    }
    review = {"issues": [], "dim_scores": {}}
    can_publish, _ = inst._decide_publish(
        chapter_num=269, task_card={}, synopsis={"synopsis": "s"},
        world_state={}, result=result, score=80.0, sm=None,
        review=review, final_text="text",
        violations=[], det_issues=[], det_soft=[], high_list=[],
        apply_world_state=True, cur_score=80.0,
        _commit_policy=_commit_policy(),
    )
    # 80 < 85 soft line -> not gray-band -> evaluate_publish on score 80 -> False
    assert can_publish is False
    assert result.get("gray_band_det_block") is None
