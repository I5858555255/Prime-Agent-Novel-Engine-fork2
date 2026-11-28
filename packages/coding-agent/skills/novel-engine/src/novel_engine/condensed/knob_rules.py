"""Deterministic knob-rule engine (M2-2).

v0's `select_knobs` is a fixed per-stage knob_set (no LLM). This module adds an
optional, still-fully-deterministic rules layer: when `quality_config.json`
carries a `knob_rules` block, `select_knobs` delegates here; otherwise v0's
per-stage behavior is returned byte-for-byte (the critical regression guard).

Pure JSON matching, no LLM / no network. A rule's `when` may combine any of:
  - `climax_within_last_chapters` (int): the volume's climax_chapter falls
    within the last N chapters of its chapter_range (inclusive window).
  - `theme_contains` (str): the volume's theme contains the substring
    (case-insensitive).
  - `stage` (int): the segment's stage (exact match).
A rule matches when EVERY condition it sets holds (AND). First matching rule
(in list order) wins -> its `knob_set`; when none match and
`fallback_stage_knobs` (default True) -> the v0 per-stage fixed knob_set
(matched_rule="stage-fallback"). Fully deterministic, pure JSON, no LLM.
"""
from __future__ import annotations

from . import volume_knobs


def _climax_within_last_chapters(volume: dict, n: int) -> bool:
    """True when climax_chapter is within the last `n` chapters of the
    chapter_range (inclusive: climax at the range end is within-last-1).
    A missing/non-int climax_chapter or range, or n < 1, is never within."""
    _start, end = volume.get("chapter_range", [0, 0])
    climax = volume.get("climax_chapter")
    if not isinstance(climax, int) or not isinstance(n, int) or n < 1:
        return False
    return (end - n + 1) <= climax <= end


def _theme_contains(volume: dict, substring: str) -> bool:
    """Case-insensitive substring test on the volume's theme."""
    theme = volume.get("theme") or ""
    return isinstance(substring, str) and substring.lower() in theme.lower()


def _condition_holds(when: dict, volume: dict, stage: int) -> bool:
    """A rule matches when EVERY condition it sets holds (AND semantics)."""
    if not isinstance(when, dict):
        return False
    if "climax_within_last_chapters" in when and not _climax_within_last_chapters(volume, when["climax_within_last_chapters"]):
        return False
    if "theme_contains" in when and not _theme_contains(volume, when["theme_contains"]):
        return False
    if "stage" in when and when["stage"] != stage:
        return False
    return True


def _rule_id(rule: dict, index: int) -> str:
    """A rule's identifier: its explicit `id` when present, else a positional
    name. Recorded in rationale.matched_rule so a pick is auditable/reproducible."""
    rid = rule.get("id")
    return rid if (isinstance(rid, str) and rid) else f"rule-{index}"


def evaluate_knob_rules(volume: dict, stage: int, rules_cfg: dict) -> tuple[dict, dict]:
    """Evaluate the optional `knob_rules` config for a volume's stage.

    `rules_cfg` = the `quality_config.json` `knob_rules` block:
    {"rules_ver": str, "rules": [{when, knob_set, id?}, ...],
    "fallback_stage_knobs": bool (default True)}.

    First matching rule (list order) -> its knob_set, rationale
    {rules_ver, matched_rule: <rule id>, bible_field: "world",
    climax_chapter, stage}. No match + fallback (default True) -> the v0
    per-stage fixed knob_set, matched_rule="stage-fallback". No match +
    fallback=False -> empty knob set, matched_rule=None. Fully deterministic:
    same volume + same rules_cfg -> same knob set. No LLM.
    """
    rules_cfg = rules_cfg or {}
    rules_ver = rules_cfg.get("rules_ver")
    rules = rules_cfg.get("rules") or []
    fallback = rules_cfg.get("fallback_stage_knobs", True)

    for index, rule in enumerate(rules):
        if _condition_holds(rule.get("when") or {}, volume, stage):
            knob_set = dict(rule.get("knob_set", {}))
            rationale = {
                "rules_ver": rules_ver,
                "matched_rule": _rule_id(rule, index),
                "bible_field": "world",
                "climax_chapter": volume.get("climax_chapter"),
                "stage": stage,
            }
            return knob_set, rationale

    if fallback:
        knob_set = dict(volume_knobs.knob_set_for_stage(stage))
        rationale = {
            "rules_ver": rules_ver,
            "matched_rule": "stage-fallback",
            "bible_field": "world",
            "climax_chapter": volume.get("climax_chapter"),
            "stage": stage,
        }
        return knob_set, rationale

    rationale = {
        "rules_ver": rules_ver,
        "matched_rule": None,
        "bible_field": "world",
        "climax_chapter": volume.get("climax_chapter"),
        "stage": stage,
    }
    return {}, rationale
