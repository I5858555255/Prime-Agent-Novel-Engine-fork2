"""Tests for FixLoopAgent — P3 integration."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.adapters.fix_loop_agent import FixLoopAgent, FixDiagnostic, FixRecommendation


class TestFixDiagnostic:
    def test_builds_valid_diagnostic(self):
        diag = FixDiagnostic(
            chapter_num=1,
            current_score=82.0,
            best_score=80.0,
            has_high_issues=False,
            deterministic_issues=[],
            soft_issues=["[长度] 字数不足"],
            review_issues=[],
            reaction_pending=False,
            low_score_eligible=False,
            no_improve_count=1,
            max_iterations=2,
            iteration=0,
        )
        assert diag.chapter_num == 1
        assert diag.current_score == 82.0
        assert diag.has_high_issues is False
        assert len(diag.deterministic_issues) == 0


class TestFixRecommendation:
    def _agent(self):
        return FixLoopAgent(None)

    def test_recommend_proceed_on_pass(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=90.0, best_score=90.0,
            has_high_issues=False, deterministic_issues=[], soft_issues=[],
            review_issues=[], reaction_pending=False, low_score_eligible=False,
            no_improve_count=0, max_iterations=2, iteration=0,
        )
        rec = self._agent().recommend(diag)
        assert rec.action == "proceed"

    def test_recommend_stop_on_no_improve(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=70.0, best_score=75.0,
            has_high_issues=True, deterministic_issues=[], soft_issues=[],
            review_issues=[], reaction_pending=False, low_score_eligible=False,
            no_improve_count=3, max_iterations=2, iteration=1,
        )
        rec = self._agent().recommend(diag)
        assert rec.action == "stop"

    def test_recommend_patch_on_high_issues(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=75.0, best_score=75.0,
            has_high_issues=True, deterministic_issues=[], soft_issues=[],
            review_issues=[{"dimension": "剧情一致性", "severity": "high"}],
            reaction_pending=False, low_score_eligible=False,
            no_improve_count=0, max_iterations=2, iteration=0,
        )
        rec = self._agent().recommend(diag)
        assert rec.action == "patch"

    def test_recommend_rewrite_chapter_on_low_score(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=55.0, best_score=55.0,
            has_high_issues=False, deterministic_issues=[], soft_issues=[],
            review_issues=[], reaction_pending=False, low_score_eligible=True,
            no_improve_count=0, max_iterations=2, iteration=0,
        )
        rec = self._agent().recommend(diag)
        assert rec.action == "rewrite_chapter"

    def test_recommend_rewrite_scene_on_hard_det_issues(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=78.0, best_score=78.0,
            has_high_issues=False,
            deterministic_issues=["[canon] 境界错误"],
            soft_issues=[], review_issues=[],
            reaction_pending=False, low_score_eligible=False,
            no_improve_count=0, max_iterations=2, iteration=0,
        )
        rec = self._agent().recommend(diag)
        assert rec.action in ("rewrite_scene", "patch")

    def test_recommend_proceed_in_gray_band(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=86.0, best_score=86.0,
            has_high_issues=False, deterministic_issues=[], soft_issues=[],
            review_issues=[], reaction_pending=False, low_score_eligible=False,
            no_improve_count=0, max_iterations=2, iteration=0,
        )
        rec = self._agent().recommend(diag)
        assert rec.action == "proceed"

    def test_recommend_default_patch(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=70.0, best_score=72.0,
            has_high_issues=False, deterministic_issues=[], soft_issues=[],
            review_issues=[], reaction_pending=False, low_score_eligible=False,
            no_improve_count=0, max_iterations=2, iteration=0,
        )
        rec = self._agent().recommend(diag)
        assert rec.action == "patch"

    def test_recommend_reaction_pending(self):
        diag = FixDiagnostic(
            chapter_num=1, current_score=85.0, best_score=85.0,
            has_high_issues=False, deterministic_issues=[], soft_issues=[],
            review_issues=[], reaction_pending=True, low_score_eligible=False,
            no_improve_count=0, max_iterations=2, iteration=0,
        )
        rec = self._agent().recommend(diag)
        assert rec.action == "patch"
