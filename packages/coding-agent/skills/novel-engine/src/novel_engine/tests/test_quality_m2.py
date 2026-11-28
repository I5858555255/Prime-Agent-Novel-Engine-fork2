# -*- coding: utf-8 -*-
"""M2 early-batch TDD tests: M2-3 commit_override + M2-2 knob_rules (+ M2-10
migration_ledger, appended by that subagent). Synthetic fixtures + tmp_path +
real Store; NO real LLM. Each Store lives in its own tmp_path subdir so
leftover sqlite/WAL files never bleed across tests.

Probe-dir pattern is identical to test_quality_layer.py (module-import-time
pre-created probe dir + a session-scoped Store() lifecycle probe; a
sqlite-refused sandbox -> pytest.skip).
"""
import json
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from novel_engine.condensed.contracts import REVIEW_CHECKS, digest
from novel_engine.condensed.quality_store import (
    commit_override,
    knob_commits_for,
    record_override,
    validate_knob_commits,
    ledger_head,
    migrations,
    record_migration,
    validate_migration_ledger,
)
from novel_engine.condensed.store import Store

SOURCE = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# probe-dir fixture (identical pattern to test_quality_layer.py)
# ---------------------------------------------------------------------------
_QUALITY_PROBE_DIR = None
try:
    _probe_base = SOURCE / ".test_tmp"
    _probe_base.mkdir(exist_ok=True)
    _QUALITY_PROBE_DIR = Path(tempfile.mkdtemp(prefix="quality_m2_session_", dir=_probe_base))
except Exception:
    pass


@pytest.fixture(scope="session")
def quality_probe_dir():
    candidate = _QUALITY_PROBE_DIR
    if candidate is None or not candidate.exists():
        base = SOURCE / ".test_tmp"
        base.mkdir(exist_ok=True)
        candidate = Path(tempfile.mkdtemp(prefix="quality_m2_session_", dir=base))
    last_exc = None
    for _attempt in range(12):
        try:
            store = Store(candidate)
            store.head()
            store.record_trace({"run_id": "probe", "usage": {"total_tokens": 5},
                                "events": [{"kind": "provider", "usage": {"total_tokens": 5}}]})
            store.close()
            for entry in candidate.iterdir():
                entry.unlink(missing_ok=True)
            return candidate
        except Exception as exc:
            last_exc = exc
            base = SOURCE / ".test_tmp"
            base.mkdir(exist_ok=True)
            candidate = Path(tempfile.mkdtemp(prefix=f"quality_m2_session_try{_attempt}_", dir=base))
    pytest.skip(f"sqlite tmp dir unavailable in this sandbox (last probe error: {last_exc}); "
                "quality-store tests require a sqlite-writable tmp path")


@pytest.fixture
def tmp_path(quality_probe_dir):
    sub = quality_probe_dir / uuid.uuid4().hex
    sub.mkdir()
    yield sub
    shutil.rmtree(sub, ignore_errors=True)


# ---------------------------------------------------------------------------
# committed-chapter helper (mirrors test_quality_layer._committed)
# ---------------------------------------------------------------------------
def _plan(chapter=1, target=4210):
    return {"chapter": chapter, "objective": "发现账目缺页并保留证据", "cast": ["陆烬"],
            "events": [{"id": f"E{i}", "action": "核对账目", "cause": "收支不符", "outcome": "取得线索"} for i in range(1, 4)],
            "ending": "保存账册准备次日询问", "boundaries": ["不能提前确定作案者"], "expanded_han": target}


