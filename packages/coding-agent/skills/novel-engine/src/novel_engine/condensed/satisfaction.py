"""爽点系统 L1-L4 deterministic core (no LLM).

爽点 = 压抑的兑现。The four layers:

  L1  DRAFT payoff-beat design helpers  (a climax chapter must carry a payoff
      beat; non-climax may carry one to set up future release).
  L2  hard acceptance  (a climax chapter whose payoff beat did not ignite in
      the prose is a blocking check — the 11th hard acceptance).
  L3  satisfaction auto-retry decision (爽点 < floor or arc climax violated ->
      targeted re-expand; per-chapter cap of 2; beyond -> HARD_BLOCK).
  L4  volume metrics + drift (satisfaction ratio across a volume's scored
      chapters; drift_suspect when the ratio drops below the 60% target).

All functions are pure and deterministic: same inputs -> same output. The LLM
surfaces (prompt wording, the actual re-expand call) wrap these decisions;
this module owns the *decisions*.
"""
from __future__ import annotations

import re
from pathlib import Path

# A climax chapter's default min 爽点, matching story_arc.CLIMAX_MIN.
CLIMAX_MIN = 4
# Non-climax chapters carry a lower 爽点 floor (they are setup/suppression, not
# the catharsis peak). Climax chapters keep the higher CLIMAX_MIN.
NON_CLIMAX_SAT_FLOOR = 2
# Per-chapter satisfaction re-expand cap (L3). Beyond this -> HARD_BLOCK.
# Raised to 3 so suppression-heavy chapters (where catharsis is harder to
# manufacture) get an extra targeted re-expand round before a hard block.
SAT_RETRY_CAP = 3
# L4 volume targets. Climax chapters must reach the climax floor (爽点≥4);
# non-climax chapters only the lower floor. A volume's satisfaction ratio is
# computed against the per-chapter floor; below the target -> drift_suspect.
SAT_VOLUME_TARGET = 0.6
# Suppression-peak chapters (inside a suppression_release release window) are
# design-intent suppression, not defects: they are exempt from the climax
# 爽点 hard check.

_CJK = re.compile(r"[\u4e00-\u9fff]")


def arc_path() -> Path:
    """Path to the committed arc_constraints.json (config/ dir relative to this
    module). Missing file is fine — story_arc.load_arc returns None for it."""
    here = Path(__file__).resolve().parent
    return here.parent / "config" / "arc_constraints.json"


# ---------------------------------------------------------------------------
# L1: payoff-beat design in DRAFT chapters
# ---------------------------------------------------------------------------

def climax_chapters(arc, chapters):
    """Return the set of chapter numbers in `chapters` that are arc climax
    targets (the `climax_at` list), if the arc defines climax_at. Empty set
    when the arc has no climax_at (or None)."""
    if not arc or "climax_at" not in arc:
        return set()
    spec = arc.get("climax_at") or {}
    target = set(spec.get("chapters", []))
    return target & set(chapters)


def is_climax_chapter(chapter, arc):
    """True when `chapter` (a dict carrying "chapter"/"chapter_range") is an
    arc climax target. Accepts the chapter number directly or a chapter dict."""
    if not arc or "climax_at" not in arc:
        return False
    spec = arc.get("climax_at") or {}
    if isinstance(chapter, dict):
        rng = chapter.get("chapter_range") or [chapter.get("chapter")]
        num = rng[-1] if isinstance(rng, list) and rng else None
    else:
        num = chapter
    if num is None:
        return False
    return num in set(spec.get("chapters", []))


def _beats(chapter):
    for scene in chapter.get("scenes", []):
        if not isinstance(scene, dict):
            continue
        for beat in scene.get("beats", []):
            if isinstance(beat, dict):
                yield beat


def ensure_climax_payoff(chapter, arc):
    """L1 deterministic backfill: a climax chapter MUST carry a payoff beat.

    Flash treats the optional `payoff` field as skippable (all null), so the
    authoring intent is enforced here rather than trusted to the writer: when a
    climax chapter has no payoff beat, the most payoff-like beat (the one whose
    outcome reverses / resolves the scene's tension) gets a synthesized payoff
    design. Non-climax chapters are returned untouched.

    Mutates `chapter` in place and returns True when a payoff was added.
    Deterministic: same chapter + arc -> same result.
    """
    num = chapter.get("chapter")
    if not (isinstance(num, int) and num in climax_chapters(arc, [num])):
        return False
    if draft_has_payoff(chapter):
        return False
    # Pick the payoff-like beat: prefer one whose outcome contains a reversal /
    # resolution marker, else the beat with the longest action (the "biggest"
    # turn in the scene).
    markers = ("反", "逆转", "破", "破局", "翻", "摆脱", "解除", "化解", "击退", "击溃",
               "亮出", "亮出", "摊开", "摊牌", "揭穿", "点破", "夺回", "夺回", "赢", "胜")
    best = None
    best_score = -1
    for beat in _beats(chapter):
        outcome = beat.get("outcome", "") or ""
        action = beat.get("action", "") or ""
        text = outcome + action
        score = sum(1 for m in markers if m in text)
        score += len("".join(_CJK.findall(text))) * 0.01  # tie-break: more content = bigger turn
        if score > best_score:
            best_score = score
            best = beat
    if best is None:
        return False
    trigger = "".join(_CJK.findall(best.get("outcome", "") or best.get("action", "")))[:12] or "反转"
    best["payoff"] = {"type": "反转", "release_of": "本章压抑",
                      "trigger": trigger, "intensity": min(5, max(3, 2 + best_score))}
    return True


