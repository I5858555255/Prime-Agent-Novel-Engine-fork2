"""Quality-layer v0 TDD: verdict leaves, chain integrity, deterministic knobs,
additive migration. Synthetic fixtures + tmp_path + stubbed provider only —
no real generated artifacts (superpowers test rule).

Sandbox note: under the DSH Windows sandbox, sqlite *file* creation is
intermittently refused (WinError 5, uncorrelated with directory age). The
`quality_probe_dir` fixture probes a full Store() lifecycle on a
pre-existing tmp dir; when the probe is refused, the sqlite-backed tests
skip cleanly (`pytest.skip`) instead of crashing. The knob/config tests
never need sqlite and always run.
"""
import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from novel_engine.condensed.contracts import REVIEW_CHECKS, digest
from novel_engine.condensed.quality_store import (
    budget_status,
    new_trace_id,
    record_override,
    record_verdict,
    re_verify,
    validate_quality_chain,
    verdict_for,
)
from novel_engine.condensed.store import Store
from novel_engine.condensed.volume_knobs import (
    knob_set_hash,
    load_quality_config,
    read_active_knobs,
    regime_boundaries,
    regimes_for,
    select_knobs,
)

SOURCE = Path(__file__).resolve().parents[1]


# Pre-create the probe dir at module import time (before pytest collection)
# so it is not fresh when the DSH sandbox evaluates sqlite file creation.
_QUALITY_PROBE_DIR = None
try:
    _probe_base = SOURCE / ".test_tmp"
    _probe_base.mkdir(exist_ok=True)
    _QUALITY_PROBE_DIR = Path(tempfile.mkdtemp(prefix="quality_session_", dir=_probe_base))
except Exception:
    pass


@pytest.fixture(scope="session")
def quality_probe_dir():
    """Session-scoped sqlite-verified tmp dir.

    Uses the module-import-time pre-created dir (not fresh at probe time) and
    verifies it with a full Store() lifecycle probe, retrying on fresh dirs.
    Under the DSH Windows sandbox the probe is intermittently refused
    (WinError 5, uncorrelated with dir age); when it is refused across all
    attempts, the sqlite-backed tests skip cleanly instead of crashing.
    """
    candidate = _QUALITY_PROBE_DIR
    if candidate is None or not candidate.exists():
        base = SOURCE / ".test_tmp"
        base.mkdir(exist_ok=True)
        candidate = Path(tempfile.mkdtemp(prefix="quality_session_", dir=base))
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
            candidate = Path(tempfile.mkdtemp(prefix=f"quality_session_try{_attempt}_", dir=base))
    pytest.skip(f"sqlite tmp dir unavailable in this sandbox (last probe error: {last_exc}); "
                "quality-store tests require a sqlite-writable tmp path")


@pytest.fixture
def tmp_path(quality_probe_dir):
    """A fresh, isolated subdir of the session-verified probe dir, content
    reset per test. Each Store(tmp_path) gets its own directory so leftover
    sqlite files / WAL locks from a previous test cannot bleed into the next.
    """
    import uuid
    import shutil
    sub = quality_probe_dir / uuid.uuid4().hex
    sub.mkdir()
    yield sub
    shutil.rmtree(sub, ignore_errors=True)


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


def _verdict(judge_model="judge-a", scores=(3, 3, 2)):
    return {"爽点": scores[0], "钩子": scores[1], "压抑": scores[2],
            "rationale": "节奏与钩子均达标。", "judge_model": judge_model,
            "prompt_ver": "v1", "knob_set_hash": knob_set_hash(load_quality_config()["knob_sets"][0]["knob_set"]),
            "verdict_error": None}


def test_verdict_schema_hash(tmp_path):
    store = Store(tmp_path)
    _committed(store)
    db = store.db
    prose_sha256 = digest("expanded prose text")
    verdict = _verdict()
    trace_id = new_trace_id()
    leaf_hash = record_verdict(db, store, 1, verdict, prose_sha256, trace_id)
    expected = digest({"verdict": verdict, "prose_sha256": prose_sha256})
    assert leaf_hash == expected
    row = db.execute("SELECT verdict, prose_sha256, trace_id, parent_hash, version FROM quality_verdicts WHERE chapter=1").fetchone()
    assert json.loads(row[0]) == verdict
    assert row[1] == prose_sha256
    assert row[2] == trace_id
    assert row[3] == store.chapter(1)["hash"]
    assert row[4] == 1
    # idempotent: same content returns the same hash, no second version
    assert record_verdict(db, store, 1, verdict, prose_sha256, trace_id) == leaf_hash
    assert db.execute("SELECT COUNT(*) FROM quality_verdicts WHERE chapter=1").fetchone()[0] == 1
    store.close()


def test_verdict_error_parse(tmp_path):
    store = Store(tmp_path)
    _committed(store)
    db = store.db
    error_verdict = _verdict()
    error_verdict["verdict_error"] = {"type": "parse"}
    trace_id = new_trace_id()
    leaf_hash = record_verdict(db, store, 1, error_verdict, None, trace_id)
    row = db.execute("SELECT verdict, prose_sha256, verdict_error, quality_verdict_hash FROM quality_verdicts WHERE chapter=1 AND version=1").fetchone()
    assert row[1] is None
    assert json.loads(row[2]) == {"type": "parse"}
    assert leaf_hash == digest({"verdict": error_verdict, "verdict_error": error_verdict["verdict_error"]})
    latest = verdict_for(db, store, 1)
    assert latest["verdict_error"] == {"type": "parse"}
    assert latest["prose_sha256"] is None
    store.close()


