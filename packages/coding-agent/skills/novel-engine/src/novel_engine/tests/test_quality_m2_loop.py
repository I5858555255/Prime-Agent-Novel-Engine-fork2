# -*- coding: utf-8 -*-
"""M2 OQ 最小闭环 TDD 测试：gold_set + retention_feed + alarm_log + story_arc
+ viral_loop（离线爆款可控闭环）。全部确定性、无网络、无 LLM；no-op 不变量守住
（缺 gold/feed/arc/suspect 都是显式 no-op，绝不臆造）。合成 fixture + tmp_path +
真实 Store（shipped config enable_quality=true，retention/telemetry 表自动建）。
Probe-dir 模式同 test_quality_m2.py / test_quality_m2_oq.py。
"""
import json
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest

from novel_engine.condensed.contracts import REVIEW_CHECKS, digest
from novel_engine.condensed.gold_set import load_gold_set
from novel_engine.condensed.retention_feed import load_retention_feed
from novel_engine.condensed.alarm_log import AlarmLog
from novel_engine.condensed.story_arc import load_arc, check_arc
from novel_engine.condensed.viral_loop import run_viral_loop
from novel_engine.condensed.store import Store

SOURCE = Path(__file__).resolve().parents[1]

# ---------------------------------------------------------------------------
# probe-dir fixture (identical pattern to test_quality_m2.py)
# ---------------------------------------------------------------------------
_QUALITY_PROBE_DIR = None
try:
    _probe_base = SOURCE / ".test_tmp"
    _probe_base.mkdir(exist_ok=True)
    _QUALITY_PROBE_DIR = Path(tempfile.mkdtemp(prefix="quality_m2_loop_session_", dir=_probe_base))
except Exception:
    pass


@pytest.fixture(scope="session")
def quality_probe_dir():
    candidate = _QUALITY_PROBE_DIR
    if candidate is None or not candidate.exists():
        base = SOURCE / ".test_tmp"
        base.mkdir(exist_ok=True)
        candidate = Path(tempfile.mkdtemp(prefix="quality_m2_loop_session_", dir=base))
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
            candidate = Path(tempfile.mkdtemp(prefix=f"quality_m2_loop_session_try{_attempt}_", dir=base))
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


def _verdicts(chapters, scores=None, ksh="k", prompt_ver="v1", judge_model="m"):
    scores = scores or {"爽点": 4, "钩子": 4, "压抑": 3}
    return [{"chapter": c, "scores": dict(scores), "prompt_ver": prompt_ver,
             "judge_model": judge_model, "knob_set_hash": ksh} for c in chapters]


def _gold(chapters, scores=None, rubric="r"):
    scores = scores or {"爽点": 4, "钩子": 4, "压抑": 3}
    return [{"chapter": c, "scores": dict(scores), "rubric": rubric} for c in chapters]


# === gold_set ===
def test_load_gold_set_reads_shipped_fixture():
    """The shipped 20-chapter gold_set_20.json loads as 20 well-formed records
    (chapter int, scores 3-dim int 0-5, rubric str)."""
    path = SOURCE / "config" / "gold_set_20.json"
    gold = load_gold_set(path)
    assert len(gold) == 20
    for rec in gold:
        assert isinstance(rec["chapter"], int) and not isinstance(rec["chapter"], bool)
        for dim in ("爽点", "钩子", "压抑"):
            assert isinstance(rec["scores"][dim], int) and 0 <= rec["scores"][dim] <= 5
        assert isinstance(rec.get("rubric"), str)


def test_load_gold_set_missing_is_noop(tmp_path):
    """A missing gold-set file → [] (EXPLICIT 'reference not ready', never a
    fabricated reference, never raises)."""
    assert load_gold_set(tmp_path / "does_not_exist.json") == []