def _draft(chapter=1, target=4210, before=None):
    scenes = []
    for i in range(2):
        scenes.append({"id": f"S{i}", "location": "账房", "time": "当晚", "pov": "陆烬", "cast": ["陆烬"],
                       "start": "账目待核", "end": "保存线索", "emotion": "警惕转为疑惑",
                       "expanded_han": target // 2 + (target % 2 if i else 0),
                       "expansion_guidance": "展开核对过程，不补造作案者", "dialogue_intents": ["独处，无对白"],
                       "text": "陆烬核对账册，发现缺页。他先记录现有数字，再检查装订痕迹，决定暂不声张。" * 9,
                       "beats": [{"id": f"B{i}-{j}", "event_ids": [f"E{j}"], "action": "核对数字",
                                  "cause": "收支不符", "outcome": "发现缺页"} for j in ([1, 2] if i == 0 else [3])]})
    return {"chapter": chapter, "title": "缺页", "story_day": chapter,
            "scenes": scenes,
            "state_changes": [{"entity": "陆烬", "key": "掌握线索", "before": before,
                               "after": f"第{chapter}页缺失", "evidence_beat": "B0-1"}],
            "ending": "保存账册，尚未确定作案者"}


def _review(payload=None):
    result = {"input_hash": "input", "checks": dict.fromkeys(REVIEW_CHECKS, True), "issues": []}
    if payload is not None:
        result["chapter_hash"] = digest(payload)
    return result


def _committed(store, chapter=1):
    value = _draft(chapter, before=None if chapter == 1 else f"第{chapter - 1}页缺失")
    value.update(plan=_plan(chapter))
    store.commit(value, _review(value))
    return value


# === M2-3 commit_override ===
def test_commit_override_folds_into_chain(tmp_path):
    """record_override + commit_override -> a knob_commits leaf whose parent_hash
    equals chapters.hash for that chapter; the staging row's committed_at is set
    (consumption)."""
    store = Store(tmp_path / "store_fold")
    _committed(store, 1)
    db = store.db
    chapter_hash = store.chapter(1)["hash"]
    record_override(db, 1, "爽点", 4, "operator raised the 爽点 floor for a flat volume")
    leaf_hash = commit_override(db, store, 1, "爽点", 4, "operator raised the 爽点 floor for a flat volume")
    row = db.execute(
        "SELECT dim, new_value, override_hash, knob_commit_hash, parent_hash, trace_id "
        "FROM knob_commits WHERE chapter=1 AND dim='爽点'").fetchone()
    assert row is not None
    assert row[1] == 4 and row[4] == chapter_hash
    override_hash = db.execute(
        "SELECT override_hash FROM knob_overrides WHERE chapter=1 AND dim='爽点'").fetchone()[0]
    assert row[2] == override_hash
    expected = digest({"chapter": 1, "dim": "爽点", "new_value": 4,
                       "rationale": "operator raised the 爽点 floor for a flat volume",
                       "override_hash": override_hash, "parent_hash": chapter_hash, "trace_id": row[5]})
    assert row[3] == expected
    committed_at = db.execute(
        "SELECT committed_at FROM knob_overrides WHERE chapter=1 AND dim='爽点'").fetchone()[0]
    assert committed_at is not None
    assert leaf_hash == row[3]
    store.close()


def test_commit_override_idempotent(tmp_path):
    store = Store(tmp_path / "store_idem")
    _committed(store, 1)
    db = store.db
    record_override(db, 1, "钩子", 5, "bump hooks")
    first = commit_override(db, store, 1, "钩子", 5, "bump hooks")
    record_override(db, 1, "钩子", 5, "bump hooks")
    second = commit_override(db, store, 1, "钩子", 5, "bump hooks")
    assert first == second
    assert db.execute("SELECT COUNT(*) FROM knob_commits WHERE chapter=1 AND dim='钩子'").fetchone()[0] == 1
    store.close()


def test_commit_override_rejects_bad_dim(tmp_path):
    store = Store(tmp_path / "store_baddim")
    _committed(store, 1)
    db = store.db
    record_override(db, 1, "节奏", 4, "not a real dim")
    with pytest.raises(ValueError, match="dim"):
        commit_override(db, store, 1, "节奏", 4, "not a real dim")
    store.close()


def test_commit_override_requires_staging(tmp_path):
    store = Store(tmp_path / "store_nostage")
    _committed(store, 1)
    db = store.db
    with pytest.raises(ValueError, match="staging"):
        commit_override(db, store, 1, "爽点", 4, "no staging row exists")
    store.close()


