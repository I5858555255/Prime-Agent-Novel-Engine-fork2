"""Deterministic judge–human calibration agreement (M2 OQ2 gate, §2).

No-op foundation for the OQ2 gold-set gate: when a gold set of human scores
exists, `score_agreement` computes a *deterministic* judge–human agreement
(per-dimension MAE + Pearson correlation + an overall agreement in 0..1).
When there is no reference (empty gold, or no chapter overlap with the
verdicts) it is an EXPLICIT no-op: `ready=False`, `n=0`, all MAE 0.0, all
correlations None — it never fabricates a reference and never raises.

Fully deterministic: no LLM, no network, no randomness. Same inputs → same
output. This is the "meter" the OQ2 gold set will calibrate; it is NOT a
re-run of the critic.
"""
from __future__ import annotations

# The three viral-quality dimensions, matching quality_judge / volume_knobs /
# quality_store. Fixed order so the per_dim_* maps are deterministic.
DIMS = ("爽点", "钩子", "压抑")


def _valid_scores(record: dict) -> dict | None:
    """Return the record's scores dict iff it is a well-formed int-0-5 triple.

    A malformed record (missing/None scores, a dim absent, a non-int, or an
    out-of-range value) is excluded so a bad entry never poisons the MAE.
    Returns the scores dict, or None when the record is not usable.
    """
    scores = record.get("scores")
    if not isinstance(scores, dict):
        return None
    for dim in DIMS:
        value = scores.get(dim)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        if not 0 <= value <= 5:
            return None
    return scores


def _most_common(values: list) -> str | None:
    """The most common value in `values`; deterministic tie-break (smallest
    string among the max-count values). None when the list is empty."""
    if not values:
        return None
    counts: dict = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    top = max(counts.values())
    tied = [value for value, count in counts.items() if count == top]
    return min(tied)


def _pearson(x: list, y: list) -> float | None:
    """Pearson correlation of two equal-length numeric series.

    Returns None when there are fewer than 2 points, or when either series has
    zero variance (all values identical) — a correlation is undefined then.
    """
    if len(x) < 2 or len(y) < 2 or len(x) != len(y):
        return None
    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n
    sxx = sum((v - mean_x) ** 2 for v in x)
    syy = sum((v - mean_y) ** 2 for v in y)
    if sxx == 0 or syy == 0:
        return None
    sxy = sum((a - mean_x) * (b - mean_y) for a, b in zip(x, y, strict=True))
    return sxy / ((sxx ** 0.5) * (syy ** 0.5))


def score_agreement(gold: list, verdicts: list, min_n: int = 30) -> dict:
    """Deterministic judge–human agreement over the chapters present in BOTH
    `gold` and `verdicts` (matched by `chapter`).

    Returns exactly::

        {"n": int,
         "per_dim_mae": {爽点:float, 钩子:float, 压抑:float},
         "per_dim_corr": {爽点:float|None, 钩子:float|None, 压抑:float|None},
         "overall_agreement": float,
         "prompt_ver": str|None, "judge_model": str|None,
         "ready": bool}

    - `n` = the number of chapters scored (gold ∩ verdicts, well-formed).
    - `per_dim_mae[d]` = mean of |judge[d] − human[d]| over the scored chapters
      (0.0 when n=0).
    - `overall_agreement` = 1 − (mean of the three per-dim MAE) / 5, in 0..1.
    - `per_dim_corr[d]` = Pearson correlation of judge vs human on dim d over
      the scored chapters; None when fewer than 2 points or zero variance.
    - `prompt_ver` / `judge_model` = the most common value among the verdict
      records that share a scored chapter (uniform per the verdict contract;
      most-common with a deterministic lexicographic tie-break); None when no
      scored verdicts carry that field.
    - `ready` = (n >= min_n). An empty gold OR no overlap → n=0, ready=False,
      all mae 0.0, all corr None: an EXPLICIT no-op that never fabricates a
      reference and never raises.
    """
    gold_by: dict = {rec.get("chapter"): _valid_scores(rec) for rec in gold}
    verdict_by: dict = {rec.get("chapter"): _valid_scores(rec) for rec in verdicts}

    # A chapter is scored only when it appears in BOTH with a well-formed
    # scores triple; a malformed record on either side is excluded.
    scored_chapters: list = [
        chapter
        for chapter in sorted((ch for ch in gold_by if ch is not None), key=repr)
        if chapter in verdict_by and gold_by[chapter] is not None and verdict_by[chapter] is not None
    ]
    shared = [(gold_by[chapter], verdict_by[chapter]) for chapter in scored_chapters]
    n = len(shared)

    per_dim_mae: dict = {dim: 0.0 for dim in DIMS}
    per_dim_corr: dict = {dim: None for dim in DIMS}
    if n:
        for dim in DIMS:
            abs_diffs = [abs(g[dim] - v[dim]) for g, v in shared]
            per_dim_mae[dim] = sum(abs_diffs) / n
            per_dim_corr[dim] = _pearson(
                [v[dim] for _, v in shared], [g[dim] for g, _ in shared]
            )

    overall_agreement = 1.0 - (sum(per_dim_mae.values()) / len(DIMS)) / 5.0

    # prompt_ver / judge_model come from the verdict RECORDS (not the scores
    # dicts) that share a scored chapter; most-common is deterministic.
    scored_set = set(scored_chapters)
    verdict_records = [rec for rec in verdicts if rec.get("chapter") in scored_set]
    prompt_vers = [rec.get("prompt_ver") for rec in verdict_records if rec.get("prompt_ver") is not None]
    judge_models = [rec.get("judge_model") for rec in verdict_records if rec.get("judge_model") is not None]

    return {
        "n": n,
        "per_dim_mae": per_dim_mae,
        "per_dim_corr": per_dim_corr,
        "overall_agreement": overall_agreement,
        "prompt_ver": _most_common(prompt_vers),
        "judge_model": _most_common(judge_models),
        "ready": n >= min_n,
    }
