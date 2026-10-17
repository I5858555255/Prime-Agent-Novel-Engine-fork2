# -*- coding: utf-8 -*-
"""Tests for deterministic pre-validation of world_state changes.

Covers:
- Entity not found -> rejected + ledger + flag_for_human
- Hard: realm regression -> rejected + ledger + flag_for_human
- Hard: dead character action -> rejected + ledger + flag_for_human
- Soft: realm jump too large -> logged warning, change proceeds
- Normal pass -> applied
- Integration: rejected change in chapter commit path -> chapter still COMMITTED
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.pipeline.fact_changes import load_entries
from novel_engine.pipeline.change_validation import validate_changes
from novel_engine.agents.world_simulator import WorldSimulator


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_validation_config(tmp_path: Path) -> None:
    """Write quality_thresholds.json with change_validation rules."""
    cfg = {
        "scoring_dimensions": [],
        "grading": {"pass": 88, "fix": 60, "fail": 0},
        "fix_strategy": {"60_87": "", "below_60": ""},
        "change_validation": {
            "hard": ["entity_not_found", "realm_regression", "dead_character_action"],
            "soft": ["realm_jump_too_large"],
            "realm_order": [
                "凡人体质",
                "炼气一层", "炼气二层", "炼气三层", "炼气四层", "炼气五层",
                "炼气六层", "炼气七层", "炼气八层", "炼气九层", "炼气圆满",
                "筑基", "筑基期", "筑基初期", "筑基中期", "筑基后期", "筑基圆满",
                "金丹", "金丹期", "金丹初期", "金丹中期", "金丹后期", "金丹圆满",
                "元婴", "元婴期", "元婴初期", "元婴中期", "元婴后期", "元婴圆满",
                "化神", "化神期", "化神初期", "化神中期", "化神后期", "化神圆满",
                "飞升", "飞升期",
                "人道之主",
            ],
        },
    }
    (tmp_path / "config" / "quality_thresholds.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8"
    )


def _make_sim(tmp_path: Path, extra_chars: dict | None = None) -> WorldSimulator:
    """Create WorldSimulator with proper directory structure and validation config."""
    (tmp_path / "memory" / "world_state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "simulation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    chars = {
        "characters": {
            "hero": {"realm": "Qiandao", "location": "Village"},
            "dead_char": {"realm": "死亡", "location": "墓园"},
        }
    }
    if extra_chars:
        for cid, cdata in extra_chars.items():
            chars["characters"][cid] = cdata

    (tmp_path / "memory" / "world_state" / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "power_system.json").write_text(
        json.dumps({"breakthrough_history": []}), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "factions.json").write_text(
        json.dumps({"factions": []}), encoding="utf-8"
    )
    (tmp_path / "config" / "simulation" / "rules.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "simulation" / "constraints.json").write_text("{}", encoding="utf-8")
    _write_validation_config(tmp_path)

    return WorldSimulator(project_root=str(tmp_path))


def _make_sim_with_chinese_realm(tmp_path: Path, initial_realm: str) -> WorldSimulator:
    """Create simulator with a Chinese-realm character for regression/jump tests."""
    (tmp_path / "memory" / "world_state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "simulation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    chars = {
        "characters": {
            "陆烬": {"realm": initial_realm, "location": "雾隐村"},
            "已死角色": {"realm": "死亡", "location": "未知"},
        }
    }
    (tmp_path / "memory" / "world_state" / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "power_system.json").write_text(
        json.dumps({"breakthrough_history": []}), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "factions.json").write_text(
        json.dumps({"factions": []}), encoding="utf-8"
    )
    (tmp_path / "config" / "simulation" / "rules.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "simulation" / "constraints.json").write_text("{}", encoding="utf-8")
    _write_validation_config(tmp_path)

    return WorldSimulator(project_root=str(tmp_path))


# ── Unit tests: validate_changes() ───────────────────────────────────────────


def test_entity_not_found_rejected(tmp_path: Path) -> None:
    """Entity not found -> rejected, flag_for_human called."""
    sim = _make_sim(tmp_path)
    flags = []
    results = validate_changes(
        [{"type": "character_realm", "target": "nonexistent", "new_value": "Novalue"}],
        characters=sim.characters,
        root=sim.root,
        flag_for_human=lambda r: flags.append(r),
    )
    assert len(results) == 1
    change, verdict, reason = results[0]
    assert verdict == "rejected"
    assert "entity_not_found" in reason
    assert len(flags) == 1
    assert "nonexistent" in flags[0]


def test_entity_not_found_location_rejected(tmp_path: Path) -> None:
    """Entity not found on character_location -> rejected."""
    sim = _make_sim(tmp_path)
    results = validate_changes(
        [{"type": "character_location", "target": "ghost", "new_value": "Nowhere"}],
        characters=sim.characters,
        root=sim.root,
    )
    assert results[0][1] == "rejected"


def test_entity_not_found_relationship_rejected(tmp_path: Path) -> None:
    """Entity not found on relationship_update -> rejected."""
    sim = _make_sim(tmp_path)
    results = validate_changes(
        [{"type": "relationship_update", "relationship_id": "ghost-friend", "new_value": "enemy"}],
        characters=sim.characters,
        root=sim.root,
    )
    assert results[0][1] == "rejected"


def test_realm_regression_rejected(tmp_path: Path) -> None:
    """Realm going backward -> rejected, flag_for_human called."""
    sim = _make_sim_with_chinese_realm(tmp_path, "筑基")
    flags = []
    results = validate_changes(
        [{"type": "character_realm", "target": "陆烬", "new_value": "凡人体质"}],
        characters=sim.characters,
        root=sim.root,
        flag_for_human=lambda r: flags.append(r),
    )
    assert len(results) == 1
    _, verdict, reason = results[0]
    assert verdict == "rejected"
    assert "realm_regression" in reason
    assert len(flags) == 1


def test_realm_advancement_passed(tmp_path: Path) -> None:
    """Normal realm advancement -> passed."""
    sim = _make_sim_with_chinese_realm(tmp_path, "炼气一层")
    results = validate_changes(
        [{"type": "character_realm", "target": "陆烬", "new_value": "炼气二层"}],
        characters=sim.characters,
        root=sim.root,
    )
    assert results[0][1] == "passed"


def test_dead_character_action_rejected(tmp_path: Path) -> None:
    """Dead character gets a realm change -> rejected."""
    sim = _make_sim_with_chinese_realm(tmp_path, "炼气一层")
    flags = []
    results = validate_changes(
        [{"type": "character_realm", "target": "已死角色", "new_value": "筑基"}],
        characters=sim.characters,
        root=sim.root,
        flag_for_human=lambda r: flags.append(r),
    )
    assert len(results) == 1
    _, verdict, reason = results[0]
    assert verdict == "rejected"
    assert "dead_character_action" in reason
    assert len(flags) == 1


def test_dead_character_with_yunluo_rejected(tmp_path: Path) -> None:
    """Dead character with '陨落' marker -> rejected."""
    (tmp_path / "memory" / "world_state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "simulation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    chars = {"characters": {"fallen": {"realm": "陨落", "location": "战场"}}}
    (tmp_path / "memory" / "world_state" / "characters.json").write_text(
        json.dumps(chars, ensure_ascii=False), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "power_system.json").write_text(
        json.dumps({"breakthrough_history": []}), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "factions.json").write_text(
        json.dumps({"factions": []}), encoding="utf-8"
    )
    (tmp_path / "config" / "simulation" / "rules.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "simulation" / "constraints.json").write_text("{}", encoding="utf-8")
    _write_validation_config(tmp_path)

    sim = WorldSimulator(project_root=str(tmp_path))
    results = validate_changes(
        [{"type": "character_realm", "target": "fallen", "new_value": "筑基"}],
        characters=sim.characters,
        root=sim.root,
    )
    assert results[0][1] == "rejected"


def test_realm_jump_too_large_soft_warn(tmp_path: Path) -> None:
    """Multi-level realm jump -> soft_warn (not rejected)."""
    sim = _make_sim_with_chinese_realm(tmp_path, "筑基")
    results = validate_changes(
        [{"type": "character_realm", "target": "陆烬", "new_value": "化神"}],
        characters=sim.characters,
        root=sim.root,
    )
    assert len(results) == 1
    _, verdict, reason = results[0]
    assert verdict == "soft_warn"
    assert "realm_jump_too_large" in reason


def test_normal_location_change_passed(tmp_path: Path) -> None:
    """character_location on existing char -> passed."""
    sim = _make_sim(tmp_path)
    results = validate_changes(
        [{"type": "character_location", "target": "hero", "new_value": "City"}],
        characters=sim.characters,
        root=sim.root,
    )
    assert results[0][1] == "passed"


def test_timeline_event_passed(tmp_path: Path) -> None:
    """timeline_event -> passed (not checked by entity rule)."""
    sim = _make_sim(tmp_path)
    results = validate_changes(
        [{"type": "timeline_event", "target": "event1", "chapter": 5, "event": "battle", "characters": ["hero"]}],
        characters=sim.characters,
        root=sim.root,
    )
    assert results[0][1] == "passed"


def test_multiple_changes_mixed_results(tmp_path: Path) -> None:
    """Batch with mix of pass, reject, soft_warn."""
    sim = _make_sim_with_chinese_realm(tmp_path, "炼气一层")
    results = validate_changes(
        [
            {"type": "character_realm", "target": "陆烬", "new_value": "炼气二层"},  # pass (adjacent)
            {"type": "character_realm", "target": "已死角色", "new_value": "筑基"},  # reject (dead)
            {"type": "character_realm", "target": "陆烬", "new_value": "化神"},     # soft_warn (jump)
            {"type": "character_location", "target": "unknown", "new_value": "X"},  # reject (entity)
        ],
        characters=sim.characters,
        root=sim.root,
    )
    verdicts = [r[1] for r in results]
    assert verdicts == ["passed", "rejected", "soft_warn", "rejected"]


def test_validate_changes_no_config_returns_empty_rules(tmp_path: Path) -> None:
    """When quality_thresholds.json has no change_validation key, default behavior applies."""
    (tmp_path / "memory" / "world_state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "simulation").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)

    chars = {"characters": {"hero": {"realm": "Qiandao", "location": "Village"}}}
    (tmp_path / "memory" / "world_state" / "characters.json").write_text(
        json.dumps(chars), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "power_system.json").write_text(
        json.dumps({"breakthrough_history": []}), encoding="utf-8"
    )
    (tmp_path / "memory" / "world_state" / "factions.json").write_text(
        json.dumps({"factions": []}), encoding="utf-8"
    )
    (tmp_path / "config" / "simulation" / "rules.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "simulation" / "constraints.json").write_text("{}", encoding="utf-8")
    # Write a quality_thresholds.json WITHOUT change_validation key
    (tmp_path / "config" / "quality_thresholds.json").write_text(
        json.dumps({"scoring_dimensions": []}), encoding="utf-8"
    )
    sim = WorldSimulator(project_root=str(tmp_path))
    # No change_validation key -> rules empty -> only entity check (always on) blocks nonexistent chars
    results = validate_changes(
        [
            {"type": "character_realm", "target": "hero", "new_value": "Yuanying"},
        ],
        characters=sim.characters,
        root=sim.root,
    )
    assert all(r[1] == "passed" for r in results)


# ── Integration tests: apply_pending_changes ─────────────────────────────────


def test_integration_entity_not_found_rejected_in_ledger(tmp_path: Path) -> None:
    """apply_pending_changes rejects entity-not-found and writes status=rejected to ledger."""
    sim = _make_sim(tmp_path)
    flags = []
    sim.apply_pending_changes(
        [{"type": "character_realm", "target": "ghost", "new_value": "X"}],
        chapter=1,
        source="director",
        flag_for_human=lambda r: flags.append(r),
    )
    # Character should NOT have been modified
    assert "ghost" not in sim.characters.get("characters", {})
    # Ledger should have rejected entry
    entries = load_entries(sim.root)
    rejected = [e for e in entries if e.get("status") == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["target"] == "ghost"
    assert rejected[0]["gate_result"] == "rejected"
    assert len(flags) == 1


def test_integration_realm_regression_rejected_in_ledger(tmp_path: Path) -> None:
    """apply_pending_changes rejects realm regression and writes to ledger."""
    sim = _make_sim_with_chinese_realm(tmp_path, "筑基")
    flags = []
    sim.apply_pending_changes(
        [{"type": "character_realm", "target": "陆烬", "new_value": "凡人体质"}],
        chapter=1,
        source="director",
        flag_for_human=lambda r: flags.append(r),
    )
    # Character realm should remain unchanged
    assert sim.characters["characters"]["陆烬"]["realm"] == "筑基"
    entries = load_entries(sim.root)
    rejected = [e for e in entries if e.get("status") == "rejected"]
    assert len(rejected) == 1
    assert rejected[0]["gate_result"] == "rejected"
    assert len(flags) == 1


def test_integration_dead_character_rejected_in_ledger(tmp_path: Path) -> None:
    """apply_pending_changes rejects dead character action and writes to ledger."""
    sim = _make_sim_with_chinese_realm(tmp_path, "炼气一层")
    flags = []
    sim.apply_pending_changes(
        [{"type": "character_realm", "target": "已死角色", "new_value": "筑基"}],
        chapter=1,
        source="director",
        flag_for_human=lambda r: flags.append(r),
    )
    assert sim.characters["characters"]["已死角色"]["realm"] == "死亡"
    entries = load_entries(sim.root)
    rejected = [e for e in entries if e.get("status") == "rejected"]
    assert len(rejected) == 1


def test_integration_soft_warn_proceeds_normally(tmp_path: Path) -> None:
    """Soft violation: change is applied and ledger shows status=applied."""
    sim = _make_sim_with_chinese_realm(tmp_path, "筑基")
    sim.apply_pending_changes(
        [{"type": "character_realm", "target": "陆烬", "new_value": "化神"}],
        chapter=1,
        source="director",
    )
    # Should be applied despite soft warning
    assert sim.characters["characters"]["陆烬"]["realm"] == "化神"
    entries = load_entries(sim.root)
    applied = [e for e in entries if e.get("status") == "applied"]
    assert len(applied) == 1
    assert applied[0]["gate_result"] == "passed"


def test_integration_normal_change_still_works(tmp_path: Path) -> None:
    """Normal valid change still applies and records correctly."""
    sim = _make_sim(tmp_path)
    result = sim.apply_pending_changes(
        [{"type": "character_realm", "target": "hero", "new_value": "Yuanying"}],
        chapter=1,
        source="director",
    )
    assert result is True
    assert sim.characters["characters"]["hero"]["realm"] == "Yuanying"
    entries = load_entries(sim.root)
    assert len(entries) == 1
    assert entries[0]["status"] == "applied"
    assert entries[0]["gate_result"] == "passed"


def test_integration_empty_changes_no_effect(tmp_path: Path) -> None:
    """Empty changes list -> no ledger entries, returns False."""
    sim = _make_sim(tmp_path)
    result = sim.apply_pending_changes([], chapter=1)
    assert result is False
    assert len(load_entries(sim.root)) == 0


def test_integration_flag_for_human_callback_called_on_reject(tmp_path: Path) -> None:
    """flag_for_human callback is invoked for each rejected change."""
    sim = _make_sim(tmp_path)
    callback = MagicMock()
    sim.apply_pending_changes(
        [
            {"type": "character_realm", "target": "ghost", "new_value": "X"},
            {"type": "character_realm", "target": "dead_char", "new_value": "Y"},
        ],
        chapter=1,
        source="director",
        flag_for_human=callback,
    )
    assert callback.call_count == 2


def test_integration_mixed_batch_partial_application(tmp_path: Path) -> None:
    """Mixed batch: rejected changes skipped, valid ones applied."""
    sim = _make_sim(tmp_path)
    sim.apply_pending_changes(
        [
            {"type": "character_realm", "target": "hero", "new_value": "NewRealm"},
            {"type": "character_realm", "target": "ghost", "new_value": "X"},
            {"type": "character_location", "target": "dead_char", "new_value": "Moved"},
        ],
        chapter=1,
        source="director",
    )
    # hero changed, dead_char changed, ghost rejected
    assert sim.characters["characters"]["hero"]["realm"] == "NewRealm"
    assert sim.characters["characters"]["dead_char"]["location"] == "Moved"
    entries = load_entries(sim.root)
    statuses = [e["status"] for e in entries]
    assert statuses.count("applied") == 2
    assert statuses.count("rejected") == 1