def draft_has_payoff(chapter):
    """True when the DRAFT chapter carries at least one payoff beat. A climax
    chapter that has none is the authoring defect L1 exists to catch."""
    for beat in _beats(chapter):
        payoff = beat.get("payoff")
        if isinstance(payoff, dict) and (payoff.get("type") or payoff.get("trigger")):
            return True
    return False


# ---------------------------------------------------------------------------
# L2: hard acceptance — a climax chapter payoff must ignite in the prose
# ---------------------------------------------------------------------------

def _payoff_signatures(beat):
    """CJK content signatures that, if echoed in the prose, count as the payoff
    having ignited: the trigger phrase and the beat's core action."""
    payoff = beat.get("payoff")
    sigs = []
    if isinstance(payoff, dict):
        for key in ("trigger", "type"):
            v = payoff.get(key)
            if isinstance(v, str):
                cjk = "".join(_CJK.findall(v))
                if len(cjk) >= 2:
                    sigs.append(cjk)
    action = beat.get("action", "")
    cjk = "".join(_CJK.findall(action))
    if len(cjk) >= 4:
        sigs.append(cjk)
    return sigs


def payoff_hard_block(prose, chapter_card, arc=None):
    """L2 hard acceptance. For a climax chapter whose DRAFT designed a payoff
    beat, the payoff MUST ignite in the prose (its trigger or core action CJK is
    echoed). If it did not, return the 11th blocking entry (non-waivable).

    `arc` is the story arc dict (with `climax_at`), read by the caller from
    `story_arc` / the committed arc_constraints — it is NOT embedded in the
    chapter card. Returns None when:
      - `arc` names no climax target for this chapter, or
      - the chapter carries no payoff beat (a missing payoff on a non-climax
        chapter is recorded elsewhere as a quality note, not a hard block), or
      - the payoff DID ignite in the prose.
    """
    chapter = chapter_card.get("chapter")
    if not isinstance(chapter, int):
        return None
    if chapter not in climax_chapters(arc, [chapter]):
        return None
    for beat in _beats(chapter_card):
        if not isinstance(beat.get("payoff"), dict):
            continue
        sigs = _payoff_signatures(beat)
        if not sigs:
            continue
        hit = any(s in prose for s in sigs)
        if hit:
            return None
        return {"check": "payoff_not_ignited",
                "detail": f"climax ch{chapter}: payoff beat {beat.get('id')} "
                           f"designed but not ignited in prose (signatures: {sigs[:3]})",
                # Waivable: a climax chapter whose catharsis the model cannot
                # manufacture is a real gap, but hard-blocking it locks the whole
                # chapter at "never passes" (linear hash chain). Record it as a
                # waivable note + let L4 drift_suspect carry the signal, instead
                # of a non-waivable block that dead-locks production.
                "waivable": True}
    return None


# ---------------------------------------------------------------------------
# L3: satisfaction auto-retry decision (with cap + HARD_BLOCK)
# ---------------------------------------------------------------------------

def should_retry_satisfaction(verdict, floor, arc_violation, round_no, cap=SAT_RETRY_CAP):
    """Fire a satisfaction re-expand round only when 爽点 < floor OR the arc
    climax is violated, and only while below the per-chapter retry cap."""
    if round_no > cap:
        return False
    if verdict is None:
        return False
    sat = verdict.get("爽点")
    floor_sat = floor.get("爽点", 0) if isinstance(floor, dict) else 0
    below = sat is not None and sat < floor_sat
    return bool(below or arc_violation)