def test_commit_override_rejects_bad_value(tmp_path):
    store = Store(tmp_path / "store_badval")
    _committed(store, 1)
    db = store.db
    record_override(db, 1, "压抑", 6, "out of range")
    with pytest.raises(ValueError, match="new_value"):
        commit_override(db, store, 1, "压抑", 6, "out of range")
    store.close()


def test_invalidate_deletes_knob_commits(tmp_path):
    store = Store(tmp_path / "store_inval")
    _committed(store, 1)
    _committed(store, 2)
    db = store.db
    record_override(db, 1, "爽点", 4, "ch1")
    record_override(db, 2, "钩子", 5, "ch2")
    commit_override(db, store, 1, "爽点", 4, "ch1")
    commit_override(db, store, 2, "钩子", 5, "ch2")
    assert db.execute("SELECT COUNT(*) FROM knob_commits").fetchone()[0] == 2
    store.invalidate_from(2, "M2-3 invalidate smoke test")
    assert db.execute("SELECT COUNT(*) FROM knob_commits WHERE chapter>=2").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM knob_commits WHERE chapter=1").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM knob_overrides WHERE chapter>=2").fetchone()[0] == 0
    store.close()


def test_validate_knob_commits_clean(tmp_path):
    store = Store(tmp_path / "store_valclean")
    _committed(store, 1)
    db = store.db
    record_override(db, 1, "爽点", 4, "clean")
    commit_override(db, store, 1, "爽点", 4, "clean")
    assert validate_knob_commits(db, store) == []
    store.close()


def test_validate_knob_commits_tampered(tmp_path):
    store = Store(tmp_path / "store_valtamper")
    _committed(store, 1)
    db = store.db
    record_override(db, 1, "爽点", 4, "tamper me")
    commit_override(db, store, 1, "爽点", 4, "tamper me")
    db.execute("UPDATE knob_commits SET knob_commit_hash='tampered' WHERE chapter=1")
    with pytest.raises(ValueError, match="knob_commit_hash mismatch"):
        validate_knob_commits(db, store)
    store.close()


def test_knob_commits_for_reads_only(tmp_path):
    store = Store(tmp_path / "store_readonly")
    _committed(store, 1)
    db = store.db
    record_override(db, 1, "爽点", 4, "a")
    record_override(db, 1, "钩子", 5, "b")
    commit_override(db, store, 1, "爽点", 4, "a")
    commit_override(db, store, 1, "钩子", 5, "b")
    leaves = knob_commits_for(db, 1)
    assert len(leaves) == 2
    assert {l["dim"] for l in leaves} == {"爽点", "钩子"}
    assert leaves[0]["id"] > leaves[1]["id"]
    assert all("knob_commit_hash" in l and "parent_hash" in l for l in leaves)
    store.close()


