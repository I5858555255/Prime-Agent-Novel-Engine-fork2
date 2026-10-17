# -*- coding: utf-8 -*-
"""Concept-unlock audit helper (read-only).

Given a chapter number and text, report:
  - which infant-arc hard-block terms were hit and where;
  - for each hit term that belongs to a concept_unlocks group, whether that
    concept is unlocked at this chapter (chapter-level), and what its
    applies_to_character constraint is;
  - a suggested disposition: belongs to an unlocked concept (release),
    belongs to a locked concept (genuine leak / keep blocked), or not mapped
    (legacy behavior).

This is the first triage tool for the ch10+ arc rule: when a new leak or
false positive appears, run this script and decide by concept state instead
of adding a keyword.

Usage (from src/):
  python -m novel_engine.scripts.concept_audit <chapter> [text_file|-]
  (text defaults to stdin)
"""
import json
import sys
from pathlib import Path

from novel_engine.engine.db import StateDB
from novel_engine.quality.scope_gate import (
    detect_scope_violations,
    reset_concept_cache,
    _build_term_to_concept_map,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    chapter = int(sys.argv[1])
    if len(sys.argv) >= 3 and sys.argv[2] != "-":
        text = Path(sys.argv[2]).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()

    reset_concept_cache()
    result = detect_scope_violations(text, chapter, {}, ROOT)

    mapping = _build_term_to_concept_map(ROOT)
    db = StateDB(project_root=ROOT)  # import_from_json runs in __init__

    print(f"=== concept audit: chapter {chapter} ===")
    if not result["hard"] and not result["soft"]:
        print("no scope hits.")
        return 0

    for label, items in (("HARD", result["hard"]), ("SOFT", result["soft"])):
        for h in items:
            term = h["term"]
            cid = mapping.get(term)
            if cid is None:
                print(f"[{label}] {term} — not in concept_unlocks (legacy path)")
                continue
            row = db.execute_custom_query(
                "SELECT unlocked_at_chapter, applies_to_character FROM concept_unlocks WHERE concept_id = ?",
                (cid,),
            )
            if not row:
                print(f"[{label}] {term} — concept {cid} missing from DB (import issue?)")
                continue
            r = row[0]
            unlocked = int(r["unlocked_at_chapter"]) <= chapter
            state = "UNLOCKED (chapter)" if unlocked else "LOCKED (chapter)"
            print(
                f"[{label}] {term} — concept {cid} {state} "
                f"(unlock@ch{r['unlocked_at_chapter']}, applies_to={r['applies_to_character']})"
                f" — sentence: {h['sentence'][:40]}"
            )
            if unlocked and str(r["applies_to_character"]) != "*":
                print(f"           note: concept is character-scoped; sentence subject must be "
                      f"{r['applies_to_character']} to be released")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
