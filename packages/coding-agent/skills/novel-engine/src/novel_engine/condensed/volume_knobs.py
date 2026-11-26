"""Deterministic per-volume knob selection (G3) + regime boundaries.

v0 = fixed per-stage knob_set read from quality_config.json: fully
deterministic, no LLM, rationale + provenance recorded. `regimes_for` returns
the concept-unlock chapter numbers strictly inside (start, end] from
concept_unlocks.json — the same source the forbidden-term layer reads — so a
knob set never silently spans two forbidden-term regimes. The rules engine +
LLM climax classification is M2.
"""
from __future__ import annotations

import json
from pathlib import Path

from .accept_check import STAGE1_MAX_CHAPTER
from .contracts import digest
from .concept_terms import _load_concepts, _DEFAULT_CONCEPT_PATH

_CONFIG = Path(__file__).resolve().parent.parent / "config" / "quality_config.json"


def load_quality_config() -> dict:
    """quality_config.json lives in the source config tree; a project-local
    override inside the output root wins when present (test injection point)."""
    for path in (Path.cwd() / "quality_config.json", _CONFIG):
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def stage_for(chapter: int) -> int:
    """Stage derived from the chapter number via STAGE1_MAX_CHAPTER (ch <= 316 -> 1)."""
    return 1 if chapter <= STAGE1_MAX_CHAPTER else 2


def knob_set_for_stage(stage: int) -> dict:
    for entry in load_quality_config()["knob_sets"]:
        if entry["stage"] == stage:
            return entry["knob_set"]
    raise ValueError(f"no knob_set defined for stage {stage} in quality_config.json")


def select_knobs(volume: dict, stage: int) -> tuple[dict, dict]:
    """Deterministic knob pick. Returns (knob_set, rationale).

    If quality_config.json carries a `knob_rules` block (M2-2 rules engine),
    the pick delegates to knob_rules.evaluate_knob_rules (bible field + volume
    theme + climax_chapter + stage, versioned rationale). Otherwise it is
    EXACTLY the v0 per-stage fixed knob_set + rationale {bible_field,
    climax_chapter, stage} — byte-identical when there is no `knob_rules`
    config (the regression guard). No LLM on either path.
    """
    config = load_quality_config()
    if "knob_rules" in config:
        # Lazy import: knob_rules imports volume_knobs (for the stage fallback),
        # so a top-level import here would be circular.
        from . import knob_rules
        return knob_rules.evaluate_knob_rules(volume, stage, config["knob_rules"])
    knob_set = dict(knob_set_for_stage(stage))
    rationale = {"bible_field": "world", "climax_chapter": volume.get("climax_chapter"), "stage": stage}
    return knob_set, rationale


def select_knobs_for_volume(volume: dict) -> list[tuple[dict, dict, list[int]]]:
    """Emit one knob set per forbidden-term regime the volume spans.

    Splits the volume's chapter_range on `regimes_for(volume["chapter_range"])`
    (the concept-unlock boundaries strictly inside (start, end]) and returns
    `len(regimes)+1` (knob_set, rationale, sub_range) tuples — one per regime
    segment, each using that segment's own stage (via STAGE1_MAX_CHAPTER).
    A volume that crosses a regime boundary therefore yields multiple knob
    sets instead of one, so a knob never applies across two regimes. A volume
    that does not cross a boundary yields exactly one tuple (back-compat).

    This is the regime-aware entry point; `select_knobs` (single stage) and
    `read_active_knobs` (the volume's last-chapter stage) remain the simpler
    read APIs for the non-straddling case.
    """
    start, end = volume["chapter_range"]
    boundaries = regimes_for(volume["chapter_range"])
    # Segments: the regime that begins at `start`, then one per boundary. A
    # boundary chapter `b` is the FIRST chapter of the new regime (it belongs to
    # the next segment, not the one before it), so each segment ends at
    # (next_boundary - 1).
    segments: list[list[int]] = []
    cursor = start
    for boundary in boundaries:
        if boundary - 1 >= cursor:
            segments.append([cursor, boundary - 1])
        cursor = boundary
    if end >= cursor:
        segments.append([cursor, end])
    out: list[tuple[dict, dict, list[int]]] = []
    for sub_start, sub_end in segments:
        # A segment represents the regime that *begins* at sub_start; its stage
        # is the stage of its first chapter (sub_start), not its endpoint.
        sub_volume = {**volume, "chapter_range": [sub_start, sub_end]}
        knob_set, rationale = select_knobs(sub_volume, stage_for(sub_start))
        out.append((knob_set, rationale, [sub_start, sub_end]))
    return out


def regime_boundaries(path=None) -> list[int]:
    """Sorted concept-unlock chapter numbers (deduped) from concept_unlocks.json —
    the same source concept_terms.forbidden_for_chapter reads."""
    concepts = _load_concepts(str(Path(path)) if path else str(_DEFAULT_CONCEPT_PATH))
    chapters = sorted({int(c["unlocked_at_chapter"]) for c in concepts
                       if isinstance(c.get("unlocked_at_chapter"), int) and c.get("unlocked_at_chapter") > 0})
    return chapters


def regimes_for(chapter_range: list[int]) -> list[int]:
    """Concept-unlock chapter numbers strictly inside (start, end].

    Examples: [600, 700] -> [653]; [300, 500] -> [317]; no boundary -> [].
    A volume straddling a boundary yields one knob set per regime
    (len(regimes)+1 sets), so a knob never applies across two regimes.
    """
    start, end = chapter_range[0], chapter_range[1]
    return [c for c in regime_boundaries() if start < c <= end]


def read_active_knobs(volume: dict) -> tuple[dict, dict]:
    """Read API used by the expand/dual-check layers: the v0 deterministic
    select_knobs result for the volume's stage (stage derived from the volume's
    own chapter_range via STAGE1_MAX_CHAPTER)."""
    start, end = volume["chapter_range"]
    stage = stage_for(end)
    return select_knobs(volume, stage)


def knob_set_hash(knob_set: dict) -> str:
    return digest(knob_set)
