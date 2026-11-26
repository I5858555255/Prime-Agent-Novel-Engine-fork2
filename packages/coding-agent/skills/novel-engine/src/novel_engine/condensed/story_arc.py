"""M2: story-arc control surface — deterministic plot-direction constraints
(the "controllable" half of the viral-quality closed loop).

Where the OQ-gate foundations measure quality (judge–human agreement, drift,
retention), this module encodes **human-authored, deterministic plot-direction
constraints** and CHECKS a chapter's verdict against them. It is a *check-only*
surface: it reads a chapter's `verdict` (the 爽点/钩子/压抑 0-5 triple) + the
verdict history and reports which constraints are satisfied / violated, with a
human-readable reason for each violation (so the DRAFT/REVIEW expansion loop
can re-feed the feedback). It NEVER re-scores (that is `quality_judge`'s job)
and NEVER auto-fixes (the fix is re-expansion, handled by the existing loop).

No-op discipline: `load_arc` on a missing/empty file returns `None` (the
EXPLICIT "no arc constraint" signal — the loop is not enforcing a plot
direction, which is different from "silently passing"). `check_arc` with a
`None` arc returns `{enforced: False, ...}` so a caller can tell "no
constraint" apart from "constraint satisfied".

Constraint types (any subset; all deterministic):
- `climax_at`: [chapters] — each listed chapter MUST satisfy
  `verdict.爽点 >= climax_min` (default 4). A missed climax is a violation.
- `suppression_release`: {chapter, min_压抑, release_by_chapter} — the
  suppression chapter must have `verdict.压抑 >= min_压抑` (accumulation),
  and by `release_by_chapter` the story must have released tension: some
  chapter in [chapter, release_by_chapter] must have `爽点 >= 4 OR 钩子 >= 4`.
- `hook_density`: {min_hooks, window, ratio} — within any `window`-chapter
  sliding window, at least `ratio` of the chapters must have
  `verdict.钩子 >= min_hooks`.

Fully deterministic, no LLM, no network.
"""
from __future__ import annotations

import json
from pathlib import Path

# Default thresholds (overridable per-constraint in the JSON file).
CLIMAX_MIN = 4          # 爽点 ≥ 4 counts as a "climax" chapter.
RELEASE_MIN = 4         # 爽点 ≥ 4 OR 钩子 ≥ 4 counts as a "release" chapter.
HOO_MIN_DEFAULT = 4     # 钩子 ≥ 4 counts as a "hooked" chapter (hook_density).


def _true_int(value, default: int) -> int:
    """A true int (bool rejected) or the `default` when value is not one.
    Used so a malformed constraint value degrades to the safe default rather
    than carrying a non-int into the deterministic checks."""
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    return value


