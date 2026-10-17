# -*- coding: utf-8 -*-
"""Regression test: soft regen calls generate_scene(previous_context=...) not prev=."""
from __future__ import annotations

import ast
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _setup_orch(tmp_path: Path):
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({"llm": {}, "review_llm": {}, "fallback_llm": {}}),
        encoding="utf-8",
    )
    (tmp_path / "config" / "llm_providers.json").write_text(
        json.dumps({
            "active_profile": "test",
            "profiles": {"test": {
                "base_url": "https://test.example.com/v1",
                "api_key_env": "TEST_KEY_SOFT_REGEN",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["test-model"], "response_format": None},
                    "polish": {"models": ["test-model"], "response_format": None, "concurrency": 4},
                    "planning": {"models": ["test-model"], "response_format": None},
                    "review": {"models": ["test-model"], "response_format": None},
                },
            }},
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    os.environ["TEST_KEY_SOFT_REGEN"] = "sk-test-soft-regen"
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}
    return orch


def _make_card_with_soft_misses(scene_count: int = 3):
    bps = []
    events = []
    for i in range(1, scene_count + 1):
        bps.append({
            "scene_num": i,
            "location": f"room{i}",
            "characters": ["陈老根"],
            "goal": f"goal{i}",
            "conflict": f"conflict{i}",
            "emotion": "normal",
            "word_count_target": 1500,
            "beats": [],
        })
        events.append({"scene_num": i, "one_line_summary": f"scene {i}"})
    return {
        "chapter_num": 9,
        "core_goal": "ch9 goal",
        "conflicts": {"internal": "", "external": ""},
        "emotion_curve": {"start": "a", "middle": "b", "climax": "c", "end": "d"},
        "scene_blueprints": bps,
        "chapter_events": events,
        "chapter_hook": "hook",
        "end_state": {"narrative_position": "", "location": "",
                      "completed_actions": [], "pending_actions": [], "time_marker": ""},
        "foreshadow_actions": [
            {"foreshadow_id": "F001",
             "action": "look toward the forbidden zone",
             "intensity": "subtle hint",
             "scene_num": 2,
             "category": "opening_anchor",
             },
        ],
    }


def test_soft_regen_no_prev_keyword_error(tmp_path: Path) -> None:
    """Soft regen must call generate_scene with previous_context=, not prev=.

    Regression for: 'WriterAgent.generate_scene() got an unexpected keyword argument 'prev''
    seen in ch6/ch7/ch10 logs when soft regen path was exercised.
    """
    orch = _setup_orch(tmp_path)
    card = _make_card_with_soft_misses()

    call_kwargs_list: list[dict] = []

    def _fake_generate_scene(task_card, bp, syn, **kw):
        call_kwargs_list.append(kw)
        out = MagicMock()
        out.scene_text = f"scene {bp.get('scene_num', 1)} regenerated"
        out.hook = ""
        out.beats = []
        out.scene_id = bp.get("scene_num", 1)
        return out

    orch.writer.generate_scene = _fake_generate_scene

    with patch.object(orch, "_finalize_current_novel"):
        orch._enforce_mandatory_beats(
            chapter_num=9,
            task_card=card,
        )

    # Verify no call used the old 'prev' keyword (which would cause TypeError)
    for kwargs in call_kwargs_list:
        assert "prev" not in kwargs, (
            f"generate_scene called with deprecated 'prev' kwarg: {kwargs}. "
            "Should use 'previous_context' instead."
        )


def test_all_generate_scene_calls_use_previous_context_not_prev() -> None:
    """Static check: no generate_scene call site passes prev= in pipeline_orchestrator.py.

    This catches accidental re-introduction of the bug.
    """
    source = (
        Path(__file__).resolve().parents[3]
        / "novel_engine" / "pipeline" / "pipeline_orchestrator.py"
    ).read_text(encoding="utf-8")

    tree = ast.parse(source)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Attribute) and node.func.attr == "generate_scene"):
                continue
            for kw in node.keywords:
                if kw.arg == "prev":
                    raise AssertionError(
                        f"generate_scene call at line {kw.lineno} uses 'prev=' instead of "
                        "'previous_context='. This causes TypeError."
                    )