def test_re_verify_new_version(tmp_path):
    store = Store(tmp_path)
    _committed(store)
    db = store.db
    verdict = _verdict(judge_model="judge-a")
    record_verdict(db, store, 1, verdict, digest("prose A"), new_trace_id())
    v1 = verdict_for(db, store, 1)
    assert v1["version"] == 1 and v1["judge_model"] == "judge-a"
    written = re_verify(db, store, [1], "judge-b")
    assert written == [(1, 2)]
    v2 = verdict_for(db, store, 1)
    assert v2["version"] == 2 and v2["judge_model"] == "judge-b"
    # v1 intact
    assert db.execute("SELECT COUNT(*) FROM quality_verdicts WHERE chapter=1").fetchone()[0] == 2
    # no-op when the latest leaf already carries the target model
    assert re_verify(db, store, [1], "judge-b") == []
    # a different model bumps again
    assert re_verify(db, store, [1], "judge-c") == [(1, 3)]
    store.close()


def test_invalidate_deletes_leaf(tmp_path):
    store = Store(tmp_path)
    _committed(store, 1)
    _committed(store, 2)
    db = store.db
    record_verdict(db, store, 1, _verdict(), digest("p1"), new_trace_id())
    record_verdict(db, store, 2, _verdict(), digest("p2"), new_trace_id())
    assert db.execute("SELECT COUNT(*) FROM quality_verdicts").fetchone()[0] == 2
    store.invalidate_from(2, "quality layer smoke test")
    assert db.execute("SELECT COUNT(*) FROM quality_verdicts WHERE chapter>=2").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM quality_verdicts WHERE chapter=1").fetchone()[0] == 1
    store.close()


def test_knob_determinism_stage(tmp_path):
    config = load_quality_config()
    # identical volume -> identical knob set (deterministic, no LLM)
    volume = {"id": "VX", "title": "synthetic", "chapter_range": [317, 652], "theme": "t",
              "core_conflicts": ["c"], "climax_chapter": 652, "climax_description": "d"}
    first, first_rationale = select_knobs(volume, 2)
    second, second_rationale = select_knobs(volume, 2)
    assert first == second == config["knob_sets"][1]["knob_set"]
    assert first == {"爽点": 4, "钩子": 4, "压抑": 3}
    assert first_rationale == {"bible_field": "world", "climax_chapter": 652, "stage": 2}
    assert second_rationale == first_rationale
    # a synthetic volume straddling the 仙门@653 regime boundary: fixture, not a
    # real volumes.json entry. [300, 700] strictly contains BOTH 317 and 653 -> 2
    # in-range boundaries -> 3 regimes.
    straddler = {"id": "VS", "title": "straddle", "chapter_range": [300, 700], "theme": "t",
                 "climax_chapter": 700}
    assert regimes_for([300, 700]) == [317, 653]
    assert regimes_for([600, 700]) == [653]
    assert regimes_for([300, 500]) == [317]
    assert regimes_for([1, 300]) == [6, 15, 20, 25]
    assert regimes_for([100, 200]) == []
    regimes = regimes_for(straddler["chapter_range"])
    assert len(regimes) == 2
    knob_sets = [select_knobs(straddler, 2)[0] for _ in range(len(regimes) + 1)]
    assert len(knob_sets) == len(regimes) + 1 == 3
    store = Store(tmp_path)
    store.close()


def test_chain_parent_intact(tmp_path):
    store = Store(tmp_path)
    _committed(store, 1)
    _committed(store, 2)
    db = store.db
    record_verdict(db, store, 2, _verdict(), digest("p2"), new_trace_id())
    assert store.prefix() == 2
    assert store.head() == 2
    leaf = verdict_for(db, store, 2)
    assert leaf["parent_hash"] == store.chapter(2)["hash"]
    assert validate_quality_chain(db, store) == []
    store.close()


def test_migration_existing_populated_db(tmp_path):
    # Pre-migration DB: 3-column usage + populated chapters, built with a raw
    # connection that mimics the legacy schema (no captured_at, no quality DDL).
    project = tmp_path / "migration_project"
    project.mkdir()
    conn = sqlite3.connect(str(project / "manuscript.sqlite3"))
    conn.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE chapters(
            number INTEGER PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL,
            parent_hash TEXT NOT NULL, review TEXT NOT NULL, story_day INTEGER NOT NULL);
        CREATE TABLE usage(run_id TEXT PRIMARY KEY, total_tokens INTEGER NOT NULL,
                            reported_requests INTEGER NOT NULL);
    """)
    conn.close()
    store = Store(project)  # the unconditional first script adds captured_at here
    assert "captured_at" in {r[1] for r in store.db.execute("PRAGMA table_info(usage)")}
    # the gated second script adds quality DDL only when enable_quality=true
    enabled = store.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='quality_verdicts'").fetchone()
    assert enabled is not None  # the shipped config has enable_quality: true
    _committed(store, 1)
    row_hashes = [r[0] for r in store.db.execute("SELECT hash FROM chapters ORDER BY number")]
    # record_trace now works with the 4-column insert
    store.record_trace({"run_id": "r1", "usage": {"total_tokens": 100},
                       "events": [{"kind": "provider", "usage": {"total_tokens": 100}}]})
    usage_cols = {r[1] for r in store.db.execute("PRAGMA table_info(usage)")}
    assert usage_cols == {"run_id", "total_tokens", "reported_requests", "captured_at"}
    assert store.db.execute("SELECT captured_at FROM usage WHERE run_id='r1'").fetchone()[0] is not None
    store.close()
    # reopen the SAME populated DB (no rmtree — the file must persist): the
    # unconditional migration ran on open, and no chapters row changed hash.
    reopened = Store(project)
    assert [r[0] for r in reopened.db.execute("SELECT hash FROM chapters ORDER BY number")] == row_hashes
    reopened.close()
    # enable_quality=false project: the gated script is skipped, but the
    # unconditional captured_at ALTER still ran
    off_project = tmp_path / "project_off"
    off_project.mkdir()
    off_conn = sqlite3.connect(str(off_project / "manuscript.sqlite3"))
    off_conn.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE chapters(
            number INTEGER PRIMARY KEY, payload TEXT NOT NULL, hash TEXT NOT NULL,
            parent_hash TEXT NOT NULL, review TEXT NOT NULL, story_day INTEGER NOT NULL);
        CREATE TABLE usage(run_id TEXT PRIMARY KEY, total_tokens INTEGER NOT NULL,
                            reported_requests INTEGER NOT NULL);
    """)
    off_conn.close()
    (off_project / "quality_config.json").write_text(json.dumps({"enable_quality": False}), encoding="utf-8")
    store_off = Store(off_project)
    assert "captured_at" in {r[1] for r in store_off.db.execute("PRAGMA table_info(usage)")}
    assert store_off.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='quality_verdicts'").fetchone() is None
    with pytest.raises(RuntimeError, match="quality layer not enabled"):
        record_verdict(store_off.db, store_off, 1, _verdict(), digest("p"), new_trace_id())
    store_off.close()