def load_arc(path) -> dict | None:
    """Load a story-arc constraint file (JSON) → an `arc` dict, or None.

    - Missing file / empty file / non-object JSON → `None` (EXPLICIT no-op:
      "no plot-direction constraint", never a fabricated arc, never raises on
      absence — that is the "not enforcing" signal).
    - A valid file is a JSON object; unrecognized keys are ignored (forward
      compat), and the three known constraint blocks are read through with
      safe defaults.
    - Deterministic: same file → same dict.
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(raw, dict):
        return None

    arc: dict = {}
    climax_at = raw.get("climax_at")
    if isinstance(climax_at, list):
        # Keep only true ints (drop bools / non-ints / duplicates, sorted).
        chapters = sorted({c for c in climax_at if isinstance(c, int) and not isinstance(c, bool)})
        if chapters:
            arc["climax_at"] = {"chapters": chapters,
                                "min": _true_int(raw.get("climax_min"), CLIMAX_MIN)}
    sr = raw.get("suppression_release")
    # `chapter` must be a true int for the block to apply; the other two fields
    # are validated (a malformed value degrades to the safe default / the
    # accumulation chapter, never a confusing non-int later in check_arc).
    if isinstance(sr, dict) and isinstance(sr.get("chapter"), int) and not isinstance(sr.get("chapter"), bool):
        acc_ch = sr["chapter"]
        arc["suppression_release"] = {
            "chapter": acc_ch,
            "min_压抑": _true_int(sr.get("min_压抑"), 3),
            "release_by_chapter": _true_int(sr.get("release_by_chapter"), acc_ch),
        }
    hd = raw.get("hook_density")
    if isinstance(hd, dict) and isinstance(hd.get("window"), int) and not isinstance(hd.get("window"), bool) and hd["window"] > 0:
        ratio = hd.get("ratio", 0.5)
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not 0.0 <= ratio <= 1.0:
            ratio = 0.5  # out-of-range / bad ratio degrades to the default.
        arc["hook_density"] = {
            "min_hooks": _true_int(hd.get("min_hooks"), HOO_MIN_DEFAULT),
            "window": hd["window"],
            "ratio": float(ratio),
        }
    # rubric / notes are documentation carried through verbatim (never data).
    for key in ("rubric", "notes"):
        if isinstance(raw.get(key), str):
            arc[key] = raw[key]
    # An empty arc object (no constraint blocks) is still "no constraint":
    # return None so check_arc reports enforced=False.
    return arc or None


def _scores_of(verdict: dict) -> dict | None:
    """The verdict's 0-5 int triple, or None if not well-formed."""
    scores = (verdict or {}).get("scores")
    if not isinstance(scores, dict):
        return None
    out: dict = {}
    for dim in ("爽点", "钩子", "压抑"):
        v = scores.get(dim)
        if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 5:
            return None
        out[dim] = v
    return out