def test_load_gold_set_drops_malformed(tmp_path):
    """Malformed records (bad chapter / out-of-range / non-int / bool score /
    non-list JSON) are DROPPED; a corrupt file degrades to [] (not a crash)."""
    p = tmp_path / "gold_mixed.json"
    p.write_text(json.dumps([
        {"chapter": 1, "scores": {"爽点": 4, "钩子": 4, "压抑": 3}},          # good
        {"chapter": 2, "scores": {"爽点": 4, "钩子": 4}},                     # dim missing
        {"chapter": 3, "scores": {"爽点": 9, "钩子": 4, "压抑": 3}},          # out of range
        {"chapter": 4, "scores": {"爽点": True, "钩子": 4, "压抑": 3}},        # bool
        {"chapter": 5, "scores": {"爽点": 4, "钩子": 4, "压抑": 3}},          # good
    ]), encoding="utf-8")
    gold = load_gold_set(p)
    assert [r["chapter"] for r in gold] == [1, 5]
    # Corrupt JSON → [] (OQ2-absent signal, not an error).
    p2 = tmp_path / "gold_corrupt.json"
    p2.write_text("{not valid json", encoding="utf-8")
    assert load_gold_set(p2) == []
    # Non-list JSON → [].
    p3 = tmp_path / "gold_obj.json"
    p3.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
    assert load_gold_set(p3) == []


# === retention_feed ===
def test_load_retention_feed_reads_shipped_fixture():
    """The shipped 20-chapter retention_feed_20.json loads as 20 well-formed
    records (float 0..1 rates, chapter int, source/captured_at str)."""
    path = SOURCE / "config" / "retention_feed_20.json"
    feed = load_retention_feed(path)
    assert len(feed) == 20
    for rec in feed:
        assert isinstance(rec["chapter"], int) and not isinstance(rec["chapter"], bool)
        for dim in ("read_through", "reread", "abandon_rate"):
            assert isinstance(rec[dim], float) and 0.0 <= rec[dim] <= 1.0
        assert isinstance(rec["source"], str) and isinstance(rec["captured_at"], str)


def test_load_retention_feed_missing_is_noop(tmp_path):
    """A missing feed file → [] (EXPLICIT 'no feed', never fabricated data)."""
    assert load_retention_feed(tmp_path / "no_feed.json") == []


def test_load_retention_feed_drops_malformed_and_dedups(tmp_path):
    """Bad records dropped; a later record for the same chapter overwrites the
    earlier one (UPSERT-on-chapter semantics), first-seen order kept."""
    p = tmp_path / "feed_mixed.json"
    p.write_text(json.dumps([
        {"chapter": 1, "read_through": 0.9, "reread": 0.2, "abandon_rate": 0.1,
         "source": "s", "captured_at": "t"},
        {"chapter": 1, "read_through": 0.3, "reread": 0.4, "abandon_rate": 0.5,
         "source": "s", "captured_at": "t2"},     # later wins for ch1
        {"chapter": 2, "read_through": 0.5, "reread": 0.5, "abandon_rate": 1.5,
         "source": "s", "captured_at": "t"},       # abandon out of range → drop
        {"chapter": 3, "read_through": True, "reread": 0.5, "abandon_rate": 0.5,
         "source": "s", "captured_at": "t"},       # bool rate → drop
        {"chapter": 4, "read_through": 1, "reread": 0.5, "abandon_rate": 0.5,
         "source": "s", "captured_at": "t"},       # int rate (not float) → drop
    ]), encoding="utf-8")
    feed = load_retention_feed(p)
    assert [r["chapter"] for r in feed] == [1]
    assert feed[0]["read_through"] == 0.3   # later value wins
    # Missing source/captured_at default to "" (an ingest-run label is separate).
    p2 = tmp_path / "feed_min.json"
    p2.write_text(json.dumps([{"chapter": 9, "read_through": 0.5, "reread": 0.5,
                               "abandon_rate": 0.5}]), encoding="utf-8")
    feed2 = load_retention_feed(p2)
    assert feed2[0]["source"] == "" and feed2[0]["captured_at"] == ""


