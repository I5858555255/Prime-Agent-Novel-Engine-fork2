#!/usr/bin/env python3
"""Audit trend script: tracks per-chapter issue convergence over production runs.

Reads audit/per_chapter_reviews.json and audit/defects.json (or any JSON files
matching that schema) to compute:
  - Total issues per chapter across all rounds
  - New issues introduced per round vs. issues resolved
  - Forced draft rate by batch

Usage:
  python scripts/audit_trend.py [--root PROJECT_ROOT] [--out OUTPUT_JSON]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def _load_json(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("chapters", "reviews"):
            val = raw.get(key)
            if isinstance(val, list):
                return val
    return []

def _extract_round(path: Path | None) -> int | None:
    """Guess the round number from file path or name."""
    if path is None:
        return None
    name = path.stem.lower()
    m = re.search(r"round(\d+)", name)
    if m:
        return int(m.group(1))
    # Fallback: check for round-* pattern in parent dir names
    for part in path.parts:
        m2 = re.search(r"round(\d+)", part.lower())
        if m2:
            return int(m2.group(1))
    return None


def build_trend(
    project_root: str | Path,
    per_chapter_reviews: Path | None = None,
    defects_file: Path | None = None,
) -> dict:
    root = Path(project_root)
    reviews_path = per_chapter_reviews or root / "audit" / "per_chapter_reviews.json"
    defects_path = defects_file or root / "audit" / "defects.json"

    chapters = _load_json(reviews_path)
    defect_records: list[dict] = []
    if defects_path.exists():
        raw = _load_json(defects_path)
        if isinstance(raw, list):
            defect_records = raw
        elif isinstance(raw, dict):
            defect_records = [raw]

    # Aggregate by chapter
    per_chapter: dict[int, dict] = defaultdict(
        lambda: {
            "rounds": [],
            "total_issues": 0,
            "issue_kinds": set(),
            "scores": [],
            "forced_draft": False,
        }
    )

    for ch in chapters:
        ch_num = int(ch.get("chapter", 0) or ch.get("chapter_num", 0))
        if not ch_num:
            continue
        info = per_chapter[ch_num]
        score = ch.get("score") or ch.get("total_score")
        if score is not None:
            info["scores"].append(float(score))
        issues = ch.get("issues") or ch.get("dimension_issues") or []
        if isinstance(issues, list):
            info["total_issues"] += len(issues)
            for iss in issues:
                kind = iss.get("kind") or iss.get("category") or iss.get("dimension") or "unknown"
                info["issue_kinds"].add(str(kind))
        if ch.get("forced_draft") or ch.get("force_published"):
            info["forced_draft"] = True
        rnd = _extract_round(reviews_path)
        if rnd is not None and rnd not in info["rounds"]:
            info["rounds"].append(rnd)

    # Merge defect records (separate source)
    for rec in defect_records:
        ch_num = int(rec.get("chapter") or 0)
        if not ch_num:
            continue
        info = per_chapter[ch_num]
        count = rec.get("count") or rec.get("defect_count") or 0
        info["total_issues"] += int(count)
        for kind in (rec.get("kinds") or rec.get("defect_kinds") or []):
            info["issue_kinds"].add(str(kind))

    # Build summary
    total_chapters = max(per_chapter.keys()) if per_chapter else 0
    chapters_with_issues = sum(1 for v in per_chapter.values() if v["total_issues"] > 0)
    total_issues = sum(v["total_issues"] for v in per_chapter.values())
    forced_drafts = sum(1 for v in per_chapter.values() if v["forced_draft"])
    all_kinds: set[str] = set()
    for v in per_chapter.values():
        all_kinds.update(v["issue_kinds"])

    # Per-round convergence heuristic: for chapters appearing in multiple rounds,
    # flag them as potentially recurring.
    recurring_chapters = [ch for ch, info in per_chapter.items() if len(info["rounds"]) > 1]

    return {
        "project_root": str(root),
        "total_chapters_reviewed": total_chapters,
        "chapters_with_issues": chapters_with_issues,
        "total_issues_recorded": total_issues,
        "forced_draft_count": forced_drafts,
        "unique_issue_kinds": sorted(all_kinds),
        "recurring_chapters_count": len(recurring_chapters),
        "recurring_chapters": recurring_chapters[:20],  # cap for readability
        "per_chapter_sample": {
            ch: {
                "total_issues": info["total_issues"],
                "issue_kinds": sorted(info["issue_kinds"]),
                "avg_score": round(sum(info["scores"]) / len(info["scores"]), 1) if info["scores"] else None,
                "forced_draft": info["forced_draft"],
                "rounds_seen": sorted(info["rounds"]),
            }
            for ch, info in sorted(per_chapter.items())[:50]
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit trend convergence tracker")
    parser.add_argument("--root", default=None, help="Project root (default: cwd)")
    parser.add_argument("--out", default=None, help="Output JSON path")
    parser.add_argument("--reviews", default=None, help="Override per_chapter_reviews.json path")
    parser.add_argument("--defects", default=None, help="Override defects.json path")
    args = parser.parse_args()

    root = Path(args.root) if args.root else Path.cwd()
    trend = build_trend(
        root,
        per_chapter_reviews=Path(args.reviews) if args.reviews else None,
        defects_file=Path(args.defects) if args.defects else None,
    )

    out = args.out or (root / "audit" / "trend_summary.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trend, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote trend summary to {out}")
    print(
        f"  Chapters: {trend['total_chapters_reviewed']}  "
        f"Issues: {trend['total_issues_recorded']}  "
        f"Forced drafts: {trend['forced_draft_count']}  "
        f"Recurring chapters: {trend['recurring_chapters_count']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