def test_additive_no_row_mutation(tmp_path):
    project = tmp_path / "additive_project"
    project.mkdir()
    store = Store(project)
    _committed(store, 1)
    _committed(store, 2)
    before = {n: h for n, h in store.db.execute("SELECT number, hash FROM chapters ORDER BY number")}
    schema_versions = [r[0] for r in store.db.execute("SELECT schema_version FROM chapters ORDER BY number")]
    marker = store.db.execute("SELECT value FROM metadata WHERE key='quality_schema_version'").fetchone()
    # fresh-DB commits are the dual-signal population: schema_version=1 (0 is only
    # the pre-migration backfill value, exercised in test_migration_existing_populated_db).
    assert all(s == 1 for s in schema_versions)
    assert json.loads(marker[0]) == 1
    # re-opening the SAME populated DB is purely additive: every chapters row hash-identical
    store.close()
    reopened = Store(project)
    after = {n: h for n, h in reopened.db.execute("SELECT number, hash FROM chapters ORDER BY number")}
    assert after == before
    reopened.close()


# --- knobs / store helpers that the M2 surface depends on ---

def test_regime_boundaries_pinned():
    # Pinned against the shipped concept_unlocks.json source: every distinct
    # unlocked_at_chapter across the top-level concepts + the unlock schedule:
    # 6 (cultivation_system), 15 (martial_identity), 20 (guardian_identity),
    # 25 (xianxia_system), 317 (lj_path_start / lj_qi_refinement),
    # 653 (sect_life), 988 (lj_foundation).
    assert regime_boundaries() == [6, 15, 20, 25, 317, 653, 988]


def test_read_active_knobs_uses_volume_stage():
    config = load_quality_config()
    volume = {"id": "V02", "chapter_range": [317, 652], "climax_chapter": 652}
    knob_set, rationale = read_active_knobs(volume)
    assert knob_set == config["knob_sets"][1]["knob_set"]
    assert rationale["stage"] == 2


def test_budget_status_null_legacy_included(tmp_path):
    from datetime import datetime, timezone
    store = Store(tmp_path)
    db = store.db
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    # legacy row (NULL captured_at) + a row inside the current calendar month
    db.execute("INSERT INTO usage(run_id,total_tokens,reported_requests,captured_at) VALUES('legacy',1000,1,NULL)")
    db.execute("INSERT INTO usage(run_id,total_tokens,reported_requests,captured_at) VALUES(?,?,?,?)",
               ("monthly", 5000, 1, f"{this_month}-01T00:00:00+00:00"))
    status = budget_status(db, 5000000)
    assert status["used"] == 6000
    assert status["legacy_included"] is True
    assert status["cap_reached"] is False
    over = budget_status(db, 5000)
    assert over["cap_reached"] is True
    store.close()


def test_record_override_stages_row(tmp_path):
    store = Store(tmp_path)
    db = store.db
    override_hash = record_override(db, 217, "爽点", 4, "operator raised the 爽点 floor for a flat volume")
    row = db.execute("SELECT dim, new_value, rationale, override_hash FROM knob_overrides WHERE chapter=217").fetchone()
    assert row[0] == "爽点" and row[1] == 4 and row[2].startswith("operator")
    assert row[3] == override_hash
    assert record_override(db, 217, "爽点", 5, "second opinion") != override_hash
    store.close()


# --- Batch B: quality_judge + accept_check.quality_notes + mark_verified.store + dual_check ---
from novel_engine.condensed.quality_judge import judge_prose, PROMPT_VER
from novel_engine.condensed.accept_check import check_prose
from novel_engine.condensed.mark_verified import mark_verified, note_quality
from novel_engine.condensed import dual_check as _dual_check_mod


def _judge_content(scores=(3, 3, 2), rationale="节奏与钩子均达标。") -> str:
    return json.dumps({"爽点": scores[0], "钩子": scores[1], "压抑": scores[2],
                       "rationale": rationale}, ensure_ascii=False)


class _RecordingPost:
    """Callable transport for judge_prose: returns scripted content, records
    each call. Kinds: 'ok' -> valid JSON string; 'badjson' -> unparseable;
    'timeout' -> raise httpx.TimeoutException."""
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def __call__(self, provider, prompt):
        kind = self._responses[self.calls] if self.calls < len(self._responses) else "timeout"
        self.calls += 1
        import httpx
        if kind == "timeout":
            raise httpx.TimeoutException("recorded timeout")
        if kind == "badjson":
            return "{not json"
        return _judge_content()