# === alarm_log ===
def test_alarm_log_emits_jsonl(tmp_path):
    """AlarmLog.emit appends one JSONL line per call; read_all returns them.
    A fresh log (no emit) has no file → read_all is [] ('nothing to alarm').
    Passing captured_at makes the line fully deterministic (P1 fix)."""
    log_path = tmp_path / "alarms.jsonl"
    alarm = AlarmLog(log_path)
    assert alarm.read_all() == []          # no emit yet → empty, no file
    dw = {"prompt_ver": "v1", "judge_model": "m", "knob_set_hash": "k",
          "window_chapters": [1, 2], "agreement": 0.2, "n": 30,
          "suspect": True, "suspect_reason": "low_agreement"}
    alarm.emit(dw, telemetry_row_id=7, captured_at="2026-11-26T00:00:00Z")
    alarm.emit(dw, telemetry_row_id=8, captured_at="2026-11-26T00:00:01Z")
    lines = alarm.read_all()
    assert len(lines) == 2
    assert lines[0]["kind"] == "drift_suspect" and lines[0]["telemetry_row_id"] == 7
    assert lines[0]["drift_window"]["suspect"] is True
    assert lines[0]["ts"] == "2026-11-26T00:00:00Z"   # deterministic (injected)
    assert lines[1]["ts"] == "2026-11-26T00:00:01Z"
    assert lines[1]["telemetry_row_id"] == 8
    # Each line is valid JSON on its own line (JSONL).
    raw_lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(raw_lines) == 2
    for line in raw_lines:
        json.loads(line)


# === story_arc ===
def test_load_arc_reads_shipped_fixture():
    """The shipped arc_constraints.json loads into the 3 known constraint blocks
    (climax_at / suppression_release / hook_density) + rubric."""
    path = SOURCE / "config" / "arc_constraints.json"
    arc = load_arc(path)
    assert arc is not None
    assert arc["climax_at"]["chapters"] == [5, 10, 15, 20]
    assert arc["climax_at"]["min"] == 4
    assert arc["suppression_release"]["chapter"] == 7
    assert arc["hook_density"]["window"] == 5


def test_load_arc_missing_is_none(tmp_path):
    """A missing arc file → None (EXPLICIT 'no plot-direction constraint', not a
    fabricated arc, not a crash)."""
    assert load_arc(tmp_path / "no_arc.json") is None


def test_check_arc_no_arc_is_explicit_noop():
    """check_arc with arc=None → enforced=False, satisfied=['no_arc'] — a caller
    can tell 'no constraint' apart from 'constraint satisfied'."""
    r = check_arc(5, {"scores": {"爽点": 5, "钩子": 4, "压抑": 3}}, None, history=[])
    assert r == {"enforced": False, "violations": [], "satisfied": ["no_arc"]}


def test_check_arc_climax_violation():
    """A climax chapter whose 爽点 < min is a violation with a human-readable
    reason; 爽点 >= min satisfies it."""
    arc = load_arc(SOURCE / "config" / "arc_constraints.json")
    # Chapter 5 must have 爽点 >= 4.
    ok = check_arc(5, {"scores": {"爽点": 5, "钩子": 4, "压抑": 3}}, arc,
                   history=[{"chapter": c, "scores": {"爽点": 4, "钩子": 4, "压抑": 3}} for c in range(1, 5)])
    assert "climax_at" in ok["satisfied"]
    miss = check_arc(5, {"scores": {"爽点": 2, "钩子": 4, "压抑": 3}}, arc,
                     history=[{"chapter": c, "scores": {"爽点": 4, "钩子": 4, "压抑": 3}} for c in range(1, 5)])
    assert any("climax_at" in v for v in miss["violations"])