def test_commit_override_disabled_layer(tmp_path):
    project = tmp_path / "off_project"
    project.mkdir()
    conn = sqlite3.connect(str(project / "manuscript.sqlite3"))
    conn.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE chapters(
            number INTEGER PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL,
            parent_hash TEXT NOT NULL, review TEXT NOT NULL, story_day INTEGER NOT NULL);
        CREATE TABLE usage(run_id TEXT PRIMARY KEY, total_tokens INTEGER NOT NULL,
                           reported_requests INTEGER NOT NULL, captured_at TEXT);
    """)
    conn.close()
    (project / "quality_config.json").write_text(json.dumps({"enable_quality": False}), encoding="utf-8")
    store = Store(project)
    assert store.quality_enabled is False
    with pytest.raises(RuntimeError, match="quality layer not enabled"):
        commit_override(store.db, store, 1, "爽点", 4, "disabled layer")
    store.close()


def test_commit_override_verb_cli(tmp_path):
    from novel_engine.condensed.runner import CondensedPipeline, main as runner_main
    outdir = tmp_path / "cli_out"
    pipeline = CondensedPipeline(SOURCE, outdir)
    pipeline.store.close()
    seed = Store(outdir)
    _committed(seed, 1)
    seed.close()
    rc = runner_main(["commit-override", "--source", str(SOURCE), "--output", str(outdir),
                      "--chapter", "1", "--dim", "爽点", "--new-value", "4",
                      "--rationale", "cli smoke test"])
    assert rc == 0
    db = Store(outdir).db
    row = db.execute(
        "SELECT knob_commit_hash, parent_hash FROM knob_commits WHERE chapter=1 AND dim='爽点'").fetchone()
    assert row is not None
    assert row[1] == Store(outdir).chapter(1)["hash"]
    Store(outdir).close()


# === M2-2 knob_rules ===
V0_KNOB_CONFIG = json.dumps({
    "enable_quality": True,
    "knob_sets": [
        {"stage": 1, "knob_set": {"爽点": 3, "钩子": 3, "压抑": 2}},
        {"stage": 2, "knob_set": {"爽点": 4, "钩子": 4, "压抑": 3}},
    ],
})
V0_STAGE1 = {"爽点": 3, "钩子": 3, "压抑": 2}
V0_STAGE2 = {"爽点": 4, "钩子": 4, "压抑": 3}
M2_VOL = {"id": "VX", "title": "synthetic", "chapter_range": [317, 652],
          "theme": "逆袭重生", "core_conflicts": ["c"],
          "climax_chapter": 652, "climax_description": "d"}


def _with_cfg(monkeypatch, text):
    """Inject a synthetic quality_config (knob_rules on/off) for the duration of
    the test by monkeypatching load_quality_config — no source change."""
    from novel_engine.condensed import volume_knobs
    import json as _j
    monkeypatch.setattr(volume_knobs, "load_quality_config", lambda: _j.loads(text))


def test_knob_rules_no_config_is_v0_behavior(tmp_path, monkeypatch):
    _with_cfg(monkeypatch, V0_KNOB_CONFIG)
    from novel_engine.condensed.volume_knobs import select_knobs
    ks, rat = select_knobs(M2_VOL, 2)
    assert ks == V0_STAGE2
    assert rat == {"bible_field": "world", "climax_chapter": 652, "stage": 2}
    assert "rules_ver" not in rat and "matched_rule" not in rat
    ks1, rat1 = select_knobs(M2_VOL, 1)
    assert ks1 == V0_STAGE1 and rat1 == {"bible_field": "world", "climax_chapter": 652, "stage": 1}


def test_knob_rules_match(tmp_path, monkeypatch):
    obj = json.loads(V0_KNOB_CONFIG)
    obj["knob_rules"] = {"rules_ver": "v1", "rules": [
        {"id": "flat-volume", "when": {"theme_contains": "逆袭"},
         "knob_set": {"爽点": 5, "钩子": 4, "压抑": 2}},
        {"id": "late-climax", "when": {"climax_within_last_chapters": 5, "stage": 2},
         "knob_set": {"爽点": 4, "钩子": 5, "压抑": 3}},
    ], "fallback_stage_knobs": True}
    _with_cfg(monkeypatch, json.dumps(obj))
    from novel_engine.condensed.volume_knobs import select_knobs
    ks, rat = select_knobs(M2_VOL, 2)
    assert ks == {"爽点": 5, "钩子": 4, "压抑": 2}
    assert rat["matched_rule"] == "flat-volume" and rat["rules_ver"] == "v1"
    assert rat["bible_field"] == "world" and rat["climax_chapter"] == 652 and rat["stage"] == 2


def test_knob_rules_fallback(tmp_path, monkeypatch):
    obj = json.loads(V0_KNOB_CONFIG)
    obj["knob_rules"] = {"rules_ver": "v2", "rules": [
        {"id": "war", "when": {"theme_contains": "war", "stage": 2},
         "knob_set": {"爽点": 1, "钩子": 1, "压抑": 1}},
    ], "fallback_stage_knobs": True}
    _with_cfg(monkeypatch, json.dumps(obj))
    from novel_engine.condensed.volume_knobs import select_knobs
    ks, rat = select_knobs(M2_VOL, 2)
    assert ks == V0_STAGE2
    assert rat["matched_rule"] == "stage-fallback" and rat["rules_ver"] == "v2"
    assert rat["bible_field"] == "world" and rat["climax_chapter"] == 652 and rat["stage"] == 2


def test_knob_rules_deterministic(tmp_path, monkeypatch):
    obj = json.loads(V0_KNOB_CONFIG)
    obj["knob_rules"] = {"rules_ver": "v1", "rules": [
        {"id": "late-climax", "when": {"climax_within_last_chapters": 50},
         "knob_set": {"爽点": 5, "钩子": 5, "压抑": 4}},
    ], "fallback_stage_knobs": True}
    _with_cfg(monkeypatch, json.dumps(obj))
    from novel_engine.condensed.volume_knobs import select_knobs
    from novel_engine.condensed.knob_rules import evaluate_knob_rules
    a = select_knobs(M2_VOL, 2)
    b = select_knobs(M2_VOL, 2)
    c = evaluate_knob_rules(M2_VOL, 2, obj["knob_rules"])
    assert a[0] == b[0] == c[0] == {"爽点": 5, "钩子": 5, "压抑": 4}
    assert a[1] == b[1] == c[1]
    assert a[1]["matched_rule"] == "late-climax"


def test_knob_rules_versioned(tmp_path, monkeypatch):
    obj = json.loads(V0_KNOB_CONFIG)
    base = {"rules": [{"id": "r1", "when": {"theme_contains": "逆袭"},
                       "knob_set": {"爽点": 5, "钩子": 4, "压抑": 2}}],
            "fallback_stage_knobs": True}
    for ver in ("v1", "v2"):
        obj["knob_rules"] = {**base, "rules_ver": ver}
        _with_cfg(monkeypatch, json.dumps(obj))
        from novel_engine.condensed.volume_knobs import select_knobs
        ks, rat = select_knobs(M2_VOL, 2)
        assert ks == {"爽点": 5, "钩子": 4, "压抑": 2}
        assert rat["rules_ver"] == ver and rat["matched_rule"] == "r1"


# === M2-10 migration_ledger ===
def _expect_ledger_hash(row: dict, parent_hash: str) -> str:
    """Recompute ledger_hash the way record_migration stores it, so tests can
    pin the exact chain digest independent of the implementation's storage."""
    return digest({"migration_id": row["migration_id"], "from_version": row["from_version"],
                   "to_version": row["to_version"], "applied_at": row["applied_at"],
                   "description": row["description"], "parent_hash": parent_hash})


