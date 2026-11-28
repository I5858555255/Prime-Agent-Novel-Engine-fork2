# -*- coding: utf-8 -*-
"""Offline 100-chapter viral-quality closed-loop trial run (no LLM, no network).

Purpose: verify that the M2 OQ minimal closed loop (items 1+2) runs end-to-end
over a 100-chapter book, entirely offline. It:

  1. creates a fresh Store in a local dir,
  2. commits 100 DETERMINISTIC condensed chapters (the same _plan/_draft/_review
     pattern the quality tests use — no LLM calls, no network),
  3. mints a DETERMINISTIC verdict per chapter: the active per-stage knob set
     (volume_knobs.select_knobs) as the judge's target, plus a seeded per-chapter
     variation so the judge–human agreement is meaningful (not all-perfect),
  4. records each verdict into the quality_verdicts hash chain (record_verdict),
  5. loads the offline OQ stand-ins (gold_set_20.json / retention_feed_20.json /
     arc_constraints.json) and runs run_viral_loop over the whole book,
  6. writes a readable report (JSON + a printed summary) of agreement, drift
     suspects, arc violations, retention ingestion, and the per-chapter verdict
     pass-rate.

This exercises the FULL closed loop offline: quality measurement (judge–human
agreement + drift) + plot-direction control (story_arc) + retention ingest +
OQ4 alarm emit — with no external platform and no LLM.

Run:  python -m novel_engine.condensed.resources.viral_loop_trial --chapters 100
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import tempfile
from pathlib import Path

from novel_engine.condensed.contracts import REVIEW_CHECKS, digest
from novel_engine.condensed.store import Store
from novel_engine.condensed import quality_store, volume_knobs, observability
from novel_engine.condensed.gold_set import load_gold_set
from novel_engine.condensed.retention_feed import load_retention_feed
from novel_engine.condensed.story_arc import load_arc
from novel_engine.condensed.alarm_log import AlarmLog
from novel_engine.condensed.viral_loop import run_viral_loop

SOURCE = Path(__file__).resolve().parents[2]   # .../src/novel_engine (condensed/resources -> condensed -> novel_engine)
CONFIG = SOURCE / "config"


def _plan(chapter: int, target: int = 4210) -> dict:
    return {"chapter": chapter, "objective": "发现账目缺页并保留证据", "cast": ["陆烬"],
            "events": [{"id": f"E{i}", "action": "核对账目", "cause": "收支不符", "outcome": "取得线索"} for i in range(1, 4)],
            "ending": "保存账册准备次日询问", "boundaries": ["不能提前确定作案者"], "expanded_han": target}


def _draft(chapter: int, target: int = 4210, before=None) -> dict:
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


def _review(payload: dict) -> dict:
    result = {"input_hash": "input", "checks": dict.fromkeys(REVIEW_CHECKS, True), "issues": []}
    result["chapter_hash"] = digest(payload)
    return result


def _commit_chapter(store: Store, chapter: int) -> dict:
    value = _draft(chapter, before=None if chapter == 1 else f"第{chapter - 1}页缺失")
    value.update(plan=_plan(chapter))
    store.commit(value, _review(value))
    return value


def _deterministic_verdict(chapter: int, knob_set: dict, seed: int) -> dict:
    """A DETERMINISTIC judge verdict for `chapter`.

    The judge's target is the active per-stage knob set (爽点/钩子/压抑 floors).
    A seeded per-chapter variation (stable across runs) makes the verdict NOT
    all-perfect, so the judge–human agreement against the gold set is a
    meaningful number. The verdict carries its judge metadata (judge_model,
    prompt_ver, knob_set_hash) so the hash chain binds it to the knob set.
    """
    rng = random.Random(f"{seed}:{chapter}")
    ksh = volume_knobs.knob_set_hash(knob_set)
    scores = {}
    for dim in ("爽点", "钩子", "压抑"):
        floor = int(knob_set.get(dim, 3))
        # Judge lands on or slightly above the floor; a 30% chance it dips one
        # below (simulating a weaker chapter) so agreement varies by chapter.
        dip = rng.random() < 0.3
        value = max(0, min(5, floor - 1 if dip else floor + (1 if rng.random() < 0.4 else 0)))
        scores[dim] = value
    # The v0 quality_judge verdict is the FLAT 3-dim dict (what record_verdict
    # stores and the runner's G6 read surface reads). Judge metadata is carried
    # so the hash chain binds the verdict to the knob set + model + prompt ver.
    return {"爽点": scores["爽点"], "钩子": scores["钩子"], "压抑": scores["压抑"],
            "rationale": "deterministic offline verdict",
            "judge_model": "deterministic-offline", "prompt_ver": "v1", "knob_set_hash": ksh}


def run_trial(chapters: int = 100, out_dir: Path | None = None, seed: int = 42,
              disagree: bool = False) -> dict:
    work = out_dir or Path(tempfile.mkdtemp(prefix="viral_loop_trial_"))
    work.mkdir(parents=True, exist_ok=True)
    store = Store(work / "trial_store")
    db = store.db
    observability._ensure_telemetry(db)

    # -- 1. Commit `chapters` deterministic condensed chapters offline. -------
    for ch in range(1, chapters + 1):
        _commit_chapter(store, ch)

    # -- 2. Mint + record a deterministic verdict per chapter. ----------------
    # read_active_knobs is the clean v0 read API: it derives the stage from the
    # volume's chapter_range (stage_for(end)) and returns the deterministic
    # knob set + rationale (no LLM). A 100-chapter volume spans stages 1->2.
    knob_set, _rationale = volume_knobs.read_active_knobs(
        {"chapter_range": [1, chapters], "climax_chapter": 90})
    flat_verdicts: list[dict] = []          # the v0 store shape (flat 3 dims)
    loop_verdicts: list[dict] = []          # the judge_calibration record shape (nested scores)
    for ch in range(1, chapters + 1):
        verdict = _deterministic_verdict(ch, knob_set, seed)
        prose_sha = hashlib.sha256(f"prose:{ch}".encode("utf-8")).hexdigest()
        quality_store.record_verdict(db, store, ch, verdict, prose_sha)
        flat_verdicts.append({"chapter": ch, **verdict})
        # judge_calibration.score_agreement consumes the NESTED-scores record
        # contract ({chapter, scores:{...}, prompt_ver, judge_model,
        # knob_set_hash}); the v0 store verdict is flat. Convert once here.
        loop_verdicts.append({
            "chapter": ch,
            "scores": {d: verdict[d] for d in ("爽点", "钩子", "压抑")},
            "prompt_ver": verdict["prompt_ver"],
            "judge_model": verdict["judge_model"],
            "knob_set_hash": verdict["knob_set_hash"],
        })

    # -- 3. Load the offline OQ stand-ins. ------------------------------------
    gold = load_gold_set(CONFIG / "gold_set_20.json")
    if disagree:
        # Force a low judge-human agreement: set the gold set's scores to 0
        # (the verdicts track the knob floors ~3-4, so an all-zero reference
        # disagrees strongly). This drives overall_agreement below the 0.5
        # drift threshold so the suspect + OQ4 alarm emit path fires end-to-end.
        gold = [
            {**g, "scores": {d: 0 for d in ("爽点", "钩子", "压抑")}}
            for g in gold
        ]
    feed = load_retention_feed(CONFIG / "retention_feed_20.json")
    arc = load_arc(CONFIG / "arc_constraints.json")
    alarm = AlarmLog(work / "trial_alarms.jsonl")

    # -- 4. Run the closed loop over the whole book. --------------------------
    # min_n lowered to 15 (the offline gold set is 20 chapters; ready when
    # gold ∩ verdicts >= 15). window = 20 for a rolling drift over recent chapters.
    result = run_viral_loop(gold=gold, verdicts=loop_verdicts, feed=feed,
                            retention_db=db, alarm=alarm, arc=arc,
                            window=20, min_n=15)

    # -- 5. Per-chapter verdict pass-rate (the runner's G6 read surface). -----
    pass_rate = _verdict_pass_rate(store, knob_set)

    report = {
        "chapters": chapters,
        "seed": seed,
        "gold_chapters": len(gold),
        "feed_chapters": len(feed),
        "verdicts": len(flat_verdicts),
        "agreement": result["agreement"],
        "drift_window": result["drift_window"],
        "ingested_retention": result["ingested_retention"],
        "suspect_emitted": result["suspect_emitted"],
        "arc": result["arc"],
        "ready": result["ready"],
        "verdict_pass_rate": pass_rate,
        "retention_rows": db.execute("SELECT COUNT(*) FROM retention").fetchone()[0],
        "telemetry_suspects": db.execute("SELECT COUNT(*) FROM telemetry WHERE kind='drift_suspect'").fetchone()[0],
        "alarms": alarm.read_all(),
    }
    report_path = work / "viral_loop_trial_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    store.close()
    report["_report_path"] = str(report_path)
    report["_work_dir"] = str(work)
    return report


def _verdict_pass_rate(store: Store, knob_set: dict) -> dict:
    """The runner's G6 read surface: verdict pass-rate over latest-version
    leaves, denominator excluding verdict_error leaves."""
    db = store.db
    leaves = db.execute(
        "SELECT chapter, version, verdict, knob_set_hash, verdict_error FROM quality_verdicts").fetchall()
    latest: dict[int, tuple] = {}
    for ch, ver, raw, ksh, verr in leaves:
        if ver >= latest.get(ch, (0,))[0]:
            latest[ch] = (ver, raw, ksh, verr)
    numer = denom = 0
    error = 0
    for ch, (ver, raw, ksh, verr) in latest.items():
        if verr:
            error += 1
            continue
        verdict = json.loads(raw)
        denom += 1
        if all(verdict.get(d) is not None and verdict.get(d) >= knob_set.get(d, 0) for d in ("爽点", "钩子", "压抑")):
            numer += 1
    return {"pass_rate": round(numer / denom, 4) if denom else None,
            "denominator": denom, "errors": error}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Offline 100-chapter viral-quality closed-loop trial run")
    parser.add_argument("--chapters", type=int, default=100)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--disagree", action="store_true",
                        help="force a low judge-human agreement (gold set scores the "
                             "opposite of the verdicts) so the drift suspect + OQ4 alarm "
                             "emit path fires end-to-end")
    args = parser.parse_args(argv)
    report = run_trial(chapters=args.chapters, out_dir=args.out, seed=args.seed,
                       disagree=args.disagree)

    print("=" * 60)
    print(f"Offline viral-loop trial: {report['chapters']} chapters (seed={report['seed']})")
    print("=" * 60)
    print(f"gold chapters (OQ2 stand-in):   {report['gold_chapters']}")
    print(f"retention feed (OQ1 stand-in):  {report['feed_chapters']} rows -> {report['retention_rows']} written")
    print(f"verdicts minted + recorded:     {report['verdicts']}")
    print(f"judge-human ready (n>=15):      {report['ready']}")
    ag = report["agreement"]
    print(f"overall agreement:              {ag.get('overall_agreement'):.4f}  (n={ag.get('n')})")
    dw = report["drift_window"]
    print(f"drift window:                   suspect={dw['suspect']} reason={dw['suspect_reason']} agreement={dw['agreement']:.4f}")
    print(f"retention ingested:             {report['ingested_retention']} rows")
    print(f"drift suspect emitted (OQ4):    {report['suspect_emitted']} (telemetry rows={report['telemetry_suspects']})")
    arc = report["arc"]
    print(f"story-arc:                      enforced={arc['enforced']}")
    print(f"  satisfied:                     {arc['satisfied']}")
    print(f"  violations ({len(arc['violations'])}):")
    for v in arc["violations"]:
        print(f"    - {v}")
    pr = report["verdict_pass_rate"]
    print(f"verdict pass-rate:              {pr['pass_rate']}  (denom={pr['denominator']}, errors={pr['errors']})")
    print(f"\nreport: {report['_report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