def test_check_arc_suppression_release():
    """The suppression chapter (压抑 >= min) + a release in [ch, release_by]
    (爽点 or 钩子 >= 4) must both hold; missing release is a violation."""
    arc = load_arc(SOURCE / "config" / "arc_constraints.json")
    # acc_ch=7 (压抑 must be >=4), release_by=9 (some ch in 7..9 must have 爽点/钩子>=4).
    good_hist = [{"chapter": 7, "scores": {"爽点": 2, "钩子": 3, "压抑": 4}},
                 {"chapter": 8, "scores": {"爽点": 5, "钩子": 3, "压抑": 3}}]  # ch8 releases
    good_cur = {"scores": {"爽点": 3, "钩子": 4, "压抑": 3}}
    r = check_arc(8, good_cur, arc, history=good_hist)
    assert "suppression_release" in r["satisfied"]
    # No release in the window → violation.
    bad_hist = [{"chapter": 7, "scores": {"爽点": 2, "钩子": 3, "压抑": 4}},
                {"chapter": 8, "scores": {"爽点": 2, "钩子": 3, "压抑": 3}}]
    r2 = check_arc(8, {"scores": {"爽点": 2, "钩子": 3, "压抑": 3}}, arc, history=bad_hist)
    assert any("suppression_release" in v for v in r2["violations"])


def test_check_arc_deterministic(tmp_path):
    """Same inputs → identical check_arc output (no randomness, no LLM)."""
    arc = load_arc(SOURCE / "config" / "arc_constraints.json")
    hist = [{"chapter": c, "scores": {"爽点": 4, "钩子": 4, "压抑": 3}} for c in range(1, 5)]
    cur = {"scores": {"爽点": 5, "钩子": 4, "压抑": 3}}
    a = check_arc(5, cur, arc, history=hist)
    b = check_arc(5, cur, arc, history=list(reversed(hist)))
    # history order must not matter (windowed checks are order-independent).
    assert a == b


# === viral_loop (closed-loop integration) ===
def test_viral_loop_full_offline_pass(tmp_path):
    """With a gold set + verdicts + a real Store db, the loop reports ready
    agreement, a non-suspect drift window, 20 ingested retention rows, no
    suspect emitted, and an arc check against the latest verdict."""
    store = Store(tmp_path / "loop_store")
    db = store.db
    gold = _gold(range(1, 40))
    verdicts = _verdicts(range(1, 40))
    feed = [{"chapter": c, "read_through": 0.9, "reread": 0.2, "abandon_rate": 0.1,
             "source": "s", "captured_at": "t"} for c in range(1, 21)]
    result = run_viral_loop(gold=gold, verdicts=verdicts, feed=feed,
                            retention_db=db, window=30, min_n=30)
    assert result["ready"] is True
    assert result["agreement"]["n"] == 39
    assert result["drift_window"]["suspect"] is False
    assert result["ingested_retention"] == 20
    assert db.execute("SELECT COUNT(*) FROM retention").fetchone()[0] == 20
    assert result["suspect_emitted"] is False
    # arc is None → enforced=False no-op.
    assert result["arc"] == {"enforced": False, "violations": [], "satisfied": ["no_arc"]}
    store.close()


def test_viral_loop_no_gold_is_noop(tmp_path):
    """Empty gold → ready=False, drift suspect_reason='no_reference', no
    suspect emitted, retention still ingested (independent of the reference)."""
    store = Store(tmp_path / "loop_nogold_store")
    db = store.db
    verdicts = _verdicts(range(1, 40))
    feed = [{"chapter": 1, "read_through": 0.9, "reread": 0.2, "abandon_rate": 0.1,
             "source": "s", "captured_at": "t"}]
    result = run_viral_loop(gold=[], verdicts=verdicts, feed=feed,
                            retention_db=db, window=30, min_n=30)
    assert result["ready"] is False
    assert result["drift_window"]["suspect_reason"] == "no_reference"
    assert result["suspect_emitted"] is False
    assert result["ingested_retention"] == 1
    store.close()


