# -*- coding: utf-8 -*-
"""Tests for WorldSimulator.apply_pending_changes fact_changes integration."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.pipeline.fact_changes import load_entries, get_changes
from novel_engine.agents.world_simulator import WorldSimulator


def _make_sim(tmp_path: Path) -> WorldSimulator:
    """Create WorldSimulator with proper directory structure."""
    # Create all necessary directories
    (tmp_path / "memory" / "world_state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "simulation").mkdir(parents=True, exist_ok=True)
    
    # Create world_state files
    chars = {"characters": {"hero": {"realm": "Qiandao", "location": "Village"}}}
    (tmp_path / "memory" / "world_state" / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "memory" / "world_state" / "power_system.json").write_text(
        json.dumps({"breakthrough_history": []}), encoding="utf-8")
    (tmp_path / "memory" / "world_state" / "factions.json").write_text(
        json.dumps({"factions": []}), encoding="utf-8")
    
    # Create config files
    (tmp_path / "config" / "simulation" / "rules.json").write_text('{}', encoding="utf-8")
    (tmp_path / "config" / "simulation" / "constraints.json").write_text('{}', encoding="utf-8")
    
    return WorldSimulator(project_root=str(tmp_path))


def test_apply_pending_changes_writes_facts(tmp_path: Path) -> None:
    """Test that character_realm change writes to fact_changes ledger."""
    sim = _make_sim(tmp_path)
    changes = [
        {"type": "character_realm", "target": "hero", "new_value": "Yuanying"},
    ]
    result = sim.apply_pending_changes(changes, chapter=1, source="director")
    
    # Should have modified the state
    assert result is True
    assert sim.characters["characters"]["hero"]["realm"] == "Yuanying"
    
    # Should have recorded to fact_changes
    entries = get_changes(sim.root, chapter=1)
    assert len(entries) == 1
    e = entries[0]
    assert e["type"] == "character_realm"
    assert e["target"] == "hero"
    assert e["old_value"] == "Qiandao"
    assert e["new_value"] == "Yuanying"
    assert e["source"] == "director"
    assert e["status"] == "applied"


def test_apply_pending_changes_no_change_no_record(tmp_path: Path) -> None:
    """Applying empty list records nothing."""
    sim = _make_sim(tmp_path)
    result = sim.apply_pending_changes([], chapter=1)
    assert result is False
    assert len(load_entries(sim.root)) == 0


def test_apply_pending_changes_target_not_in_state(tmp_path: Path) -> None:
    """Change targeting unknown character is rejected and recorded as rejected."""
    sim = _make_sim(tmp_path)
    changes = [{"type": "character_realm", "target": "unknown", "new_value": "X"}]
    result = sim.apply_pending_changes(changes, chapter=1)
    
    # No modification since target doesn't exist
    assert result is False
    
    # Records the rejected change
    entries = get_changes(sim.root, chapter=1)
    assert len(entries) == 1
    assert entries[0]["status"] == "rejected"
    assert entries[0]["gate_result"] == "rejected"


def test_apply_pending_changes_character_location(tmp_path: Path) -> None:
    """Test character_location change records old/new values."""
    sim = _make_sim(tmp_path)
    changes = [{"type": "character_location", "target": "hero", "new_value": "City"}]
    result = sim.apply_pending_changes(changes, chapter=1, source="director")
    
    assert result is True
    assert sim.characters["characters"]["hero"]["location"] == "City"
    
    entries = get_changes(sim.root, chapter=1)
    assert len(entries) == 1
    assert entries[0]["old_value"] == "Village"
    assert entries[0]["new_value"] == "City"


def test_apply_pending_changes_relationship_update(tmp_path: Path) -> None:
    """Test relationship_update change records correctly."""
    sim = _make_sim(tmp_path)
    changes = [
        {"type": "relationship_update", "relationship_id": "hero-friend", "new_value": "best_friend"}
    ]
    result = sim.apply_pending_changes(changes, chapter=1, source="reviewer")
    
    assert result is True
    assert sim.characters["characters"]["hero"]["relationships"]["friend"] == "best_friend"
    
    entries = get_changes(sim.root, chapter=1)
    assert len(entries) == 1
    assert entries[0]["type"] == "relationship_update"
    assert entries[0]["target"] == "hero-friend"
    assert entries[0]["source"] == "reviewer"
    assert entries[0]["status"] == "applied"


def test_apply_pending_changes_timeline_event(tmp_path: Path) -> None:
    """Test timeline_event change records correctly."""
    sim = _make_sim(tmp_path)
    changes = [
        {
            "type": "timeline_event",
            "target": "event1",
            "chapter": 1,
            "event": "battle",
            "characters": ["hero"],
        }
    ]
    result = sim.apply_pending_changes(changes, chapter=1, source="director")
    
    assert result is True
    assert len(sim.power_system.get("breakthrough_history", [])) == 1
    
    entries = get_changes(sim.root, chapter=1)
    assert len(entries) == 1
    assert entries[0]["type"] == "timeline_event"
    assert entries[0]["new_value"] == {
        "chapter": 1,
        "event": "battle",
        "characters": ["hero"],
    }


def test_apply_pending_changes_source_passed_through(tmp_path: Path) -> None:
    """Test that source parameter is passed through to fact_changes record."""
    sim = _make_sim(tmp_path)
    changes = [{"type": "character_location", "target": "hero", "new_value": "City"}]
    sim.apply_pending_changes(changes, chapter=2, source="deterministic_fallback")
    
    entries = get_changes(sim.root, chapter=2)
    assert len(entries) == 1
    assert entries[0]["source"] == "deterministic_fallback"


def test_apply_pending_changes_multiple_changes(tmp_path: Path) -> None:
    """Test multiple changes in one call."""
    sim = _make_sim(tmp_path)
    changes = [
        {"type": "character_realm", "target": "hero", "new_value": "Yuanying"},
        {"type": "character_location", "target": "hero", "new_value": "City"},
    ]
    result = sim.apply_pending_changes(changes, chapter=1, source="director")
    
    assert result is True
    entries = get_changes(sim.root, chapter=1)
    assert len(entries) == 2
    assert all(e["status"] == "applied" for e in entries)
