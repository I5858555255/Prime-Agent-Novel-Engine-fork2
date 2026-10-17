# -*- coding: utf-8 -*-
"""Tests for deferred fact_change recording in _commit_chapter path."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.pipeline.fact_changes import get_changes, list_status
from novel_engine.pipeline.fact_changes import load_entries


def _setup_orch(tmp_path: Path):
    """Setup minimal orchestrator for testing."""
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    
    # Setup directories
    for d in ["config", "chapters/novel", "chapters/draft", "chapters/synopsis",
              "chapters/outline", "chapters/state",
              "memory/world_state/pending", "audit"]:
        (tmp_path / d).mkdir(parents=True, exist_ok=True)
    
    # Create world_state files
    chars = {"characters": {"hero": {"realm": "Qiandao", "location": "Village"}}}
    (tmp_path / "memory" / "world_state" / "characters.json").write_text(
        json.dumps(chars), encoding="utf-8")
    (tmp_path / "memory" / "world_state" / "power_system.json").write_text(
        json.dumps({"breakthrough_history": []}), encoding="utf-8")
    (tmp_path / "memory" / "world_state" / "factions.json").write_text(
        json.dumps({"factions": []}), encoding="utf-8")
    
    # Config files
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({"llm": {"use_mock": True}, "review_llm": {"use_mock": True},
                     "fallback_llm": {"use_mock": True}, "chapter_target_chars": 7500}),
        encoding="utf-8")
    (tmp_path / "config" / "llm_providers.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "forbidden.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "quality_policy.json").write_text(
        json.dumps({"publication_line": 88, "soft_publication_line": 85,
                     "min_ratio": 0.85, "max_ratio": 1.2, "tolerance_chars": 500}),
        encoding="utf-8")
    
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}
    orch._frozen_task_cards = {1: {
        "chapter_num": 1, "core_goal": "test",
        "scene_blueprints": [{"scene_num": 1, "location": "room", "characters": ["A"],
                              "goal": "test scene", "word_count_target": 1000}],
        "chapter_events": [{"event_type": "test", "one_line_summary": "test event"}],
    }}
    
    return orch


def test_deferred_path_records_fact_changes(tmp_path: Path) -> None:
    """When apply_world_state=False, fact_changes records status=deferred."""
    from novel_engine.core.state_machine import StateMachine
    
    orch = _setup_orch(tmp_path)
    
    synopsis_deferred = {
        "synopsis": "test synopsis",
        "state_changes": [{"type": "character_realm", "target": "hero", "new_value": "Yuanying"}],
    }
    task_card = orch._frozen_task_cards[1]
    
    sm = StateMachine(project_root=str(tmp_path))
    
    # Call _commit_chapter directly with apply_world_state=False
    # Need to provide a valid result dict with 'errors' key
    result = {"success": False, "score": 45.0, "errors": []}
    
    orch._commit_chapter(
        chapter_num=1,
        task_card=task_card,
        synopsis=synopsis_deferred,
        world_state={},
        result=result,
        score=45.0,
        sm=sm,
        apply_world_state=False,
    )
    
    # Check that deferred record was created
    deferred = list_status(tmp_path, "deferred")
    assert len(deferred) == 1
    assert deferred[0]["type"] == "character_realm"
    assert deferred[0]["target"] == "hero"
    assert deferred[0]["gate_result"] == "deferred"
    assert deferred[0]["status"] == "deferred"


def test_applied_path_records_facts(tmp_path: Path) -> None:
    """When apply_world_state=True, fact_changes records status=applied."""
    from novel_engine.core.state_machine import StateMachine
    
    orch = _setup_orch(tmp_path)
    orch._current_synopsis_source = "director"
    
    synopsis_with_changes = {
        "synopsis": "test synopsis",
        "state_changes": [{"type": "character_realm", "target": "hero", "new_value": "Yuanying"}],
    }
    task_card = orch._frozen_task_cards[1]
    
    sm = StateMachine(project_root=str(tmp_path))
    result = {"success": True, "score": 92.0, "errors": []}
    
    orch._commit_chapter(
        chapter_num=1,
        task_card=task_card,
        synopsis=synopsis_with_changes,
        world_state={},
        result=result,
        score=92.0,
        sm=sm,
        apply_world_state=True,
    )
    
    # Check that applied record was created
    entries = get_changes(tmp_path, chapter=1)
    applied = [e for e in entries if e["status"] == "applied"]
    assert len(applied) == 1
    assert applied[0]["type"] == "character_realm"
    assert applied[0]["target"] == "hero"
    assert applied[0]["old_value"] == "Qiandao"
    assert applied[0]["new_value"] == "Yuanying"
    assert applied[0]["source"] == "director"
    assert applied[0]["gate_result"] == "passed"


def test_deferred_path_no_changes(tmp_path: Path) -> None:
    """When no state_changes, no fact_changes records should be created."""
    from novel_engine.core.state_machine import StateMachine
    
    orch = _setup_orch(tmp_path)
    
    synopsis_no_changes = {
        "synopsis": "test synopsis",
        "state_changes": [],
    }
    task_card = orch._frozen_task_cards[1]
    
    sm = StateMachine(project_root=str(tmp_path))
    result = {"success": False, "score": 45.0, "errors": []}
    
    orch._commit_chapter(
        chapter_num=1,
        task_card=task_card,
        synopsis=synopsis_no_changes,
        world_state={},
        result=result,
        score=45.0,
        sm=sm,
        apply_world_state=False,
    )
    
    # No fact_changes records should exist
    entries = load_entries(tmp_path)
    assert len(entries) == 0