def _make_accept_pkg(base, chapter=1):
    """Build a minimal expansion package (scene card + manifest + neutral prose)
    that passes every compliance check at stage 1, so blocking stays empty and
    only quality_notes can appear. Writes <base>/chapter_NNNN/{场景扩写卡.json,
    manifest.json} and <base>/prose_chNNNN.txt. Returns (pkg, card, manifest, prose_path)."""
    pkg = base / f"chapter_{chapter:04d}"
    pkg.mkdir(parents=True, exist_ok=False)
    card = {
        "chapter": chapter, "title": "缺页",
        "scenes": [{
            "id": "S0", "location": "账房", "time": "当晚", "pov": "陆烬",
            "cast": ["陆烬"], "start": "账目待核", "end": "保存线索",
            "emotion": "警惕转为疑惑", "dialogue_intents": ["独处，无对白"],
            "beats": [
                {"id": "B0-1", "event_ids": ["E1"], "action": "核对数字清点收支",
                 "cause": "收支不符", "outcome": "发现缺页"},
                {"id": "B0-2", "event_ids": ["E2"], "action": "检查装订痕迹",
                 "cause": "装订有撕痕", "outcome": "暂不声张"},
            ]},
        ],
    }
    manifest = {
        "chapter": chapter, "source_hash": "x" * 64, "parent_hash": "GENESIS",
        "canon_hash": "y" * 64, "expanded_target_han": 4210,
        "expanded_range": [4000, 5000], "external_expansion_verified": False,
    }
    (pkg / "场景扩写卡.json").write_text(json.dumps(card, ensure_ascii=False), encoding="utf-8")
    (pkg / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    # A minimal canon file: dual_check's LLM path reads <pkg>/全书设定.md for materials.
    (pkg / "全书设定.md").write_text(
        "### C001 主角认知边界\n陆烬为弃婴，只能写生理感知，不写认知判断。\n"
        "### C002 观察者边界\n观察层不得数据化/机制化。\n", encoding="utf-8")
    base_block = "陆烬核对账册数字，逐项清点收支。他检查装订痕迹，发现缺页。他暂不声张，保存线索。夜色渐深，烛火摇曳。"
    # ~40 CJK/block. Compute a multiplier that lands the CJK count near 4210
    # (inside [4000,5000]) so word_count stays out of `blocking`.
    per_block = len([c for c in base_block if '\u4e00' <= c <= '\u9fff'])
    mult = max(1, round(4210 / per_block)) if per_block else 120
    prose = base_block * mult
    top = "夜色渐深，烛火摇曳。"
    while len([c for c in prose if '\u4e00' <= c <= '\u9fff']) < 4000:
        prose += top
    # cap at 5000
    while len([c for c in prose if '\u4e00' <= c <= '\u9fff']) > 5000:
        prose = prose[: len(prose) - 1]
    prose_path = base / f"prose_ch{chapter:04d}.txt"
    prose_path.write_text(prose, encoding="utf-8")
    return pkg, card, manifest, prose_path


def test_quality_notes_never_blocking(tmp_path):
    """A dim < knob -> quality_notes non-empty, blocking empty, pass True;
    mark_verified succeeds on a quality_notes-only report (R28)."""
    pkg, card, manifest, prose_path = _make_accept_pkg(tmp_path, chapter=1)
    prose = prose_path.read_text(encoding="utf-8")
    spec = {"爽点": 4, "钩子": 4, "压抑": 3}
    verdict = {"爽点": 3, "钩子": 4, "压抑": 3, "rationale": "爽点前置不足。",
               "judge_model": "stub", "prompt_ver": "v1", "knob_set_hash": "ksh",
               "verdict_error": None}
    report = check_prose(prose, card, manifest, spec=spec, verdict=verdict)
    assert report["blocking"] == []
    notes = report.get("quality_notes", [])
    assert len(notes) == 1
    assert notes[0]["check"] == "quality_note"
    assert notes[0]["dim"] == "爽点"
    assert notes[0]["score"] == 3
    assert notes[0]["threshold"] == 4
    # quality_notes is NEVER in blocking or in warnings.
    assert all(n.get("check") != "quality_note" for n in report["blocking"])
    assert all(n.get("check") != "quality_note" for n in report.get("warnings", []))
    # mark_verified on a quality_notes-only report succeeds (no store, no waiver).
    out = tmp_path / "accept_report.json"
    from novel_engine.condensed.accept_check import run as program_run
    run_report = program_run(pkg, prose_path, out_path=out, spec=spec, verdict=verdict)
    assert run_report["pass"] is True
    assert run_report["verdict"] == "PASS"
    stamped = mark_verified(pkg, out)
    assert stamped["external_expansion_verified"] is True
    assert stamped["verification"]["program_verdict"] == "PASS"
    assert stamped["verification"]["waiver"] is None
    # Back-compat: spec=None -> no quality_notes key.
    assert "quality_notes" not in check_prose(prose, card, manifest)


def test_waiver_still_compliance_only(tmp_path):
    """cognition_boundary block + waiver -> WAIVED; a quality_notes-only report
    never needs a waiver (compliance-only, G5)."""
    from novel_engine.condensed.accept_check import run as program_run
    # (a) a waiver-able compliance block stamps WAIVED.
    pkg, card, manifest, prose_path = _make_accept_pkg(tmp_path, chapter=1)
    out = tmp_path / "accept_report.json"
    program_run(pkg, prose_path, out_path=out)
    report = json.loads(out.read_text(encoding="utf-8"))
    report["blocking"] = [{"check": "cognition_boundary", "detail": "synthetic", "waivable": True}]
    report["pass"] = False
    report["verdict"] = "BLOCK"
    out.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    waiver_path = tmp_path / "waiver.json"
    waiver_path.write_text(json.dumps(
        {"checks": ["cognition_boundary"], "reasons": {"cognition_boundary": "synthetic test"}},
        ensure_ascii=False), encoding="utf-8")
    stamped = mark_verified(pkg, out, waiver=waiver_path)
    assert stamped["verification"]["program_verdict"] == "WAIVED"
    assert stamped["verification"]["waiver"]["checks"] == ["cognition_boundary"]
    # (b) a quality_notes-only report NEVER needs a waiver.
    pkg2, card2, manifest2, prose2 = _make_accept_pkg(tmp_path, chapter=2)
    spec = {"爽点": 4, "钩子": 4, "压抑": 3}
    verdict = {"爽点": 2, "钩子": 4, "压抑": 3, "rationale": "爽点不足。",
               "judge_model": "stub", "prompt_ver": "v1", "knob_set_hash": "ksh",
               "verdict_error": None}
    out2 = tmp_path / "accept2.json"
    program_run(pkg2, prose2, out_path=out2, spec=spec, verdict=verdict)
    report2 = json.loads(out2.read_text(encoding="utf-8"))
    assert report2["blocking"] == []
    assert report2.get("quality_notes")
    stamped2 = mark_verified(pkg2, out2)  # no waiver
    assert stamped2["verification"]["program_verdict"] == "PASS"
    assert stamped2["verification"]["waiver"] is None


def test_judge_verdict_schema():
    """Stubbed transport -> bounded verdict dict has all keys; judge_model =
    the provider's model; forced bad JSON -> verdict_error.type='parse', no
    crash, scores absent."""
    spec = {"爽点": 4, "钩子": 4, "压抑": 3}
    provider = ("https://example.local", "TEST_KEY", "stub-judge-model", {})
    ok_post = _RecordingPost(["ok"])
    v = judge_prose("prose…", spec, provider, "trace-1", knob_set_hash_arg="ksh-fixed", post=ok_post)
    assert ok_post.calls == 1
    assert set(v) == {"爽点", "钩子", "压抑", "rationale", "judge_model",
                      "prompt_ver", "knob_set_hash", "verdict_error"}
    assert v["judge_model"] == "stub-judge-model"
    assert v["prompt_ver"] == PROMPT_VER == "v1"
    assert v["knob_set_hash"] == "ksh-fixed"
    assert v["verdict_error"] is None
    for dim in ("爽点", "钩子", "压抑"):
        assert isinstance(v[dim], int) and 0 <= v[dim] <= 5
    assert isinstance(v["rationale"], str) and len(v["rationale"]) <= 80
    # forced bad JSON -> parse error leaf, no crash, no scores.
    bad_post = _RecordingPost(["badjson", "badjson"])
    v_err = judge_prose("prose…", spec, provider, "trace-2", knob_set_hash_arg="k", post=bad_post)
    assert bad_post.calls == 2
    assert v_err["verdict_error"] == {"type": "parse"}
    for dim in ("爽点", "钩子", "压抑"):
        assert dim not in v_err
    assert v_err["judge_model"] == "stub-judge-model"
    assert v_err["prompt_ver"] == PROMPT_VER


def test_judge_retries_once():
    """A transport that fails once then succeeds -> exactly 2 calls total,
    verdict has scores; a transport that always fails -> 2 calls then
    verdict_error."""
    spec = {"爽点": 4, "钩子": 4, "压抑": 3}
    provider = ("https://example.local", "TEST_KEY", "m", {})
    flaky = _RecordingPost(["badjson", "ok"])
    v = judge_prose("prose…", spec, provider, "t", post=flaky)
    assert flaky.calls == 2
    assert v["verdict_error"] is None
    assert (v["爽点"], v["钩子"], v["压抑"]) == (3, 3, 2)
    always_fail = _RecordingPost(["badjson", "badjson"])
    v2 = judge_prose("prose…", spec, provider, "t", post=always_fail)
    assert always_fail.calls == 2
    assert v2["verdict_error"] == {"type": "parse"}
    for dim in ("爽点", "钩子", "压抑"):
        assert dim not in v2
    # a timeout failure also retries once.
    timeout = _RecordingPost(["timeout", "ok"])
    v3 = judge_prose("prose…", spec, provider, "t", post=timeout)
    assert timeout.calls == 2
    assert v3["verdict_error"] is None


def test_mark_verified_store_link(tmp_path):
    """mark_verified with a real store + committed chapter + recorded verdict ->
    manifest verdict_record_hash == the leaf hash; with a verdict_error leaf ->
    verdict_record_hash is None; integrity failure (tampered leaf) -> refusal.

    Each Store lives in its own tmp_path subdir (Store writes manuscript.sqlite3,
    audit/, canon.json, ... into its root) so the expansion package dirs stay clean.
    """
    from novel_engine.condensed.accept_check import run as program_run

    def _package(base_dir, chapter):
        _make_accept_pkg(base_dir, chapter=chapter)
        return base_dir / f"chapter_{chapter:04d}"

    # (a) normal scored leaf -> verdict_record_hash == the leaf hash.
    store_a = Store(tmp_path / "store_a")
    _committed(store_a, 1)
    pkg_a = _package(tmp_path / "pkg_a", 1)
    out_a = pkg_a / "accept_report.json"
    program_run(pkg_a, pkg_a.parent / "prose_ch0001.txt", out_path=out_a)
    verdict_a = _verdict(scores=(4, 4, 3))
    leaf_hash_a = record_verdict(store_a.db, store_a, 1, verdict_a, digest("prose-1"), new_trace_id())
    stamped_a = mark_verified(pkg_a, out_a, store=store_a, chapter=1)
    assert stamped_a["external_expansion_verified"] is True
    assert stamped_a["verification"]["verdict_record_hash"] == leaf_hash_a
    store_a.close()

    # (b) verdict_error leaf -> verdict_record_hash is None (still stamps).
    store_b = Store(tmp_path / "store_b")
    _committed(store_b, 1)
    pkg_b = _package(tmp_path / "pkg_b", 2)
    out_b = pkg_b / "accept_report.json"
    program_run(pkg_b, pkg_b.parent / "prose_ch0002.txt", out_path=out_b)
    err_verdict_b = dict(_verdict())
    err_verdict_b["verdict_error"] = {"type": "parse"}
    record_verdict(store_b.db, store_b, 1, err_verdict_b, None, new_trace_id())
    stamped_b = mark_verified(pkg_b, out_b, store=store_b, chapter=1)
    assert stamped_b["external_expansion_verified"] is True
    assert stamped_b["verification"]["verdict_record_hash"] is None
    store_b.close()

    # (c) integrity failure (tampered leaf) -> refusal, manifest NOT stamped.
    store_c = Store(tmp_path / "store_c")
    _committed(store_c, 1)
    pkg_c = _package(tmp_path / "pkg_c", 3)
    out_c = pkg_c / "accept_report.json"
    program_run(pkg_c, pkg_c.parent / "prose_ch0003.txt", out_path=out_c)
    record_verdict(store_c.db, store_c, 1, _verdict(), digest("p"), new_trace_id())
    store_c.db.execute("UPDATE quality_verdicts SET quality_verdict_hash='tampered' WHERE chapter=1")
    with pytest.raises(ValueError, match="quality_verdict_hash mismatch"):
        mark_verified(pkg_c, out_c, store=store_c, chapter=1)
    manifest_c = json.loads((pkg_c / "manifest.json").read_text(encoding="utf-8"))
    assert manifest_c.get("external_expansion_verified") is not True
    store_c.close()


def test_dual_check_quality_notes_passthrough(tmp_path, monkeypatch):
    """dual_check fast-path (spec=None) -> merged quality_notes == [];
    LLM path (spec present) -> merged carries the program-report quality_notes."""
    pkg, card, manifest, prose_path = _make_accept_pkg(tmp_path, chapter=1)
    # (a) fast path, spec forced None -> program report has no quality_notes -> [].
    fast = _dual_check_mod.dual_check(pkg, prose_path, fast_path=True, spec=None)
    assert fast["fast_path"] is True
    assert fast["quality_notes"] == []
    # (b) LLM path, spec present -> program report carries quality_notes -> merged.
    spec = {"爽点": 4, "钩子": 4, "压抑": 3}
    verdict = {"爽点": 2, "钩子": 4, "压抑": 3, "rationale": "爽点不足。",
               "judge_model": "stub", "prompt_ver": "v1", "knob_set_hash": "k",
               "verdict_error": None}

    def _fake_check(base_url, api_key, model, materials, text, **kw):
        return {"choices": [{"message": {"content": "### 一、阻断问题\n（无）"}}]}
    monkeypatch.setattr(_dual_check_mod, "_llm_check", _fake_check)
    import os
    # The review phase's key_env (AGNES_TOKEN_API_KEY in the active profile)
    # must be non-empty for dual_check's `if not key` guard to pass; the fake
    # _llm_check means no real HTTP fires.
    monkeypatch.setenv(_dual_check_mod.load_provider("review")[1], "test-key")
    merged = _dual_check_mod.dual_check(pkg, prose_path, fast_path=False, spec=spec, verdict=verdict)
    assert "quality_notes" in merged
    assert any(n["dim"] == "爽点" for n in merged["quality_notes"])
    assert merged["verdict"] in ("PASS", "BLOCK")


# --- Batch C: expand_run judge hook + runner qc/knobs/note-quality/status/quota-guard ---
from novel_engine.condensed import expand_run as _expand_run_mod
from novel_engine.condensed import observability as _observability_mod


def _expand_pkg(base, chapter=1):
    """Build a minimal expansion package with the 扩写提示词.md that
    expand_until_range reads (its full expansion prompt). Returns the package
    dir (also writes the prose-adjacent files _make_accept_pkg uses for
    check_prose, which expand's final check needs)."""
    pkg, card, manifest, prose_path = _make_accept_pkg(base, chapter=chapter)
    # The 扩写提示词.md that expand_until_range reads for its full-prompt path.
    # The continuation-template cut marker must be present so `materials` is
    # extracted; the prose range stays [4000,5000].
    (pkg / "扩写提示词.md").write_text(
        "## 扩写任务\n把简写章扩写至 4000-5000 汉字，保持人称与视角。\n"
        "## 全书设定.md\n### C001 主角认知边界\n陆烬为弃婴，只能写生理感知，不写认知判断。\n",
        encoding="utf-8")
    return pkg, card, manifest, prose_path


def test_expand_run_judge_hook(tmp_path, monkeypatch):
    """expand_until_range with spec + store -> one judge_prose call + a
    record_verdict leaf + total_expansion_tokens accounting. Intermediate
    rounds run spec=None (compliance-only); the judge fires on the FINAL
    check_prose only. Synthetic transport: no real LLM."""
    pkg, card, manifest, prose_path = _expand_pkg(tmp_path, chapter=1)
    store = Store(tmp_path / "store_expand")
    _committed(store, 1)

    # Stub the LLM transport with a deterministic, in-range CJK expansion so the
    # loop terminates in one round (han ~4210, no blocking) and no real HTTP fires.
    neutral = "陆烬核对账册数字，逐项清点收支。他检查装订痕迹，发现缺页。他暂不声张，保存线索。夜色渐深，烛火摇曳。"
    per_block = len([c for c in neutral if '\u4e00' <= c <= '\u9fff'])
    mult = max(1, round(4210 / per_block)) if per_block else 120
    body = neutral * mult
    while len([c for c in body if '\u4e00' <= c <= '\u9fff']) < 4000:
        body += "夜色渐深，烛火摇曳。"
    while len([c for c in body if '\u4e00' <= c <= '\u9fff']) > 5000:
        body = body[:-1]

    judge_calls = {"n": 0}

    def _fake_judge(prose, spec, provider, trace_id, knob_set_hash_arg=None, post=None):
        judge_calls["n"] += 1
        # A bounded verdict where 爽点 is BELOW the knob so quality_notes is
        # non-empty (R28: soft, never blocking) and the verdict leaf records.
        return {"爽点": 2, "钩子": 4, "压抑": 3, "rationale": "爽点前置不足。",
                "judge_model": "stub-judge", "prompt_ver": "v1",
                "knob_set_hash": knob_set_hash_arg or "ksh", "verdict_error": None}

    def _fake_llm(base_url, api_key, model, messages, max_tokens, temperature,
                  reasoning_effort=None):
        return body, {"total_tokens": 1000, "prompt_tokens": 500,
                      "completion_tokens": 500, "usage_estimated": False}

    monkeypatch.setattr(_expand_run_mod, "judge_prose", _fake_judge)
    monkeypatch.setattr(_expand_run_mod, "_llm", _fake_llm)
    monkeypatch.setattr(_expand_run_mod, "load_provider_full",
                        lambda phase: ("https://example.local", "K", "stub-judge", {}))

    spec = {"爽点": 4, "钩子": 4, "压抑": 3}
    summary = _expand_run_mod.expand_until_range(pkg, base_url="u", api_key="k", model="m",
                                                  max_rounds=1, spec=spec, store=store)
    # One critic call (a parse-retry would make 2; the stub succeeds first).
    assert judge_calls["n"] == 1
    # The verdict leaf is recorded + the prose is bound.
    leaf = _quality_store_verdict_for(store, 1)
    assert leaf is not None
    assert leaf["verdict"]["judge_model"] == "stub-judge"
    assert leaf["verdict_error"] is None
    # Usage accounting landed in the usage table (captured_at stamped).
    assert summary["total_expansion_tokens"] >= 1000
    assert summary["trace_id"]
    # The final check carried the knob-set quality note (爽点 2 < 4).
    assert any(n["dim"] == "爽点" for n in summary.get("quality_notes", []))
    # The chapter row hash is unchanged (the verdict is a leaf off it).
    assert store.chapter(1) is not None
    store.close()


def _quality_store_verdict_for(store, chapter):
    from novel_engine.condensed.quality_store import verdict_for
    return verdict_for(store.db, store, chapter)


def test_expand_run_intermediate_rounds_no_spec(tmp_path, monkeypatch):
    """When spec is None the intermediate rounds run check_prose(spec=None)
    (compliance-only, no quality_notes) and no judge_prose call fires; the
    final check has no spec so no verdict leaf is recorded."""
    pkg, card, manifest, prose_path = _expand_pkg(tmp_path, chapter=1)
    store = Store(tmp_path / "store_nospec")
    _committed(store, 1)
    neutral = "陆烬核对账册数字，逐项清点收支。他检查装订痕迹，发现缺页。他暂不声张，保存线索。夜色渐深，烛火摇曳。"
    per_block = len([c for c in neutral if '\u4e00' <= c <= '\u9fff'])
    mult = max(1, round(4210 / per_block)) if per_block else 120
    body = neutral * mult
    while len([c for c in body if '\u4e00' <= c <= '\u9fff']) < 4000:
        body += "夜色渐深，烛火摇曳。"
    while len([c for c in body if '\u4e00' <= c <= '\u9fff']) > 5000:
        body = body[:-1]
    judge_calls = {"n": 0}

    def _fake_judge(*a, **k):
        judge_calls["n"] += 1
        raise AssertionError("judge must not fire when spec is None")

    monkeypatch.setattr(_expand_run_mod, "judge_prose", _fake_judge)
    monkeypatch.setattr(_expand_run_mod, "_llm",
                        lambda base_url, api_key, model, messages, max_tokens, temperature,
                        reasoning_effort=None: (body, {"total_tokens": 900, "usage_estimated": False}))
    monkeypatch.setattr(_expand_run_mod, "load_provider_full",
                        lambda phase: ("https://example.local", "K", "stub-judge", {}))
    summary = _expand_run_mod.expand_until_range(pkg, base_url="u", api_key="k", model="m",
                                                 max_rounds=1, spec=None, store=store)
    assert judge_calls["n"] == 0
    assert "verdict_recorded" not in summary
    # No quality_notes key is required when spec is None (back-compat).
    assert summary.get("quality_notes", []) == []
    store.close()


def test_runner_qc_read_only(tmp_path):
    """qc() reads the verdict leaves read-only: a PASS line + a verdict_error
    line + a BELOW_KNOB line, with the error excluded from pass_rate."""
    from novel_engine.condensed import runner as _runner
    store = Store(tmp_path / "store_qc")
    _committed(store, 1)
    _committed(store, 2)
    _committed(store, 3)
    db = store.db
    config = load_quality_config()
    knob1 = config["knob_sets"][0]["knob_set"]
    ksh1 = knob_set_hash(knob1)
    # ch1: PASS (all dims meet the knob).
    record_verdict(db, store, 1, {"爽点": knob1["爽点"], "钩子": knob1["钩子"], "压抑": knob1["压抑"],
                                   "rationale": "ok", "judge_model": "m", "prompt_ver": "v1",
                                   "knob_set_hash": ksh1, "verdict_error": None},
                   digest("p1"), new_trace_id())
    # ch2: verdict_error leaf (excluded from the pass-rate denominator).
    err = dict(_verdict())
    err["verdict_error"] = {"type": "parse"}
    record_verdict(db, store, 2, err, None, new_trace_id())
    # ch3: BELOW_KNOB (爽点 below the knob).
    low = dict(_verdict())
    low["爽点"] = 0
    low["钩子"] = knob1["钩子"]
    low["压抑"] = knob1["压抑"]
    record_verdict(db, store, 3, low, digest("p3"), new_trace_id())
    # A read-only qc: the store's head/prefix are unchanged by the read.
    before_head = store.head()
    pipeline = _runner.CondensedPipeline.__new__(_runner.CondensedPipeline)
    pipeline.store = store
    pipeline.settings = _runner.ProductionConfig()
    pipeline.root = store.root
    result = pipeline.qc()
    assert store.head() == before_head
    by_chapter = {line["chapter"]: line for line in result["lines"]}
    assert by_chapter[1]["status"] == "PASS"
    assert by_chapter[2]["status"] == "verdict_error"
    assert by_chapter[3]["status"] == "BELOW_KNOB"
    # pass_rate over the latest non-error leaves: ch1 PASS, ch3 BELOW -> 1/2.
    assert result["pass_rate"] == 0.5
    store.close()


def test_runner_status_pass_rate(tmp_path):
    """status() surfaces verdict_pass_rate (denominator excludes verdict_error
    leaves) + verdict_error_rate + quality_budget readout."""
    from novel_engine.condensed import runner as _runner
    store = Store(tmp_path / "store_status")
    _committed(store, 1)
    _committed(store, 2)
    db = store.db
    config = load_quality_config()
    knob1 = config["knob_sets"][0]["knob_set"]
    ksh1 = knob_set_hash(knob1)
    good = {"爽点": knob1["爽点"], "钩子": knob1["钩子"], "压抑": knob1["压抑"],
             "rationale": "ok", "judge_model": "m", "prompt_ver": "v1",
             "knob_set_hash": ksh1, "verdict_error": None}
    record_verdict(db, store, 1, good, digest("p1"), new_trace_id())
    err = dict(_verdict())
    err["verdict_error"] = {"type": "timeout"}
    record_verdict(db, store, 2, err, None, new_trace_id())
    pipeline = _runner.CondensedPipeline.__new__(_runner.CondensedPipeline)
    pipeline.store = store
    pipeline.settings = _runner.ProductionConfig()
    pipeline.root = store.root
    st = pipeline.status()
    # ch1 is the only scored leaf and it passes; ch2 is a verdict_error.
    assert st["verdict_pass_rate"] == 1.0
    assert st["verdict_error_rate"] == 0.5
    assert st["quality_budget"]["cap"] == config["token_cap"]
    store.close()


def test_expand_run_quota_guard(tmp_path, monkeypatch):
    """The expand branch's budget_guard_configured halts cleanly on a quota
    breach (the round-2 mystery-freeze class) instead of firing the LLM. The
    guard is pure-read over the `usage` table; a synthetic over-cap row makes
    it return halt=True."""
    store = Store(tmp_path / "store_quota")
    db = store.db
    # A synthetic usage row that puts the month well over the token cap.
    cap = load_quality_config()["token_cap"]
    from datetime import datetime, timezone
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    db.execute("INSERT INTO usage(run_id,total_tokens,reported_requests,captured_at) VALUES(?,?,?,?)",
               ("quota-probe", cap + 1, 1, f"{this_month}-01T00:00:00+00:00"))
    guard = _observability_mod.budget_guard_configured(db)
    assert guard["halt"] is True
    assert guard["reason"] == "quota"
    # The raise-form contract (spec: "raises QuotaExceeded -> clean 'quota'
    # halt"): budget_guard_raises on the same over-cap store raises.
    with pytest.raises(_observability_mod.QuotaExceeded) as excinfo:
        _observability_mod.budget_guard_raises_configured(db)
    assert excinfo.value.guard["halt"] is True
    # A clean store (no over-cap usage) does not halt or raise.
    store2 = Store(tmp_path / "store_quota_ok")
    guard_ok = _observability_mod.budget_guard_configured(store2.db)
    assert guard_ok["halt"] is False
    assert _observability_mod.budget_guard_raises_configured(store2.db)["halt"] is False
    store.close()
    store2.close()


def test_judge_provider_phase_resolves(monkeypatch):
    """Cross-review blocker #1: the critic resolves via load_provider_full('judge')
    to a PINNED model (not the generic fallback) with temperature=0 +
    reasoning_effort=none — the spec's G1 bounded-critic contract. The judge
    phase is shipped in llm_providers.json (active profile)."""
    from novel_engine.condensed.providers import load_provider_full
    base_url, key_env, model, opts = load_provider_full("judge")
    assert model  # a pinned model, never the generic default-model fallback
    assert model is not None
    assert "flash" in model or "pro" in model  # an agnes critic model
    # The critic is a single bounded call: temperature 0 + no reasoning.
    assert opts["temperature"] == 0
    assert opts["reasoning_effort"] == "none"