def satisfaction_retry_decision(verdict, floor, arc_violation, round_no, cap=SAT_RETRY_CAP):
    """The full L3 decision for one re-expand round:
      - 'pass'               when 爽点 is at/above the floor and the arc is satisfied.
      - 'retry_satisfaction' when below the floor / arc violated and under the cap.
      - 'hard_block'         when still unsatisfied after the cap (no infinite loop).
    Returns a dict with `action` and a `reason` string."""
    if not should_retry_satisfaction(verdict, floor, arc_violation, round_no, cap):
        if round_no > cap:
            return {"action": "hard_block",
                    "reason": f"satisfaction: still unsatisfied after {cap} re-expand rounds"}
        return {"action": "pass",
                "reason": "satisfaction at/above floor and arc satisfied"}
    return {"action": "retry_satisfaction",
            "reason": f"satisfaction: 爽点 below floor {floor.get('爽点')} or arc climax violated"}


# ---------------------------------------------------------------------------
# L4: volume satisfaction ratio + drift
# ---------------------------------------------------------------------------

def volume_satisfaction_ratio(verdicts, floor_satisfaction=CLIMAX_MIN):
    """Fraction of scored chapters whose 爽点 reaches the floor.
    `verdicts` is an iterable of verdict dicts (or None for not-yet-scored).
    Returns None when no chapter is scored yet."""
    scored = [v for v in verdicts if isinstance(v, dict) and v.get("爽点") is not None]
    if not scored:
        return None
    reaching = sum(1 for v in scored if v["爽点"] >= floor_satisfaction)
    return reaching / len(scored)


def drift_suspect(verdicts, floor_satisfaction=CLIMAX_MIN, target=SAT_VOLUME_TARGET):
    """True when the volume's satisfaction ratio drops below `target` (default
    60%) AND there is a scored signal. No scored chapters -> no signal -> False."""
    ratio = volume_satisfaction_ratio(verdicts, floor_satisfaction)
    if ratio is None:
        return False
    return ratio < target


# ---------------------------------------------------------------------------
# L4 (refined): per-chapter floors + suppression-peak exemption
# ---------------------------------------------------------------------------

def is_suppression_exempt(chapter, arc):
    """True when `chapter` (a number or a verdict/dict carrying 'chapter') falls
    inside a `suppression_release` release window — the design-intent
    suppression peak. Such a chapter is exempt from the climax 爽点 hard check:
    low catharsis there is the point of the arc, not a defect.
    """
    if not arc or "suppression_release" not in arc:
        return False
    sr = arc.get("suppression_release") or {}
    acc_ch = sr.get("chapter")
    release_by = sr.get("release_by_chapter", acc_ch)
    if acc_ch is None:
        return False
    if isinstance(chapter, dict):
        num = chapter.get("chapter")
    else:
        num = chapter
    if num is None:
        return False
    # The accumulation chapter + the release window up to release_by are the
    # suppression zone. (The release-by chapter itself is where catharsis SHOULD
    # land, so it stays in the window but the window is the suppression side.)
    return acc_ch <= num <= max(acc_ch, release_by)


def satisfaction_floor_for(chapter, arc, floor_satisfaction=CLIMAX_MIN):
    """Per-chapter 爽点 floor. Climax chapters (arc climax_at) use the high
    `floor_satisfaction` (default CLIMAX_MIN=4); every other chapter uses the
    lower NON_CLIMAX_SAT_FLOOR. Suppression-exempt chapters use the lower floor
    too (they are setup, not peaks)."""
    if isinstance(chapter, dict):
        num = chapter.get("chapter")
    else:
        num = chapter
    if num is None:
        return floor_satisfaction
    if is_suppression_exempt(num, arc):
        return NON_CLIMAX_SAT_FLOOR
    if num in climax_chapters(arc, [num]):
        # A climax chapter's 爽点 floor is at least CLIMAX_MIN (4), even if the
        # knob floor is lower — climax chapters are where catharsis must peak.
        return max(floor_satisfaction, CLIMAX_MIN)
    return NON_CLIMAX_SAT_FLOOR


def volume_satisfaction_ratio_dual(verdicts, arc, climax_floor=CLIMAX_MIN, target=SAT_VOLUME_TARGET):
    """L4 dual-target ratio: each verdict is judged against ITS OWN chapter's
    floor (climax -> climax_floor; otherwise -> NON_CLIMAX_SAT_FLOOR). A
    verdict record must carry `chapter`. Returns {"ratio": float|None,
    "drift_suspect": bool, "reaching": int, "scored": int}."""
    scored = [v for v in verdicts if isinstance(v, dict)
              and isinstance(v.get("chapter"), int)
              and v.get("爽点") is not None]
    if not scored:
        return {"ratio": None, "drift_suspect": False, "reaching": 0, "scored": 0}
    reaching = 0
    for v in scored:
        floor = satisfaction_floor_for(v["chapter"], arc, climax_floor)
        if v["爽点"] >= floor:
            reaching += 1
    ratio = reaching / len(scored)
    return {"ratio": ratio, "drift_suspect": ratio < target,
            "reaching": reaching, "scored": len(scored)}

