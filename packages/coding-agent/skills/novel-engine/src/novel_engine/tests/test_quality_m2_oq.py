# -*- coding: utf-8 -*-
"""M2 OQ-gate foundations TDD tests: M2-5 judge_calibration + M2-4 retention_ingest
+ M2-6 drift. These are the OQ-gated no-op foundations: each is a deterministic,
no-network, no-LLM meter that is an EXPLICIT no-op when its external input is
absent (OQ1 retention feed, OQ2 human gold set, OQ4 alarm channel). They never
fabricate a reference / feed / channel.

Synthetic fixtures + tmp_path + real Store (the shipped config ships
enable_quality=true, so the quality-gated tables exist). NO real LLM.
Probe-dir pattern is identical to test_quality_m2.py.
"""
import json
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from novel_engine.condensed.contracts import REVIEW_CHECKS, digest
from novel_engine.condensed.judge_calibration import score_agreement
from novel_engine.condensed.retention_ingest import ingest_retention
from novel_engine.condensed.drift import rolling_agreement, record_drift_suspect
from novel_engine.condensed.store import Store

SOURCE = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# probe-dir fixture (identical pattern to test_quality_m2.py / test_quality_layer.py)
# ---------------------------------------------------------------------------
_QUALITY_PROBE_DIR = None
try:
    _probe_base = SOURCE / ".test_tmp"
    _probe_base.mkdir(exist_ok=True)
    _QUALITY_PROBE_DIR = Path(tempfile.mkdtemp(prefix="quality_m2_oq_session_", dir=_probe_base))
except Exception:
    pass


@pytest.fixture(scope="session")
def quality_probe_dir():
    candidate = _QUALITY_PROBE_DIR
    if candidate is None or not candidate.exists():
        base = SOURCE / ".test_tmp"
        base.mkdir(exist_ok=True)
        candidate = Path(tempfile.mkdtemp(prefix="quality_m2_oq_session_", dir=base))
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
            candidate = Path(tempfile.mkdtemp(prefix=f"quality_m2_oq_session_try{_attempt}_", dir=base))
    pytest.skip(f"sqlite tmp dir unavailable in this sandbox (last probe error: {last_exc}); "
                "quality-store tests require a sqlite-writable tmp path")


@pytest.fixture
def tmp_path(quality_probe_dir):
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


# --- record builders for the OQ-gate tests --------------------------------
def _verdicts(chapters, scores=None, ksh="k", prompt_ver="v1", judge_model="m"):
    scores = scores or {"爽点": 4, "钩子": 4, "压抑": 3}
    return [{"chapter": c, "scores": dict(scores), "prompt_ver": prompt_ver,
             "judge_model": judge_model, "knob_set_hash": ksh} for c in chapters]


def _gold(chapters, scores=None, rubric="r"):
    scores = scores or {"爽点": 4, "钩子": 4, "压抑": 3}
    return [{"chapter": c, "scores": dict(scores), "rubric": rubric} for c in chapters]


def _feed_record(chapter, rt=0.9, rr=0.2, ar=0.1, source="analytics", captured_at="t0"):
    return {"chapter": chapter, "read_through": rt, "reread": rr, "abandon_rate": ar,
            "source": source, "captured_at": captured_at}


# === M2-5 judge_calibration ===
def test_score_agreement_no_reference_is_noop():
    """empty gold -> n=0, ready=False, all mae 0.0, all corr None. An EXPLICIT
    no-op that never fabricates a reference and never raises (OQ2 not-ready)."""
    r = score_agreement([], _verdicts([1, 2, 3]))
    assert r["n"] == 0
    assert r["ready"] is False
    assert r["per_dim_mae"] == {"爽点": 0.0, "钩子": 0.0, "压抑": 0.0}
    assert r["per_dim_corr"] == {"爽点": None, "钩子": None, "压抑": None}
    # 1 - 0.0/5 == 1.0: a literal, degenerate-but-harmless value; `ready` carries
    # the "not actually calibrated" signal, so a downstream gate must key on it.
    assert r["overall_agreement"] == 1.0
    # No overlap (verdict chapters absent from gold) is ALSO a no-op.
    r2 = score_agreement(_gold([1]), _verdicts([2]))
    assert r2["n"] == 0 and r2["ready"] is False