def check_arc(chapter: int, verdict: dict, arc: dict | None,
              history: list[dict] | None = None) -> dict:
    """Deterministically check a chapter's verdict against the story-arc
    constraints.

    Args:
        chapter: the chapter number being checked.
        verdict: that chapter's verdict record ({scores: {...}, ...}).
        arc: the loaded constraint dict (or None for "no constraint").
        history: prior chapters' verdict records, as a list of
            {chapter: int, scores: {爽点, 钩子, 压抑}} — used by the
            windowed constraints (hook_density) and the release window
            (suppression_release). May be None (checked chapter only).

    Returns exactly::

        {"enforced": bool,
         "violations": [str],      # human-readable, actionable
         "satisfied":  [str]}      # which constraint kinds passed

    - `arc is None` → `{"enforced": False, "violations": [], "satisfied": ["no_arc"]}`
      (the EXPLICIT "no plot-direction constraint" no-op; not a silent pass).
    - Each violated constraint emits a one-line human reason (e.g.
      "第 N 章爽点=X < 阈值 4，climax 未达成"), suitable for re-feeding the
      DRAFT/REVIEW expansion loop.
    - Fully deterministic: same inputs → same output.
    """
    if arc is None:
        return {"enforced": False, "violations": [], "satisfied": ["no_arc"]}

    history = list(history or [])
    # Build a chapter -> scores lookup from history + the current verdict, so
    # windowed checks can see up to the current chapter.
    by_chapter: dict[int, dict] = {}
    for rec in history:
        if isinstance(rec, dict) and isinstance(rec.get("chapter"), int):
            scores = _scores_of(rec)
            if scores:
                by_chapter[rec["chapter"]] = scores
    cur_scores = _scores_of(verdict)
    if cur_scores:
        by_chapter[chapter] = cur_scores

    violations: list[str] = []
    satisfied: list[str] = []

    # -- climax_at ---------------------------------------------------------
    if "climax_at" in arc:
        spec = arc["climax_at"]
        min_c = spec.get("min", CLIMAX_MIN)
        missed = []
        for ch in spec.get("chapters", []):
            s = by_chapter.get(ch)
            if s is None:
                # A climax chapter with no verdict yet: cannot confirm — flag it
                # only if it is the current chapter (future chapters are pending).
                if ch == chapter:
                    missed.append((ch, None))
                continue
            if s["爽点"] < min_c:
                missed.append((ch, s["爽点"]))
        if missed:
            parts = [f"第{ch}章爽点={'?' if val is None else val} < 阈值 {min_c}" for ch, val in missed]
            violations.append(f"climax_at 未达成: " + ", ".join(parts))
        else:
            satisfied.append("climax_at")

    # -- suppression_release ----------------------------------------------
    if "suppression_release" in arc:
        sr = arc["suppression_release"]
        acc_ch = sr["chapter"]
        min_acc = sr.get("min_压抑", 3)
        # A release deadline before the accumulation chapter is an authoring
        # bug; normalize it to the accumulation chapter (release no earlier than
        # it accumulated) rather than producing an empty/inverted window.
        release_by = max(sr.get("release_by_chapter", acc_ch), acc_ch)
        acc = by_chapter.get(acc_ch)
        suppressed = False
        # (a) accumulation: the suppression chapter must have 压抑 >= min_acc.
        if acc is not None and acc["压抑"] < min_acc:
            violations.append(
                f"suppression_release: 第{acc_ch}章压抑={acc['压抑']} < 阈值 {min_acc}，压抑积累不足")
            suppressed = True
        # (b) release: some chapter in [acc_ch, release_by] must have
        # 爽点 >= RELEASE_MIN OR 钩子 >= RELEASE_MIN.
        window = [by_chapter[c] for c in range(acc_ch, release_by + 1) if c in by_chapter]
        released = any(s["爽点"] >= RELEASE_MIN or s["钩子"] >= RELEASE_MIN for s in window)
        if not window:
            # No verdicts yet in the release window: only flag if we're past the
            # release deadline and still haven't released.
            if chapter >= release_by and not released:
                violations.append(
                    f"suppression_release: 第{release_by}章前未释放（第{acc_ch}~{release_by}章无爽点/钩子≥{RELEASE_MIN}）")
                suppressed = True
        elif not released:
            violations.append(
                f"suppression_release: 第{acc_ch}~{release_by}章未释放（无爽点/钩子≥{RELEASE_MIN}）")
            suppressed = True
        # Satisfied only when neither sub-check raised (a local flag, not a
        # re-scan of the global violations list — robust to other constraints
        # also appending violations).
        if not suppressed:
            satisfied.append("suppression_release")

    # -- hook_density ------------------------------------------------------
    if "hook_density" in arc:
        hd = arc["hook_density"]
        min_hooks = hd.get("min_hooks", HOO_MIN_DEFAULT)
        window = hd.get("window", 0)
        ratio = hd.get("ratio", 0.5)
        if window > 0:
            # Slide a `window`-chapter window over the chapters up to `chapter`,
            # and require that in EVERY *complete* window at least `ratio` of
            # the window's chapters have 钩子 >= min_hooks. The ratio is always
            # measured over the FULL `window` chapters (not the available count),
            # so a missing chapter can never inflate the measured density.
            # A window that is not yet complete (some chapters in it have no
            # verdict yet) is skipped — it cannot be judged at the designed ratio.
            bad = []
            for start in range(max(1, chapter - window + 1), chapter - window + 2):
                seg = [c for c in range(start, start + window) if c in by_chapter]
                if len(seg) < window:
                    continue  # incomplete window (not enough history yet) — skip.
                hooked = sum(1 for c in seg if by_chapter[c]["钩子"] >= min_hooks)
                if hooked / window < ratio:
                    bad.append(start)
            if bad:
                violations.append(
                    f"hook_density 未达成: {len(bad)} 个 {window} 章窗口钩子密度 < {ratio}（钩子≥{min_hooks}）")
            else:
                satisfied.append("hook_density")

    return {"enforced": True, "violations": violations, "satisfied": satisfied}
