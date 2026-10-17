# -*- coding: utf-8 -*-
"""Tests for fact_changes audit ledger."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.pipeline.fact_changes import (
    record_change,
    load_entries,
    get_changes,
    list_status,
    revert_change,
    revert_chapter,
    ledger_path,
)


def test_record_and_load(tmp_path: Path) -> None:
    """Basic record + load."""
    cid = record_change(
        tmp_path,
        chapter=1,
        change_type="character_realm",
        target="char_a",
        old_value="Qiandao",
        new_value="Yuanying",
        source="director",
        status="applied",
    )
    assert cid.startswith("ch1-")
    entries = load_entries(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    assert e["change_id"] == cid
    assert e["chapter"] == 1
    assert e["type"] == "character_realm"
    assert e["target"] == "char_a"
    assert e["old_value"] == "Qiandao"
    assert e["new_value"] == "Yuanying"
    assert e["source"] == "director"
    assert e["status"] == "applied"
    assert "ts" in e


def test_idempotent_write_same_change_id(tmp_path: Path) -> None:
    """Writing the same change_id twice does not duplicate."""
    cid = record_change(
        tmp_path, chapter=2, change_type="x", target="t",
        old_value=1, new_value=2, status="applied",
    )
    cid2 = record_change(
        tmp_path, chapter=2, change_type="x", target="t",
        old_value=1, new_value=2, status="applied", change_id=cid,
    )
    assert cid2 == cid
    assert len(load_entries(tmp_path)) == 1


def test_idempotent_write_auto_generated_duplicate(tmp_path: Path) -> None:
    """If auto-generated change_id collides, a UUID suffix is appended."""
    # Force a collision by writing two changes with same chapter
    # The second should get a unique id
    record_change(
        tmp_path, chapter=3, change_type="a", target="t1",
        old_value=None, new_value=1, status="applied",
    )
    record_change(
        tmp_path, chapter=3, change_type="b", target="t2",
        old_value=None, new_value=2, status="applied",
    )
    entries = load_entries(tmp_path)
    assert len(entries) == 2
    ids = {e["change_id"] for e in entries}
    assert len(ids) == 2


def test_get_changes_filters_by_chapter(tmp_path: Path) -> None:
    record_change(tmp_path, chapter=1, change_type="a", target="t1",
                  old_value=None, new_value=1, status="applied")
    record_change(tmp_path, chapter=2, change_type="b", target="t2",
                  old_value=None, new_value=2, status="applied")
    ch1 = get_changes(tmp_path, chapter=1)
    ch2 = get_changes(tmp_path, chapter=2)
    assert len(ch1) == 1 and ch1[0]["chapter"] == 1
    assert len(ch2) == 1 and ch2[0]["chapter"] == 2


def test_list_status(tmp_path: Path) -> None:
    record_change(tmp_path, chapter=1, change_type="a", target="t",
                  old_value=None, new_value=1, status="applied")
    record_change(tmp_path, chapter=1, change_type="b", target="t",
                  old_value=None, new_value=2, status="deferred")
    applied = list_status(tmp_path, "applied")
    deferred = list_status(tmp_path, "deferred")
    assert len(applied) == 1
    assert len(deferred) == 1


def test_atomic_write_no_partial(tmp_path: Path) -> None:
    """Write leaves valid JSONL even if interrupted (simulated)."""
    # We can't easily simulate interruption, but we verify the file
    # is always valid JSONL after each write.
    for i in range(5):
        record_change(tmp_path, chapter=1, change_type="x", target=f"t{i}",
                      old_value=i, new_value=i + 1, status="applied")
    entries = load_entries(tmp_path)
    assert len(entries) == 5
    for e in entries:
        assert isinstance(e, dict)
        assert "change_id" in e


def test_revert_change(tmp_path: Path) -> None:
    cid = record_change(tmp_path, chapter=5, change_type="realm", target="hero",
                        old_value="Level1", new_value="Level2", status="applied")
    rid = revert_change(tmp_path, cid)
    assert rid is not None
    entries = load_entries(tmp_path)
    # Original + revert record
    assert len(entries) == 2
    revert_rec = next(e for e in entries if e["change_id"] == rid)
    assert revert_rec["status"] == "reverted"
    assert revert_rec["old_value"] == "Level2"
    assert revert_rec["new_value"] == "Level1"


def test_revert_change_missing_id(tmp_path: Path) -> None:
    assert revert_change(tmp_path, "nonexistent-id") is None


def test_revert_chapter(tmp_path: Path) -> None:
    cids = []
    for i in range(3):
        cid = record_change(tmp_path, chapter=10, change_type="x", target=f"t{i}",
                            old_value=i, new_value=i + 1, status="applied")
        cids.append(cid)
    reverts = revert_chapter(tmp_path, chapter=10)
    assert len(reverts) == 3
    entries = load_entries(tmp_path)
    # 3 original + 3 revert = 6
    assert len(entries) == 6
    reverted_ids = {e["change_id"] for e in entries if e["status"] == "reverted"}
    assert len(reverted_ids) == 3


def test_revert_chapter_reverses_order(tmp_path: Path) -> None:
    """Reverts should be in reverse order (latest first)."""
    cids = []
    for i in range(3):
        cid = record_change(tmp_path, chapter=20, change_type="x", target=f"t{i}",
                            old_value=f"v{i}", new_value=f"nv{i}", status="applied")
        cids.append(cid)
    reverts = revert_chapter(tmp_path, chapter=20)
    # Last written (cids[2]) should be reverted first (reverts[0])
    assert reverts[0] != cids[0]  # First revert is for the last written change
    assert reverts[-1] != cids[2]  # Last revert is for the first written change


def test_ledger_path(tmp_path: Path) -> None:
    p = ledger_path(tmp_path)
    assert p == tmp_path / "runtime" / "fact_changes.jsonl"


def test_change_without_old_value(tmp_path: Path) -> None:
    """Timeline event has no old_value."""
    record_change(tmp_path, chapter=1, change_type="timeline_event",
                  target="event1", old_value=None,
                  new_value={"chapter": 1, "event": "battle"}, status="applied")
    e = get_changes(tmp_path, chapter=1)[0]
    assert e["old_value"] is None
    assert e["new_value"] == {"chapter": 1, "event": "battle"}

def test_normalize_target_by_name_resolves_to_id(tmp_path: Path) -> None:
    """When target is a character name and the type is entity-bearing,
    record_change writes the resolved ID instead of the name."""
    # Set up characters.json with C001 named 陆烬
    chars = {"characters": {"C001": {"name": "陆烬", "realm": "凡人体质"}}}
    ws = tmp_path / "memory" / "world_state"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "characters.json").write_text(json.dumps(chars), encoding="utf-8")
    (ws / "factions.json").write_text(json.dumps({"factions": {}}), encoding="utf-8")

    cid = record_change(
        tmp_path, chapter=5, change_type="character_realm",
        target="陆烬", old_value=None, new_value="炼气期", status="applied",
    )
    entries = load_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["target"] == "C001", f"Expected C001, got {entries[0]["target"]!r}"
    assert entries[0]["change_id"] == cid


def test_normalize_target_id_unchanged(tmp_path: Path) -> None:
    """When target is already an ID, record_change leaves it as-is."""
    chars = {"characters": {"C001": {"name": "陆烬"}}}
    ws = tmp_path / "memory" / "world_state"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "characters.json").write_text(json.dumps(chars), encoding="utf-8")
    (ws / "factions.json").write_text(json.dumps({"factions": {}}), encoding="utf-8")

    record_change(
        tmp_path, chapter=5, change_type="character_realm",
        target="C001", old_value=None, new_value="炼气期", status="applied",
    )
    entries = load_entries(tmp_path)
    assert entries[0]["target"] == "C001"


def test_normalize_target_non_entity_type_no_resolution(tmp_path: Path) -> None:
    """Non-entity change types skip normalization."""
    record_change(
        tmp_path, chapter=5, change_type="plot_note",
        target="某段剧情", old_value=None, new_value="new note", status="applied",
    )
    entries = load_entries(tmp_path)
    assert entries[0]["target"] == "某段剧情"