def test_score_agreement_perfect_is_one():
    """A well-formed gold/verdict pair with identical scores -> perfect agreement
    (each dim MAE 0.0, overall 1.0) and a ready state only when n >= min_n."""
    gold = _gold(range(1, 40))
    verdicts = _verdicts(range(1, 40))
    r = score_agreement(gold, verdicts, min_n=30)
    assert r["n"] == 39
    assert r["ready"] is True
    assert r["per_dim_mae"] == {"爽点": 0.0, "钩子": 0.0, "压抑": 0.0}
    assert r["overall_agreement"] == 1.0
    # prompt_ver / judge_model surface the verdicts' uniform values.
    assert r["prompt_ver"] == "v1" and r["judge_model"] == "m"


def test_score_agreement_mae_and_readiness(tmp_path):
    """MAE drives overall_agreement = 1 - mean(per_dim_mae)/5, and `ready` is
    (n >= min_n): 20 well-formed overlaps are NOT ready at the default min_n=30."""
    verdicts = [{"chapter": c, "scores": {"爽点": 5, "钩子": 5, "压抑": 5}} for c in range(1, 21)]
    gold = [{"chapter": c, "scores": {"爽点": 5, "钩子": 5, "压抑": 3}} for c in range(1, 21)]
    r = score_agreement(gold, verdicts, min_n=30)
    assert r["n"] == 20 and r["ready"] is False
    # 压抑 differs by 2 every chapter -> per-dim MAE is 2.0; the other two match.
    assert r["per_dim_mae"]["压抑"] == pytest.approx(2.0)
    assert r["per_dim_mae"]["爽点"] == 0.0 and r["per_dim_mae"]["钩子"] == 0.0
    # overall_agreement = 1 - mean(per_dim_mae)/5 = 1 - ((0+0+2)/3)/5 = 0.8667.
    assert r["overall_agreement"] == pytest.approx(1 - (2.0 / 3) / 5)
    # Lower the gate and the same pair is ready.
    assert score_agreement(gold, verdicts, min_n=20)["ready"] is True


def test_score_agreement_malformed_records_excluded():
    """A malformed record (missing dim / out-of-range / non-int / bool score) is
    excluded, not poisoned into the MAE; it never raises."""
    verdicts = [
        {"chapter": 1, "scores": {"爽点": 4, "钩子": 4, "压抑": 3}},
        {"chapter": 2, "scores": {"爽点": 4, "钩子": 4}},                 # dim missing
        {"chapter": 3, "scores": {"爽点": 9, "钩子": 4, "压抑": 3}},        # out of range
        {"chapter": 4, "scores": {"爽点": True, "钩子": 4, "压抑": 3}},     # bool
    ]
    gold = [{"chapter": 1, "scores": {"爽点": 4, "钩子": 4, "压抑": 3}}]
    r = score_agreement(gold, verdicts, min_n=1)
    assert r["n"] == 1 and r["ready"] is True
    assert r["per_dim_mae"] == {"爽点": 0.0, "钩子": 0.0, "压抑": 0.0}


# === M2-4 retention_ingest ===
def test_ingest_retention_upsert(tmp_path):
    """A non-empty feed UPSERTs into the retention table: latest feed for the same
    chapter overwrites the earlier one, and the row count is the number of distinct
    chapters written."""
    store = Store(tmp_path / "ret_store")
    db = store.db
    n = ingest_retention(db, [_feed_record(1, rt=0.9), _feed_record(2, rt=0.5)], source="run1")
    assert n == 2
    # A later feed for chapter 1 wins on upsert.
    ingest_retention(db, [_feed_record(1, rt=0.2, rr=0.7, source="run2")], source="run2")
    row = db.execute("SELECT read_through, reread, source FROM retention WHERE chapter=1").fetchone()
    assert row == (0.2, 0.7, "run2")
    assert db.execute("SELECT COUNT(*) FROM retention").fetchone()[0] == 2
    store.close()


def test_ingest_retention_empty_is_noop(tmp_path):
    """An empty feed writes nothing and returns 0 (the OQ1 'no feed' signal); it
    never fabricates retention rows."""
    store = Store(tmp_path / "ret_noop_store")
    db = store.db
    assert ingest_retention(db, [], source="run0") == 0
    assert db.execute("SELECT COUNT(*) FROM retention").fetchone()[0] == 0
    store.close()


