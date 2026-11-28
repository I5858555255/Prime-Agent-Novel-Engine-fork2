"""M2 drift metric: rolling judge-human agreement + bounded suspect events.

This is the OQ-gate foundation for drift (M2 roadmap M2-6). It is a
deterministic, no-network, no-LLM metric: it reuses
`judge_calibration.score_agreement` on the most recent `window` chapters and
emits a `drift_window` readout. It records a suspect ONLY when the reference
is ready (a gold set exists) AND the agreement actually dropped below the
threshold — an uncalibrated / no-reference state is an EXPLICIT no-op
(`suspect_reason='no_reference'`), never fabricated into a suspect, because
"not-ready" is not "drift".

The paging/routing channel is OQ4 and is deliberately NOT chosen here: this
module only produces the metric + a telemetry suspect row; `record_drift_suspect`
returns the row id (the `emit` hook) so OQ4 routing can be layered on later.
"""
from __future__ import annotations

import datetime as _dt
import json

from . import judge_calibration
from . import observability
from .quality_store import new_trace_id

# Below this agreement (0..1 scale, where 1.0 = perfect judge-human match) the
# rolling window is a drift suspect — provided the reference is actually ready.
LOW_AGREEMENT_THRESHOLD = 0.5


def _shared_chapters(gold: list[dict], verdicts: list[dict]) -> list[int]:
    """The chapter numbers present in BOTH gold and verdicts, ascending.

    A chapter only counts toward the agreement when it has a human reference
    (in `gold`) AND a judge verdict (in `verdicts`). Duplicate entries in the
    same list are tolerated (deduped by chapter)."""
    gold_ch = {g.get("chapter") for g in gold if isinstance(g.get("chapter"), int)}
    verdict_ch = {v.get("chapter") for v in verdicts if isinstance(v.get("chapter"), int)}
    return sorted(ch for ch in (gold_ch & verdict_ch) if ch is not None)


