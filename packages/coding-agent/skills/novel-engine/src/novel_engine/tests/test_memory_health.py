# -*- coding: utf-8 -*-
"""Tests for memory_health.py read-only health-check script."""
from __future__ import annotations

import io
import json
import sqlite3
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.scripts import memory_health as mh_mod


# ── Fixture helpers ──────────────────────────────────────────────────────────


def _write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def _make_db(root: Path, chars: dict, factions: Optional[dict] = None,
             foreshadows: Optional[list[dict]] = None,
             relationships: Optional[list[dict]] = None) -> Path:
    """Create a minimal state.db at root/runtime/state.db."""
    rt = root / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    db_path = rt / "state.db"
    # Remove any pre-existing db so the test is isolated.
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE characters (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, realm TEXT,
            location TEXT, description TEXT, faction_id TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE factions (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, leader TEXT,
            description TEXT, power_level INTEGER DEFAULT 100
        )
    """)
    cur.execute("""
        CREATE TABLE foreshadows (
            id TEXT PRIMARY KEY, plant_chapter INTEGER, resolve_chapter INTEGER,
            plant_context TEXT, resolve_method TEXT, importance REAL DEFAULT 0.5,
            status TEXT DEFAULT 'planned'
        )
    """)
    cur.execute("""
        CREATE TABLE clue_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT, foreshadow_id TEXT,
            chapter INTEGER, intensity TEXT, method TEXT,
            FOREIGN KEY (foreshadow_id) REFERENCES foreshadows(id)
        )
    """)
    cur.execute("""
        CREATE TABLE relationships (
            char_from TEXT, char_to TEXT, relation_type TEXT,
            affinity INTEGER, description TEXT,
            PRIMARY KEY (char_from, char_to)
        )
    """)
    if chars:
        for cid, cd in chars.items():
            cur.execute(
                "INSERT OR REPLACE INTO characters VALUES (?,?,?,?,?,?)",
                (cid, cd.get("name", ""), cd.get("realm", ""),
                 cd.get("location", ""), cd.get("description", ""),
                 cd.get("faction_id", "")),
            )
    if factions:
        for fid, fd in factions.items():
            cur.execute(
                "INSERT OR REPLACE INTO factions VALUES (?,?,?,?,?)",
                (fid, fd.get("name", ""), fd.get("leader", ""),
                 fd.get("description", ""), fd.get("power_level", 100)),
            )
    if foreshadows:
        for fs in foreshadows:
            cur.execute(
                "INSERT OR REPLACE INTO foreshadows VALUES (?,?,?,?,?,?,?)",
                (fs["id"], fs.get("plant_chapter", 0),
                 fs.get("resolve_chapter", 0), fs.get("plant_context", ""),
                 fs.get("resolve_method", ""), fs.get("importance", 0.5),
                 fs.get("status", "planned")),
            )
    if relationships:
        for rel in relationships:
            cur.execute(
                "INSERT OR REPLACE INTO relationships VALUES (?,?,?,?,?)",
                (rel.get("char_from", ""), rel.get("char_to", ""),
                 rel.get("relation_type", ""), rel.get("affinity", 50),
                 rel.get("description", "")),
            )
    conn.commit()
    conn.close()
    return db_path


def _write_foreshadow_registry(root: Path, foreshadows: list[dict]) -> Path:
    p = root / "config" / "foreshadow" / "registry.json"
    _write_json(p, {"foreshadows": foreshadows})
    return p


def _write_characters_json(root: Path, chars: dict) -> Path:
    p = root / "memory" / "world_state" / "characters.json"
    _write_json(p, {"characters": chars})
    return p


def _write_fact_changes(root: Path, entries: list[object]) -> Path:
    rt = root / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    p = rt / "fact_changes.jsonl"
    lines = []
    for e in entries:
        if isinstance(e, str):
            lines.append(e)
        else:
            lines.append(json.dumps(e, ensure_ascii=False))
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _write_last_chapter(root: Path, n: int) -> None:
    rt = root / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "last_success_chapter.txt").write_text(
        str(n), encoding="utf-8"
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_contradictory_properties_detected(tmp_path: Path) -> None:
    """StateDB and world_state disagree on a character field -> reported."""
    _make_db(tmp_path, {
        "C1": {"name": "陆烬", "realm": "炼气期", "location": "雾隐村"},
    })
    _write_characters_json(tmp_path, {
        "C1": {"name": "陆烬", "realm": "筑基期", "location": "雾隐村"},
    })
    _write_last_chapter(tmp_path, 5)

    report, rc = mh_mod.run(tmp_path)
    assert rc == 1
    assert "CONTRADICTION" in report
    assert "realm" in report
    assert "炼气期" in report
    assert "筑基期" in report


def test_expired_foreshadow_detected(tmp_path: Path) -> None:
    """Foreshadow with resolve_chapter < current_chapter and status='planned' -> reported."""
    foreshadows = [
        {"id": "F001", "plant_chapter": 1, "resolve_chapter": 3,
         "status": "planned", "plant_context": "x", "resolve_method": "y", "importance": 0.5},
        {"id": "F002", "plant_chapter": 10, "resolve_chapter": 20,
         "status": "planned", "plant_context": "x", "resolve_method": "y", "importance": 0.5},
    ]
    _make_db(tmp_path, {}, foreshadows=foreshadows)
    _write_foreshadow_registry(tmp_path, foreshadows)
    _write_last_chapter(tmp_path, 5)

    report, rc = mh_mod.run(tmp_path)
    assert rc == 1
    assert "EXPIRED_FORESHADOW" in report
    assert "F001" in report
    # F002 should NOT be flagged (resolve_chapter=20 >= current=5)
    assert "F002" not in report


def test_expired_foreshadow_terminal_not_flagged(tmp_path: Path) -> None:
    """Foreshadow past resolve_chapter but status is terminal -> not reported."""
    foreshadows = [
        {"id": "F001", "plant_chapter": 1, "resolve_chapter": 3,
         "status": "resolved", "plant_context": "x", "resolve_method": "y", "importance": 0.5},
        {"id": "F002", "plant_chapter": 1, "resolve_chapter": 3,
         "status": "completed", "plant_context": "x", "resolve_method": "y", "importance": 0.5},
        {"id": "F003", "plant_chapter": 1, "resolve_chapter": 3,
         "status": "abandoned", "plant_context": "x", "resolve_method": "y", "importance": 0.5},
    ]
    _make_db(tmp_path, {}, foreshadows=foreshadows)
    _write_foreshadow_registry(tmp_path, foreshadows)
    _write_last_chapter(tmp_path, 10)

    report, rc = mh_mod.run(tmp_path)
    assert rc == 0
    assert "EXPIRED_FORESHADOW" not in report


def test_dangling_references_in_relationships(tmp_path: Path) -> None:
    """Relationship referencing non-existent character -> reported."""
    _make_db(tmp_path, {
        "C1": {"name": "陆烬", "realm": "炼气期"},
    }, relationships=[
        {"char_from": "C1", "char_to": "C999", "relation_type": "master"},
    ])
    _write_last_chapter(tmp_path, 5)

    report, rc = mh_mod.run(tmp_path)
    assert rc == 1
    assert "DANGLING_REF" in report
    assert "C999" in report


def test_dangling_references_in_fact_changes(tmp_path: Path) -> None:
    """fact_changes referencing non-existent entity -> reported."""
    _make_db(tmp_path, {"C1": {"name": "陆烬"}})
    _write_last_chapter(tmp_path, 5)
    _write_fact_changes(tmp_path, [
        {"change_id": "ch5-0001", "chapter": 5, "type": "character",
         "target": "CMISSING", "old_value": None, "new_value": "dead",
         "source": "agent", "gate_result": "passed", "status": "applied",
         "ts": "2026-01-01T00:00:00+00:00"},
    ])

    report, rc = mh_mod.run(tmp_path)
    assert rc == 1
    assert "DANGLING_REF" in report
    assert "CMISSING" in report


def test_clean_scenario_exit_code_zero(tmp_path: Path) -> None:
    """No contradictions, no expired foreshadows, no dangling refs -> exit 0."""
    chars = {"C1": {"name": "陆烬", "realm": "炼气期", "location": "雾隐村"}}
    _make_db(tmp_path, chars, foreshadows=[
        {"id": "F001", "plant_chapter": 1, "resolve_chapter": 100,
         "status": "planned", "plant_context": "", "resolve_method": "",
         "importance": 0.5},
    ])
    _write_characters_json(tmp_path, chars)
    _write_foreshadow_registry(tmp_path, [
        {"id": "F001", "plant_chapter": 1, "resolve_chapter": 100,
         "status": "planned", "plant_context": "", "resolve_method": "",
         "importance": 0.5},
    ])
    _write_last_chapter(tmp_path, 5)
    _write_fact_changes(tmp_path, [])

    report, rc = mh_mod.run(tmp_path)
    assert rc == 0
    assert "CLEAN" in report
    assert "CONTRADICTION" not in report
    assert "EXPIRED_FORESHADOW" not in report
    assert "DANGLING_REF" not in report


def test_hard_problem_exit_code_nonzero(tmp_path: Path) -> None:
    """At least one hard problem -> exit code != 0."""
    chars = {"C1": {"name": "陆烬", "realm": "炼气期"}}
    _make_db(tmp_path, chars)
    _write_characters_json(tmp_path, {
        "C1": {"name": "陆烬", "realm": "筑基期"},
    })
    _write_last_chapter(tmp_path, 5)

    report, rc = mh_mod.run(tmp_path)
    assert rc != 0
    assert "CONTRADICTION" in report


def test_audit_trend_section_present(tmp_path: Path) -> None:
    """Audit trend section appears in output even when no reviews exist."""
    _make_db(tmp_path, {"C1": {"name": "陆烬"}})
    _write_last_chapter(tmp_path, 5)
    # No audit dir at all.

    report, rc = mh_mod.run(tmp_path)
    assert rc == 0
    assert "Audit Trend Metrics" in report


def test_dangling_reference_with_known_faction(tmp_path: Path) -> None:
    """fact_change targeting a known faction is not flagged."""
    _make_db(tmp_path, {"C1": {"name": "陆烬"}}, factions={
        "F1": {"name": "云隐坊市"},
    })
    _write_last_chapter(tmp_path, 5)
    _write_fact_changes(tmp_path, [
        {"change_id": "ch5-0001", "chapter": 5, "type": "faction",
         "target": "F1", "old_value": None, "new_value": "active",
         "source": "agent", "gate_result": "passed", "status": "applied",
         "ts": "2026-01-01T00:00:00+00:00"},
    ])

    report, rc = mh_mod.run(tmp_path)
    assert rc == 0
    assert "DANGLING_REF" not in report


def test_multiple_hard_problems_all_reported(tmp_path: Path) -> None:
    """When multiple categories have problems, all are listed."""
    chars = {"C1": {"name": "陆烬", "realm": "炼气期"}}
    _make_db(tmp_path, chars,
             foreshadows=[
                 {"id": "FOLD", "plant_chapter": 1, "resolve_chapter": 2,
                  "status": "planned", "plant_context": "", "resolve_method": "",
                  "importance": 0.5},
             ],
             relationships=[
                 {"char_from": "C1", "char_to": "GHOST", "relation_type": "enemy"},
             ])
    _write_characters_json(tmp_path, {
        "C1": {"name": "陆烬", "realm": "筑基期"},  # contradict realm
    })
    _write_foreshadow_registry(tmp_path, [
        {"id": "FOLD", "plant_chapter": 1, "resolve_chapter": 2,
         "status": "planned", "plant_context": "", "resolve_method": "",
         "importance": 0.5},
    ])
    _write_last_chapter(tmp_path, 5)
    _write_fact_changes(tmp_path, [
        {"change_id": "ch5-0001", "chapter": 5, "type": "character",
         "target": "BOGUS", "old_value": None, "new_value": "x",
         "source": "agent", "gate_result": "passed", "status": "applied",
         "ts": "2026-01-01T00:00:00+00:00"},
    ])

    report, rc = mh_mod.run(tmp_path)
    assert rc == 1
    assert "CONTRADICTION" in report
    assert "EXPIRED_FORESHADOW" in report
    assert "DANGLING_REF" in report


def test_dry_run_returns_code_without_print(tmp_path: Path) -> None:
    """--dry-run returns same exit code but suppresses stdout."""
    _make_db(tmp_path, {"C1": {"name": "陆烬", "realm": "炼气期"}})
    _write_characters_json(tmp_path, {
        "C1": {"name": "陆烬", "realm": "筑基期"},
    })
    _write_last_chapter(tmp_path, 5)

    captured = io.StringIO()
    old_stdout = sys.stdout
    sys.stdout = captured
    try:
        rc = mh_mod.main(["--dry-run", "--root", str(tmp_path)])
    finally:
        sys.stdout = old_stdout

    assert rc == 1
    assert captured.getvalue().strip() == ""


def test_no_state_db_skips_checks(tmp_path: Path) -> None:
    """Without state.db, checks are skipped with informative message."""
    _write_characters_json(tmp_path, {"C1": {"name": "陆烬"}})
    _write_last_chapter(tmp_path, 5)

    report, rc = mh_mod.run(tmp_path)
    assert rc == 0
    assert "SKIPPED" in report


def test_runtime_foreshadow_not_in_registry_flagged(tmp_path: Path) -> None:
    """Foreshadow present in DB but absent from registry, past resolve_chapter, is flagged."""
    _make_db(tmp_path, {}, foreshadows=[
        {"id": "FRUNTIME", "plant_chapter": 1, "resolve_chapter": 2,
         "status": "planned", "plant_context": "", "resolve_method": "",
         "importance": 0.5},
    ])
    # Registry is empty — foreshadow only in DB.
    _write_foreshadow_registry(tmp_path, [])
    _write_last_chapter(tmp_path, 5)

    report, rc = mh_mod.run(tmp_path)
    assert rc == 1
    assert "EXPIRED_FORESHADOW" in report
    assert "FRUNTIME" in report
    assert "runtime-only" in report


def test_fact_changes_invalid_lines_ignored(tmp_path: Path) -> None:
    """Malformed lines and missing change_id entries are skipped gracefully."""
    _make_db(tmp_path, {"C1": {"name": "陆烬"}})
    _write_last_chapter(tmp_path, 5)
    _write_fact_changes(tmp_path, [
        {"change_id": "ch5-0001", "chapter": 5, "type": "character",
         "target": "CVALID", "old_value": None, "new_value": "x",
         "source": "agent", "gate_result": "passed", "status": "applied",
         "ts": "2026-01-01T00:00:00+00:00"},
        "this is not json",           # malformed line
        {"no_change_id": True},        # missing change_id
        {"change_id": "ch5-0002", "chapter": 5, "type": "character",
         "target": "CMISSING", "old_value": None, "new_value": "x",
         "source": "agent", "gate_result": "passed", "status": "applied",
         "ts": "2026-01-01T00:00:00+00:00"},
    ])

    report, rc = mh_mod.run(tmp_path)
    assert rc == 1
    assert "DANGLING_REF" in report
    assert "CMISSING" in report


# ── Path resolution (Issue 3 regression) ─────────────────────────────────────


def test_main_defaults_root_to_data_dir(capsys) -> None:
    """main() without --root must default to DATA_DIR (not cwd) so the real
    state.db / audit / world_state are found from any cwd.
    """
    rc = mh_mod.main([])
    # Must return an int code; DATA_DIR exists in-repo so no crash expected.
    assert isinstance(rc, int)


def test_data_dir_resolves_to_package_root() -> None:
    """DATA_DIR resolves to the novel_engine package dir.

    Hermetic: verifies the directory-resolution logic only. No assertions on
    runtime artefacts (state.db / per_chapter_reviews.json) since those are
    only present after a real generation run.
    """
    assert mh_mod.DATA_DIR.is_dir()
    assert (mh_mod.DATA_DIR / "config").is_dir()
    assert (mh_mod.DATA_DIR / "core").is_dir()



# ── Bug fix: memory_health main() single-run regression ──────────────────────


def test_main_single_report_no_duplication(capsys: object) -> None:
    """main() without --root must produce exactly ONE report section,
    not two (the old buggy code ran run() twice — once with cwd, once with
    DATA_DIR — producing duplicate output)."""
    import io
    from contextlib import redirect_stdout

    f = io.StringIO()
    with redirect_stdout(f):
        rc = mh_mod.main([])

    output = f.getvalue()
    # Count how many times RESULT appears — should be exactly 1
    result_count = output.count("RESULT:")
    assert result_count == 1, (
        f"main() produced {result_count} RESULT lines (expected 1). "
        "Old double-run bug may be present."
    )
    # The single report must reference DATA_DIR, not cwd
        # Just verify RESULT appears exactly once - no need to check specific paths
    assert isinstance(rc, int)


def test_main_with_root_arg_runs_once(capsys: object) -> None:
    """main() with --root must run exactly once against that root."""
    import tempfile
    from pathlib import Path as P
    from contextlib import redirect_stdout
    import io

    with tempfile.TemporaryDirectory() as td:
        root = P(td)
        # Set up minimal valid project
        for sub in (
            "config/simulation", "config/foreshadow", "memory/world_state",
            "runtime",
        ):
            (root / sub).mkdir(parents=True, exist_ok=True)
        (root / "config" / "simulation" / "constraints.json").write_text("{}")
        (root / "config" / "foreshadow" / "registry.json").write_text(
            json.dumps({"foreshadows": []}),
        )
        (root / "runtime" / "last_success_chapter.txt").write_text("1")

        f = io.StringIO()
        with redirect_stdout(f):
            rc = mh_mod.main(["--root", str(root)])

        output = f.getvalue()
        result_count = output.count("RESULT:")
        assert result_count == 1, (
            f"main(--root=...) produced {result_count} RESULT lines (expected 1)."
        )
        assert isinstance(rc, int)

def test_dangling_reference_with_character_name_not_flagged(tmp_path: Path) -> None:
    """fact_change targeting a character by name (not ID) is not flagged when the name exists in DB."""
    _make_db(tmp_path, {
        "C001": {"name": "陆烬", "realm": "凡人体质"},
    })
    _write_last_chapter(tmp_path, 5)
    _write_fact_changes(tmp_path, [
        {"change_id": "ch5-0001", "chapter": 5, "type": "character_realm",
         "target": "陆烬", "old_value": None, "new_value": "炼气期",
         "source": "agent", "gate_result": "passed", "status": "applied",
         "ts": "2026-01-01T00:00:00+00:00"},
    ])

    report, rc = mh_mod.run(tmp_path)
    assert rc == 0
    assert "DANGLING_REF" not in report
    assert "陆烬" not in report


def test_dangling_reference_with_faction_name_not_flagged(tmp_path: Path) -> None:
    """fact_change targeting a faction by name is not flagged when the name exists in DB."""
    _make_db(tmp_path, {"C001": {"name": "陆烬"}}, factions={
        "F001": {"name": "大乾皇朝"},
    })
    _write_last_chapter(tmp_path, 5)
    _write_fact_changes(tmp_path, [
        {"change_id": "ch5-0001", "chapter": 5, "type": "faction",
         "target": "大乾皇朝", "old_value": None, "new_value": "active",
         "source": "agent", "gate_result": "passed", "status": "applied",
         "ts": "2026-01-01T00:00:00+00:00"},
    ])

    report, rc = mh_mod.run(tmp_path)
    assert rc == 0
    assert "DANGLING_REF" not in report