def test_ingest_retention_validates(tmp_path):
    """Type/range discipline: chapter must be int (not bool), the three rates must
    be float in [0,1] (not bool/int), source/captured_at must be str."""
    store = Store(tmp_path / "ret_bad_store")
    db = store.db
    with pytest.raises(ValueError, match="chapter"):
        ingest_retention(db, [_feed_record(True)], source="r")
    with pytest.raises(ValueError, match="read_through"):
        ingest_retention(db, [_feed_record(1, rt=1.5)], source="r")
    with pytest.raises(ValueError, match="source"):
        ingest_retention(db, [{"chapter": 1, "read_through": 0.5, "reread": 0.5,
                              "abandon_rate": 0.5, "source": 5, "captured_at": "t"}], source="r")
    # str/float chapters are rejected too (chapter must be a true int).
    with pytest.raises(ValueError, match="chapter"):
        ingest_retention(db, [_feed_record("1")], source="r")
    with pytest.raises(ValueError, match="chapter"):
        ingest_retention(db, [_feed_record(1.0)], source="r")
    store.close()


def test_ingest_retention_disabled_layer_raises(tmp_path, monkeypatch):
    """When the quality layer is disabled (no retention table), ingest raises
    RuntimeError('quality layer not enabled') — it never writes to a missing table."""
    # Build a bare 3-table DB + a disabled quality config to mirror
    # test_commit_override_disabled_layer in test_quality_m2.py.
    cfg_dir = tmp_path / "disabled_cfg"
    cfg_dir.mkdir()
    (cfg_dir / "quality_config.json").write_text(json.dumps({"enable_quality": False}), encoding="utf-8")
    # A DB that lacks the retention table: open sqlite directly, not via Store.
    db = sqlite3.connect(tmp_path / "disabled.sqlite3")
    db.executescript("""
        CREATE TABLE metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE chapters(number INTEGER PRIMARY KEY, payload TEXT NOT NULL,
                             hash TEXT NOT NULL, parent_hash TEXT NOT NULL,
                             review TEXT NOT NULL, story_day INTEGER NOT NULL);
        CREATE TABLE facts(entity TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                          chapter INTEGER NOT NULL, PRIMARY KEY(entity,key));
    """)
    with pytest.raises(RuntimeError, match="quality layer not enabled"):
        ingest_retention(db, [_feed_record(1)], source="r")
    db.close()


# === M2-6 drift ===
def test_rolling_agreement_no_reference_is_noop():
    """empty gold -> suspect=False, suspect_reason='no_reference', agreement 0.0.
    An explicit no-op: NOT-READY is not DRIFT; never fabricate a reference."""
    r = rolling_agreement([], _verdicts([1, 2, 3]))
    assert r["suspect"] is False
    assert r["suspect_reason"] == "no_reference"
    assert r["agreement"] == 0.0
    assert r["n"] == 0
    assert r["window_chapters"] == []


def test_rolling_agreement_window_slices():
    """15 overlapping chapters, window=10 -> window_chapters is the LAST 10 by
    chapter number (6..15); agreement is computed over that slice only."""
    v = _verdicts(range(1, 16))
    g = _gold(range(1, 16))
    r = rolling_agreement(g, v, window=10, min_n=30)
    assert r["window_chapters"] == list(range(6, 16))
    assert r["n"] == 10
    # n=10 < min_n=30 -> not ready -> the slice is treated as a no-op reference.
    assert r["suspect"] is False
    assert r["suspect_reason"] == "no_reference"
    assert r["agreement"] == 0.0
    assert r["knob_set_hash"] == "k"
    assert r["judge_model"] == "m" and r["prompt_ver"] == "v1"


def test_rolling_agreement_low_agreement_suspect():
    """A gold/verdict pair with a deliberately low agreement (<0.5) and n>=min ->
    suspect=True, suspect_reason='low_agreement' (the only real suspect path)."""
    v = _verdicts(range(1, 31), scores={"爽点": 0, "钩子": 0, "压抑": 0})
    g = _gold(range(1, 31), scores={"爽点": 5, "钩子": 5, "压抑": 5})
    r = rolling_agreement(g, v, window=30, min_n=30)
    assert r["n"] == 30
    assert r["agreement"] < 0.5
    assert r["suspect"] is True
    assert r["suspect_reason"] == "low_agreement"