def test_record_migration_appends(tmp_path):
    """First row: from=0 to=1 parent=GENESIS. Second row: parent = the first
    row's ledger_hash (the ledger is a hash chain)."""
    store = Store(tmp_path)
    db = store.db
    # A real enable_quality store auto-backfills the v0 bootstrap row
    # (quality_schema_v1), so start the chain assertions from that head.
    h2 = record_migration(db, "quality_schema_v2", 1, 2, "add retention ingest")
    rows = migrations(db)
    assert [r["migration_id"] for r in rows] == ["quality_schema_v1", "quality_schema_v2"]
    assert rows[0]["parent_hash"] == "GENESIS"
    assert rows[1]["parent_hash"] == rows[0]["ledger_hash"]
    assert rows[1]["ledger_hash"] == _expect_ledger_hash(rows[1], rows[0]["ledger_hash"])
    assert h2 == rows[1]["ledger_hash"]
    store.close()


def test_record_migration_idempotent(tmp_path):
    """Recording the same migration_id twice returns the existing ledger_hash
    and does NOT write a duplicate row."""
    store = Store(tmp_path)
    db = store.db
    h1 = record_migration(db, "quality_schema_v1", 0, 1, "first pass")
    h1_again = record_migration(db, "quality_schema_v1", 0, 1, "first pass")
    assert h1_again == h1
    # The v0 bootstrap row (from backfill) is what 'quality_schema_v1' maps to.
    assert len(migrations(db)) == 1
    h2 = record_migration(db, "quality_schema_v2", 1, 2)
    assert h2 != h1
    assert len(migrations(db)) == 2
    store.close()