def test_viral_loop_low_agreement_emits_suspect(tmp_path):
    """A gold set that DISAGREES with the verdicts (ready n>=min_n, agreement
    < 0.5) → a real drift suspect: a telemetry row is recorded AND an alarm is
    emitted to the OQ4 channel. (record_drift_suspect ensures the telemetry
    table itself, so the test does not reach into a sibling's private DDL.)"""
    store = Store(tmp_path / "loop_suspect_store")
    db = store.db
    # Gold says 5/5/5, verdicts say 0/0/0 → big disagreement (MAE 5, agreement 0).
    gold = [{"chapter": c, "scores": {"爽点": 5, "钩子": 5, "压抑": 5}, "rubric": "r"} for c in range(1, 31)]
    verdicts = [{"chapter": c, "scores": {"爽点": 0, "钩子": 0, "压抑": 0},
                 "prompt_ver": "v1", "judge_model": "m", "knob_set_hash": "k"} for c in range(1, 31)]
    alarm = AlarmLog(tmp_path / "suspect_alarms.jsonl")
    result = run_viral_loop(gold=gold, verdicts=verdicts, feed=None,
                            retention_db=db, alarm=alarm, window=30, min_n=30,
                            )
    assert result["drift_window"]["suspect"] is True
    assert result["suspect_emitted"] is True
    assert db.execute("SELECT COUNT(*) FROM telemetry WHERE kind='drift_suspect'").fetchone()[0] == 1
    alarms = alarm.read_all()
    assert len(alarms) == 1 and alarms[0]["drift_window"]["suspect"] is True
    store.close()


def test_viral_loop_suspect_without_db_reports_but_does_not_emit():
    """The P3 gating case: a real low-agreement suspect with retention_db=None
    → drift_window.suspect is True (the caller sees it) but suspect_emitted is
    False (no store, no alarm; the loop never fabricates a telemetry row)."""
    gold = _gold(range(1, 31), scores={"爽点": 5, "钩子": 5, "压抑": 5})
    verdicts = _verdicts(range(1, 31), scores={"爽点": 0, "钩子": 0, "压抑": 0})
    result = run_viral_loop(gold=gold, verdicts=verdicts, feed=None,
                            retention_db=None, window=30, min_n=30)
    assert result["drift_window"]["suspect"] is True     # the suspect IS detected
    assert result["suspect_emitted"] is False            # …but not persisted/emitted (no db)
    assert result["ready"] is True


def test_viral_loop_no_verdicts_is_explicit_sentinel():
    """With empty verdicts the arc check is an EXPLICIT no_verdicts sentinel
    (enforced reflects whether an arc was supplied; never a fabricated
    chapter-0 verdict, never a silent pass)."""
    gold = _gold(range(1, 31))
    result = run_viral_loop(gold=gold, verdicts=[], feed=None,
                            retention_db=None, window=30, min_n=30,
                            arc={"climax_at": {"chapters": [5], "min": 4}})
    assert result["arc"]["satisfied"] == ["no_verdicts"]
    assert result["arc"]["violations"] == []
    # No usable verdict → agreement n=0, ready False.
    assert result["ready"] is False
    assert result["drift_window"]["suspect_reason"] == "no_reference"


def test_viral_loop_no_db_reports_would_be_feed():
    """With no retention_db, a feed is validated-but-not-persisted: the loop
    still reports the feed's row count (read-only) and does NOT crash or write.
    A suspect cannot be persisted/emitted without a db → suspect_emitted False
    (the drift_window still reports suspect=True so the caller sees it)."""
    gold = _gold(range(1, 31))
    verdicts = _verdicts(range(1, 31))
    feed = [{"chapter": c, "read_through": 0.9, "reread": 0.2, "abandon_rate": 0.1,
             "source": "s", "captured_at": "t"} for c in range(1, 21)]
    result = run_viral_loop(gold=gold, verdicts=verdicts, feed=feed,
                            retention_db=None, window=30, min_n=30)
    assert result["ingested_retention"] == 20   # validated count, not written
    assert result["suspect_emitted"] is False   # no db → no persist/emit
    assert result["ready"] is True