def _most_common_knob_set_hash(verdicts: list[dict], chapters: set[int]) -> str | None:
    """The most common knob_set_hash among the verdict records for `chapters`.

    Deterministic tie-break: on a tie, the lexicographically smallest hash wins
    (so the result is reproducible regardless of input order). None when no
    shared verdict carries a knob_set_hash (or no shared verdicts)."""
    counts: dict[str, int] = {}
    for v in verdicts:
        if not isinstance(v, dict):
            continue
        if v.get("chapter") not in chapters:
            continue
        ksh = v.get("knob_set_hash")
        if isinstance(ksh, str) and ksh:
            counts[ksh] = counts.get(ksh, 0) + 1
    if not counts:
        return None
    # Highest count first; ties broken by the smallest hash for determinism.
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def rolling_agreement(gold: list[dict], verdicts: list[dict], window: int = 10,
                     min_n: int = 30) -> dict:
    """Judge-human agreement over the most recent `window` overlapping chapters.

    Returns a `drift_window` dict:
    {prompt_ver, judge_model, knob_set_hash, window_chapters:list[int],
     agreement:float, n:int, suspect:bool, suspect_reason:str|None}

    - Slices to the LAST `window` chapters of the gold ∩ verdicts overlap (by
      chapter number), then delegates to `judge_calibration.score_agreement`
      on that slice.
    - `agreement` = the slice's `overall_agreement`; `n` = its `n`;
      `prompt_ver`/`judge_model` = its values; `knob_set_hash` = the most
      common knob_set_hash among the shared verdicts (None when none).
    - `suspect`: False + reason='no_reference' when the reference is NOT ready
      (score_agreement `ready` is False — no gold / empty gold / no overlap) —
      an EXPLICIT no-op, NOT a suspect (never fabricate a reference, never
      report "not-ready" as "drift"). When not ready, `agreement` is reported
      as 0.0 (there is no usable agreement to report — never a vacuous 1.0
      from an empty-MAE degenerate). Otherwise suspect =
      (n >= min_n) AND (agreement < LOW_AGREEMENT_THRESHOLD);
      suspect_reason = 'low_agreement' when suspect else None.
    - `window` must be a positive int; `window <= 0` raises `ValueError`
      (a caller bug, not a no-op — the metric measures "the most recent
      `window` chapters", which needs a positive window).
    - Fully deterministic (input-order independent).
    """
    shared = _shared_chapters(gold, verdicts)
    if window <= 0:
        # A non-positive window is a caller bug, not a no-op signal: the metric
        # is "agreement over the most recent `window` chapters", which needs a
        # positive window. Fail loudly rather than silently returning empty.
        raise ValueError("rolling_agreement: window must be a positive int")
    # Last `window` chapters (by ascending chapter number).
    window_chapters = shared[-window:]
    window_set = set(window_chapters)

    # Slice the inputs to the window so score_agreement measures ONLY the
    # most recent `window` overlapping chapters.
    sliced_gold = [g for g in gold if isinstance(g, dict) and g.get("chapter") in window_set]
    sliced_verdicts = [v for v in verdicts if isinstance(v, dict) and v.get("chapter") in window_set]

    agreement_result = judge_calibration.score_agreement(sliced_gold, sliced_verdicts, min_n=min_n)
    agreement = float(agreement_result.get("overall_agreement", 0.0) or 0.0)
    n = int(agreement_result.get("n", 0) or 0)
    prompt_ver = agreement_result.get("prompt_ver")
    judge_model = agreement_result.get("judge_model")
    # The window's verdicts carry the most representative knob_set_hash.
    knob_set_hash = _most_common_knob_set_hash(sliced_verdicts, window_set)

    if not agreement_result.get("ready", False):
        # Reference not ready (no gold / insufficient overlap / n < min_n):
        # an explicit no-op, NOT a suspect.
        agreement = 0.0
        suspect = False
        suspect_reason: str | None = "no_reference"
    else:
        suspect = (n >= min_n) and (agreement < LOW_AGREEMENT_THRESHOLD)
        suspect_reason = "low_agreement" if suspect else None

    return {
        "prompt_ver": prompt_ver,
        "judge_model": judge_model,
        "knob_set_hash": knob_set_hash,
        "window_chapters": window_chapters,
        "agreement": agreement,
        "n": n,
        "suspect": suspect,
        "suspect_reason": suspect_reason,
    }


def record_drift_suspect(db, drift_window: dict) -> int | None:
    """Write ONE telemetry suspect row for a drift window, when it is suspect.

    When `drift_window["suspect"]` is True: insert a `telemetry` row
    (kind='drift_suspect', tokens=0, model=judge_model, prompt_ver, payload=
    the drift_window as compact JSON, a fresh trace_id) and return the new row's
    `lastrowid`. This is the OQ4-free suspect record: it only PERSISTS the
    suspect; the paging channel is a later OQ4 routing concern, so the row id
    is the `emit` hook (a caller routes it where OQ4 lands).

    When `drift_window["suspect"]` is False (including a 'no_reference'
    window): return None and write NOTHING — not-ready is not a suspect.

    Reuses `observability._ensure_telemetry` for the table (no re-define)."""
    dw = dict(drift_window or {})
    if not dw.get("suspect"):
        # 'no_reference' (and any non-suspect window) is an explicit no-op:
        # do not write a suspect row.
        return None

    observability._ensure_telemetry(db)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    payload = json.dumps(dw, ensure_ascii=False, sort_keys=True)
    run_id = "drift-suspect-" + new_trace_id()
    with db:
        cur = db.execute(
            "INSERT INTO telemetry(trace_id, run_id, kind, tokens, model, prompt_ver, payload, captured_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (new_trace_id(), run_id, "drift_suspect", 0, dw.get("judge_model"),
             dw.get("prompt_ver"), payload, now))
        return cur.lastrowid