def test_store_backfills_v0_marker(tmp_path):
    """Opening a v0 store (chapters carry the schema_version marker, i.e. the
    quality_schema_version metadata row) with an empty ledger auto-backfills ONE
    row 'quality_schema_v1' 0->1. Re-opening does NOT double-record (idempotent),
    and existing chapters row hashes are untouched (purely additive)."""
    project = tmp_path / "backfill_project"
    project.mkdir()
    store = Store(project)
    _committed(store, 1)
    assert store.metadata("quality_schema_version") == 1
    rows = migrations(store.db)
    assert [r["migration_id"] for r in rows] == ["quality_schema_v1"]
    assert rows[0]["from_version"] == 0 and rows[0]["to_version"] == 1
    assert rows[0]["parent_hash"] == "GENESIS"
    chapter_hash = store.chapter(1)["hash"]
    store.close()
    reopened = Store(project)
    assert [r["migration_id"] for r in migrations(reopened.db)] == ["quality_schema_v1"]
    assert reopened.chapter(1)["hash"] == chapter_hash
    reopened.close()


def test_validate_migration_ledger_clean_and_tampered(tmp_path):
    """validate_migration_ledger recomputes every row + parent link: a clean
    ledger returns []; a tampered ledger_hash raises ValueError; a broken
    parent link also raises."""
    store = Store(tmp_path)
    db = store.db
    record_migration(db, "quality_schema_v2", 1, 2)
    assert validate_migration_ledger(db) == []
    db.execute("UPDATE migration_ledger SET ledger_hash='tampered' WHERE migration_id='quality_schema_v1'")
    with pytest.raises(ValueError, match="migration_ledger"):
        validate_migration_ledger(db)
    store.close()

    store2 = Store(tmp_path / "broken_parent")
    db2 = store2.db
    record_migration(db2, "quality_schema_v2", 1, 2)
    db2.execute("UPDATE migration_ledger SET parent_hash='WRONG' WHERE migration_id='quality_schema_v2'")
    with pytest.raises(ValueError, match="migration_ledger"):
        validate_migration_ledger(db2)
    store2.close()


def test_ledger_head(tmp_path):
    """ledger_head returns the latest to_version; 0 when the ledger is empty.

    A freshly-opened `enable_quality` Store auto-backfills quality_schema_v1,
    so its ledger is never empty. To exercise the 'empty -> 0' branch in
    isolation, build a bare sqlite DB with ONLY the migration_ledger table
    (no Store backfill), then drive the head through record_migration."""
    conn = sqlite3.connect(str(tmp_path / "bare_ledger.sqlite3"))
    conn.execute("""
        CREATE TABLE migration_ledger(
          migration_id TEXT PRIMARY KEY,
          from_version INTEGER NOT NULL, to_version INTEGER NOT NULL,
          applied_at TEXT NOT NULL, description TEXT,
          ledger_hash TEXT NOT NULL, parent_hash TEXT NOT NULL)""")
    conn.commit()
    assert ledger_head(conn) == 0
    record_migration(conn, "quality_schema_v1", 0, 1)
    assert ledger_head(conn) == 1
    record_migration(conn, "quality_schema_v2", 1, 2)
    assert ledger_head(conn) == 2
    conn.close()
    # The Store-open backfill path (a real enable_quality store) lands at head 1
    # from the bootstrap migration, and a subsequent explicit record raises it.
    store = Store(tmp_path / "store_head")
    assert ledger_head(store.db) == 1
    record_migration(store.db, "quality_schema_v2", 1, 2)
    assert ledger_head(store.db) == 2
    store.close()
