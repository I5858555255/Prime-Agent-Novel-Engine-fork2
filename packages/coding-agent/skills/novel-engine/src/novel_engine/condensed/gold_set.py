"""M2 OQ-gate: OQ2 minimal gold-set loader (design §1 of the minimal closed loop).

The OQ2 gate's real input is a ~200-chapter human-annotated gold set with a
scoring rubric. That is not available offline, so this module loads the
**minimal offline stand-in**: a local JSON file of ~20 hand-scored chapters
(each: chapter number + a 0-5 int triple for 爽点/钩子/压抑 + the rubric
string). It is the reference set `judge_calibration.score_agreement` calibrates
against.

No-op discipline (mirrors the OQ-gate foundations): a missing or empty file
returns `[]` — an EXPLICIT "reference not ready" signal, never a fabricated
reference. Malformed records (missing/non-int/out-of-range scores, non-int
chapter) are DROPPED so a bad entry never poisons the MAE (same discipline as
`judge_calibration._valid_scores`).

Fully deterministic, no network, no LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

# The three viral-quality dimensions, matching quality_judge / judge_calibration /
# quality_store. Fixed order so validation is deterministic.
DIMS = ("爽点", "钩子", "压抑")


def _valid_gold_record(record: dict) -> dict | None:
    """Return a normalized gold record iff it is well-formed; None otherwise.

    A gold record is usable when `chapter` is a true int (not bool) and
    `scores` is a dict carrying all three dims as int 0-5 (bool rejected).
    The `rubric` field, when present, must be a str (carried through verbatim;
    its absence is tolerated — the rubric is documentation, not data)."""
    if not isinstance(record, dict):
        return None
    chapter = record.get("chapter")
    if isinstance(chapter, bool) or not isinstance(chapter, int):
        return None
    scores = record.get("scores")
    if not isinstance(scores, dict):
        return None
    for dim in DIMS:
        value = scores.get(dim)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if not 0 <= value <= 5:
            return None
    normalized = {"chapter": chapter, "scores": {dim: scores[dim] for dim in DIMS}}
    rubric = record.get("rubric")
    if rubric is not None:
        if not isinstance(rubric, str):
            return None
        normalized["rubric"] = rubric
    return normalized


def load_gold_set(path) -> list[dict]:
    """Load a minimal OQ2 gold set from a local JSON file.

    - The file is a JSON **list** of gold records (each as above).
    - Missing file / empty file / JSON that is not a list → `[]` (EXPLICIT no-op:
      "reference not ready", never a fabricated reference, never raises on the
      file being absent — that is the OQ2-absent signal).
    - Malformed records are DROPPED (a bad entry never poisons the MAE).
    - Deterministic: input-order independent, same file → same list.
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt / unreadable file is treated as "no reference", not an error:
        # the OQ2 gate must degrade to an explicit no-op, never crash the loop.
        return []
    if not isinstance(raw, list):
        return []
    gold: list[dict] = []
    seen: set[int] = set()
    for record in raw:
        normalized = _valid_gold_record(record)
        if normalized is None:
            continue
        # Dedup by chapter (a duplicate human score for one chapter is dropped,
        # first-seen wins, for determinism).
        if normalized["chapter"] in seen:
            continue
        seen.add(normalized["chapter"])
        gold.append(normalized)
    return gold
