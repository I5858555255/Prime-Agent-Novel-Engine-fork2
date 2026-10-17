#!/usr/bin/env python3
"""Shadow comparison: old vs new severity-gated blocking on per_chapter_reviews.json.

Computes how many chapters would change verdict under the new severity gate:
- Old rule: any hard-dimension issue blocks regardless of severity
- New rule: hard-dimension issues only block at high/medium severity
- Also tracks advisory_high count (note-dimension + high severity)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


# Copy of DEFAULT_POLICY severity_map from quality_policy.py
SEVERITY_MAP = {
    "leak_scaffolding": "hard",
    "truncation": "hard",
    "verbatim_duplication": "hard",
    "forbidden_block": "hard",
    "scene_missing": "hard",
    "length_deviation": "note",
    "beat_repetition_thematic": "note",
    "hook_missing": "note",
    "plot_consistency": "hard",
    "foreshadow_execution": "hard",
    "character_consistency": "note",
    "style_match": "note",
    "pacing": "note",
    "innovation": "note",
    "hook_strength": "note",
    "reader_retention": "note",
    "cliffhensity": "note",
}


def is_blocking(policy: dict, category: str) -> bool:
    return policy.get("severity_map", {}).get(category) == "hard"


def _old_review_issue_is_blocking(policy: dict, issue: dict) -> bool:
    """Old logic: category→dimension→severity fallback, no severity gate."""
    return is_blocking(policy, issue.get("category") or issue.get("dimension") or issue.get("severity", ""))


def _new_review_issue_is_blocking(policy: dict, issue: dict) -> tuple[bool, bool]:
    """New logic: severity-gated. Returns (blocking, advisory_high)."""
    is_hard_dim = issue.get("category") or issue.get("dimension")
    sev = issue.get("severity", "").lower()

    if is_blocking(policy, is_hard_dim):
        return sev in ("high", "medium"), False

    if sev == "high":
        return False, True

    return False, False


def analyze_review(review: dict, policy: dict) -> dict:
    issues = review.get("issues") or []
    chapter = review.get("chapter_num", "?")
    old_verdict = review.get("verdict", "unknown")

    old_blocking = []
    new_blocking = []
    new_advisory = []

    for iss in issues:
        dim = iss.get("dimension", "unknown")
        sev = iss.get("severity", "unknown")
        cat = iss.get("category")

        # Old logic
        if _old_review_issue_is_blocking(policy, iss):
            old_blocking.append(f"{dim}/{sev}")

        # New logic
        b, adv = _new_review_issue_is_blocking(policy, iss)
        if b:
            new_blocking.append(f"{dim}/{sev}")
        if adv:
            new_advisory.append(f"{dim}/{sev}")

    # Chapter would change verdict if old had blocking but new doesn't
    old_blocked = len(old_blocking) > 0
    new_blocked = len(new_blocking) > 0
    changed = old_blocked and not new_blocked

    return {
        "chapter": chapter,
        "old_verdict": old_verdict,
        "old_blocking_count": len(old_blocking),
        "old_blocking_issues": old_blocking,
        "new_blocking_count": len(new_blocking),
        "new_blocking_issues": new_blocking,
        "new_advisory_count": len(new_advisory),
        "new_advisory_issues": new_advisory,
        "verdict_changed": changed,
    }


def main():
    root = Path(__file__).parent.parent.parent.parent / "src" / "novel_engine" / "audit"
    path = root / "per_chapter_reviews.json"

    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 1

    data = json.loads(path.read_text(encoding="utf-8"))
    reviews = data.get("reviews", []) if isinstance(data, dict) else data

    policy = {"severity_map": SEVERITY_MAP}

    results = [analyze_review(r, policy) for r in reviews]

    # Aggregate stats
    total_chapters = len(results)
    old_blocked_chapters = sum(1 for r in results if r["old_blocking_count"] > 0)
    new_blocked_chapters = sum(1 for r in results if r["new_blocking_count"] > 0)
    changed_chapters = sum(1 for r in results if r["verdict_changed"])
    total_old_blocking_issues = sum(r["old_blocking_count"] for r in results)
    total_new_blocking_issues = sum(r["new_blocking_count"] for r in results)
    total_advisory_high = sum(r["new_advisory_count"] for r in results)

    # Chapters that changed from blocked to unblocked
    changed_details = [r for r in results if r["verdict_changed"]]

    print("=" * 70)
    print("SEVERITY-GATED BLOCKING SHADOW COMPARISON")
    print("=" * 70)
    print(f"Data source: {path}")
    print(f"Total chapters analyzed: {total_chapters}")
    print()
    print("--- Aggregate Stats ---")
    print(f"Old rule (no severity gate):     {old_blocked_chapters}/{total_chapters} chapters blocked")
    print(f"  Total blocking issues (old):   {total_old_blocking_issues}")
    print(f"New rule (severity-gated):       {new_blocked_chapters}/{total_chapters} chapters blocked")
    print(f"  Total blocking issues (new):   {total_new_blocking_issues}")
    print(f"  Total advisory_high issues:    {total_advisory_high}")
    print()
    print(f"Chapters with verdict change:    {changed_chapters}/{total_chapters}")
    print()

    if changed_details:
        print("--- Chapters with Verdict Change (old=blocked → new=not blocked) ---")
        for r in changed_details:
            print(f"  Ch{r['chapter']:>2}: old_blocked={r['old_blocking_issues']} → new_blocked={r['new_blocking_issues']}, advisory={r['new_advisory_issues']}")
        print()

    print("--- Per-Chapter Detail ---")
    print(f"{'Ch':>4} | {'Old Verdict':>10} | {'Old Block':>9} | {'New Block':>9} | {'Adv High':>9} | {'Changed'}")
    print("-" * 70)
    for r in results:
        changed_marker = "YES" if r["verdict_changed"] else ""
        print(f"{r['chapter']:>4} | {r['old_verdict']:>10} | {r['old_blocking_count']:>9} | {r['new_blocking_count']:>9} | {r['new_advisory_count']:>9} | {changed_marker}")
    print()

    # forced_draft_rate impact estimate
    if total_chapters > 0:
        old_force_rate = old_blocked_chapters / total_chapters
        new_force_rate = new_blocked_chapters / total_chapters
        print("--- Estimated forced_draft_rate Impact ---")
        print(f"Old forced_draft_rate estimate: {old_force_rate:.1%}")
        print(f"New forced_draft_rate estimate: {new_force_rate:.1%}")
        if old_force_rate > 0:
            change_pct = (old_force_rate - new_force_rate) / old_force_rate * 100
            print(f"Reduction in blocking rate:      {change_pct:.1f}%")

    return 0


if __name__ == "__main__":
    sys.exit(main())
