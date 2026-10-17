# -*- coding: utf-8 -*-
"""Tests for MemoryManager pending semantics integration with fact_changes."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.core.memory_manager import MemoryManager
from novel_engine.pipeline.fact_changes import load_entries


def test_add_pending_change_writes_to_fact_changes(tmp_path: Path) -> None:
    """add_pending_change writes to both pending file and fact_changes ledger."""
    mm = MemoryManager(project_root=str(tmp_path))
    mm.add_pending_change({
        "type": "character_realm",
        "target": "hero",
        "new_value": "Yuanying",
        "source": "director",
    }, chapter=12)
    # Check pending file
    pfile = tmp_path / "memory" / "world_state" / "pending" / "pending_changes.json"
    data = json.loads(pfile.read_text(encoding="utf-8"))
    assert len(data.get("pending_changes", [])) == 1
    # Check fact_changes ledger
    entries = load_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["status"] == "pending"
    assert entries[0]["type"] == "character_realm"
    assert entries[0]["target"] == "hero"
    # chapter must be preserved (CC: 缺陷A 修复——章节号可追溯)
    assert entries[0]["chapter"] == 12


def test_commit_pending_changes_clears_pending_file(tmp_path: Path) -> None:
    """commit_pending_changes removes items from pending file."""
    mm = MemoryManager(project_root=str(tmp_path))
    change = {"type": "character_realm", "target": "hero", "new_value": "Yuanying"}
    mm.add_pending_change(change)
    mm.commit_pending_changes([change])
    pfile = tmp_path / "memory" / "world_state" / "pending" / "pending_changes.json"
    data = json.loads(pfile.read_text(encoding="utf-8"))
    assert len(data.get("pending_changes", [])) == 0


def test_commit_does_not_touch_fact_changes(tmp_path: Path) -> None:
    """commit_pending_changes clears pending file but does not alter fact_changes."""
    mm = MemoryManager(project_root=str(tmp_path))
    change = {"type": "character_realm", "target": "hero", "new_value": "Yuanying"}
    mm.add_pending_change(change)
    entries_before = len(load_entries(tmp_path))
    mm.commit_pending_changes([change])
    entries_after = len(load_entries(tmp_path))
    # fact_changes should still have the pending record (status unchanged)
    assert entries_after == entries_before
    # But pending file should be cleared
    pfile = tmp_path / "memory" / "world_state" / "pending" / "pending_changes.json"
    data = json.loads(pfile.read_text(encoding="utf-8"))
    assert len(data.get("pending_changes", [])) == 0