def test_rolling_agreement_rejects_non_positive_window():
    """window <= 0 is a caller bug, not a no-op: rolling_agreement raises
    ValueError rather than silently returning an empty window (OQ-gate
    hardening)."""
    with pytest.raises(ValueError, match="window"):
        rolling_agreement(_gold([1]), _verdicts([1]), window=0)
    with pytest.raises(ValueError, match="window"):
        rolling_agreement(_gold([1]), _verdicts([1]), window=-1)


def test_rolling_agreement_deterministic_input_order():
    """The drift_window is independent of input order; the knob_set_hash tie-break
    is the lexicographically smallest on a tie."""
    v = _verdicts(range(1, 31), scores={"爽点": 0, "钩子": 0, "压抑": 0}, ksh="k")
    g = _gold(range(1, 31), scores={"爽点": 5, "钩子": 5, "压抑": 5})
    fwd = rolling_agreement(g, v, window=30, min_n=30)
    bwd = rolling_agreement(list(reversed(g)), list(reversed(v)), window=30, min_n=30)
    assert fwd == bwd
    assert fwd["knob_set_hash"] == "k"


def test_record_drift_suspect_writes_when_suspect(tmp_path):
    """A suspect drift_window -> exactly one telemetry row (kind='drift_suspect',
    tokens=0, model/prompt_ver from the window, payload carries the window); the
    function returns the new row's id (the OQ4-free `emit` hook)."""
    from novel_engine.condensed import observability as _obs
    store = Store(tmp_path / "drift_store")
    db = store.db
    _obs._ensure_telemetry(db)  # lazily-created table; ensure it before counting
    dw = {"prompt_ver": "v1", "judge_model": "m", "knob_set_hash": "k",
          "window_chapters": [1, 2], "agreement": 0.2, "n": 2,
          "suspect": True, "suspect_reason": "low_agreement"}
    before = db.execute("SELECT COUNT(*) FROM telemetry WHERE kind='drift_suspect'").fetchone()[0]
    row_id = record_drift_suspect(db, dw)
    assert row_id is not None
    assert db.execute("SELECT COUNT(*) FROM telemetry WHERE kind='drift_suspect'").fetchone()[0] == before + 1
    row = db.execute(
        "SELECT trace_id, kind, tokens, model, prompt_ver, payload FROM telemetry WHERE id=?",
        (row_id,)).fetchone()
    assert row[1] == "drift_suspect" and row[2] == 0 and row[3] == "m" and row[4] == "v1"
    payload = json.loads(row[5])
    assert payload["suspect"] is True and payload["suspect_reason"] == "low_agreement"
    assert payload["window_chapters"] == [1, 2] and payload["agreement"] == 0.2
    assert row[0]  # an opaque trace_id is written (never PII)
    store.close()


def test_record_drift_suspect_noop_when_no_reference(tmp_path):
    """A no_reference drift_window -> returns None and writes NOTHING (not-ready
    is not a suspect; we do not fabricate a reference). A ready-but-above-
    threshold (suspect=False, reason=None) window is also a no-op."""
    from novel_engine.condensed import observability as _obs
    store = Store(tmp_path / "drift_noop_store")
    db = store.db
    _obs._ensure_telemetry(db)  # lazily-created table; ensure it before counting
    dw = {"prompt_ver": "v1", "judge_model": "m", "knob_set_hash": None,
          "window_chapters": [], "agreement": 0.0, "n": 0,
          "suspect": False, "suspect_reason": "no_reference"}
    assert record_drift_suspect(db, dw) is None
    assert db.execute("SELECT COUNT(*) FROM telemetry WHERE kind='drift_suspect'").fetchone()[0] == 0
    dw_ok = {"prompt_ver": "v1", "judge_model": "m", "knob_set_hash": "k",
             "window_chapters": [1, 2], "agreement": 0.9, "n": 30,
             "suspect": False, "suspect_reason": None}
    assert record_drift_suspect(db, dw_ok) is None
    assert db.execute("SELECT COUNT(*) FROM telemetry WHERE kind='drift_suspect'").fetchone()[0] == 0
    store.close()
