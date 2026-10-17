# -*- coding: utf-8 -*-
"""P0 regression tests for _commit_chapter path after method-split undef-name fixes.

Verifies:
- T1: generate_single_chapter with score >= publication_line reaches COMMITTED,
      appends to event_ledger and end_state, sets status=COMMITTED.
- T2: same flow with state_changes in synopsis triggers apply_pending_changes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.pipeline.chapter_status import COMMITTED, get_status, last_committed
from novel_engine.pipeline.event_ledger import load_entries, load_end_states


_FIXTURE_CARD = {
    "chapter_num": 1,
    "core_goal": "test goal",
    "scene_blueprints": [
        {"scene_num": 1, "location": "room", "characters": ["A"],
         "goal": "test scene", "word_count_target": 1000},
    ],
    "chapter_events": [{"event_type": "test", "one_line_summary": "test event"}],
}

# Generate enough Chinese chars to pass the length gate (~900+ chars)
_LONG_TEXT = "陈老根在屋里坐着，听着外面的风声。夜深了，村里很安静。陆烬蜷缩在他怀里，呼吸均匀。窗外月光如水，洒在青石板路上。远处传来犬吠声，打破了夜的宁静。陈老根轻轻拍着陆烬的背，眼神温柔。这个孩子来历不明，但他决定抚养他长大。岁月静好，现世安稳。" * 20


def _make_orch(tmp_path: Path):
    """Minimal orchestrator with all LLM paths mocked."""
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "novel").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "draft").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "synopsis").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "outline").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "memory" / "world_state" / "pending").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({
            "llm": {"use_mock": True}, "review_llm": {"use_mock": True},
            "fallback_llm": {"use_mock": True},
            "chapter_target_chars": 7500,
        }), encoding="utf-8")
    (tmp_path / "config" / "llm_providers.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "forbidden.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "quality_policy.json").write_text(
        json.dumps({
            "publication_line": 88, "soft_publication_line": 85,
            "min_ratio": 0.85, "max_ratio": 1.2, "tolerance_chars": 500,
        }), encoding="utf-8")
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}
    orch._frozen_task_cards = {1: _FIXTURE_CARD}
    orch._frozen_synopsis = {1: {"synopsis": "test synopsis"}}
    orch._stage_world_sim = lambda cn: {}
    orch._stage_directing = lambda cn, ws, bypass_cache=None: _FIXTURE_CARD
    orch._stage_synopsis = lambda tc: {"synopsis": "test synopsis"}
    orch._stage_write = lambda tc, syn: _LONG_TEXT
    orch._ensure_chinese = lambda n: n
    orch._finalize_current_novel = lambda cn: None
    orch._stage_review = lambda cn, tc, syn, cur, ws=None: {
        "review": {
            "chapter_num": cn,
            "scores": {
                "plot_consistency": 25, "character_consistency": 22,
                "foreshadow_execution": 20, "style_match": 15,
                "pacing": 12, "innovation": 10,
            },
            "total_score": 92.0,
            "verdict": "pass",
            "issues": [],
            "fix_scope": "",
        },
        "score": 92.0,
        "verdict": "pass",
        "review_unstable": False,
    }
    orch._deterministic_quality_gate = lambda text, tc, scene_texts=None: {
        "passed": True, "issues": [],
    }
    return orch


def _assert_committed(root: Path, ch: int) -> None:
    assert get_status(root, ch) == COMMITTED, f"expected COMMITTED for ch{ch}"
    entries = load_entries(root)
    assert any(e.get("chapter_id") == ch for e in entries), \
        f"event_ledger missing chapter {ch}: {entries}"
    ends = load_end_states(root)
    assert any(e.get("chapter_id") == ch for e in ends), \
        f"end_state missing chapter {ch}: {ends}"
    novel_file = root / "chapters" / "novel" / f"chapter_{ch}.txt"
    assert novel_file.exists(), f"novel file missing: {novel_file}"


def test_commit_path_sets_committed_and_appends_ledger(tmp_path: Path) -> None:
    """T1: high-score pass reaches _commit_chapter, sets COMMITTED, appends ledger."""
    orch = _make_orch(tmp_path)
    with __import__("unittest.mock").mock.patch.object(
        orch, "_enforce_word_count", side_effect=lambda n, lo, hi: n
    ):
        result = orch.generate_single_chapter(1)

    assert result.get("success") is True, f"expected success, got {result}"
    _assert_committed(tmp_path, 1)
    assert last_committed(tmp_path) == 1


def test_commit_path_with_state_changes_applies_world_state(tmp_path: Path) -> None:
    """T2: synopsis with state_changes triggers apply_pending_changes when score>=pub."""
    orch = _make_orch(tmp_path)
    applied = {"changes": []}

    def _record_apply(changes, chapter=0, source="director"):
        applied["changes"] = list(changes)
        return True

    orch.simulator.apply_pending_changes = _record_apply

    synopsis_with_changes = {
        "synopsis": "test synopsis",
        "state_changes": [{"type": "character_age", "character": "A", "delta": 1}],
    }
    orch._frozen_synopsis = {1: synopsis_with_changes}
    orch._stage_synopsis = lambda tc: synopsis_with_changes

    with __import__("unittest.mock").mock.patch.object(
        orch, "_enforce_word_count", side_effect=lambda n, lo, hi: n
    ):
        result = orch.generate_single_chapter(1)

    assert result.get("success") is True, f"expected success, got {result}"
    _assert_committed(tmp_path, 1)
    assert applied["changes"] == synopsis_with_changes["state_changes"], \
        f"apply_pending_changes not called with expected changes: {applied}"
