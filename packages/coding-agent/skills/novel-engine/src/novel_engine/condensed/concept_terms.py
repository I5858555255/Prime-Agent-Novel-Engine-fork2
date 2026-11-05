"""Dynamic forbidden-terms provider driven by concept_unlocks.json.

The full-mode workflow already has a data-driven concept-unlock system
(`config/leak_terms/concept_unlocks.json`): each term belongs to a concept,
each concept has an unlock chapter and an optional character restriction.
This module gives the condensed pipeline the same authority so the
forbidden list scales beyond the first 10 chapters without manual curation.

- Terms in a locked concept → forbidden at the current chapter.
- Terms in an unlocked concept → allowed (the concept is now known to the
  story world, so naming it is no longer a cognition-boundary violation).
- Terms not claimed by any concept → fall back to the caller's static list
  (pseudo-scientific/data/modern vocabulary that has no canonical concept).
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Iterable

# Default location of the canonical leak-terms config (same path scope_gate uses).
_DEFAULT_CONCEPT_PATH = Path(__file__).parent.parent / "config" / "leak_terms" / "concept_unlocks.json"


@lru_cache(maxsize=4)
def _load_concepts(path: str) -> list[dict]:
    """Read concept_unlocks.json and return the flat concepts list."""
    p = Path(path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    concepts = list(data.get("concepts", []))
    # Also harvest concepts declared in the unlock schedule (V02/V03 unlocks).
    for vol in data.get("unlock_schedule", []):
        for cu in vol.get("unlocks", []):
            concepts.append(cu)
    # Dedupe by concept_id (keep the first definition that carries terms).
    seen: dict[str, dict] = {}
    for c in concepts:
        cid = c.get("concept_id")
        if not cid or cid in seen:
            continue
        base = seen.get(cid)
        if base is None or not base.get("terms") and c.get("terms"):
            seen[cid] = c
        else:
            merged = dict(base)
            merged_terms = list(base.get("terms", []))
            for t in c.get("terms", []):
                if t not in merged_terms:
                    merged_terms.append(t)
            merged["terms"] = merged_terms
            # Earlier unlock chapter wins (most permissive).
            if c.get("unlocked_at_chapter") is not None:
                merged["unlocked_at_chapter"] = min(
                    int(merged.get("unlocked_at_chapter") or 10**9),
                    int(c["unlocked_at_chapter"]),
                )
            seen[cid] = merged
    return list(seen.values())


def term_concept_map(path: str | Path | None = None) -> dict[str, dict]:
    """Return {term: concept} for every term declared in concept_unlocks.json.

    A term may be declared in multiple concepts; the most permissive one
    (lowest unlock chapter, wildcard character) wins so the term is released
    as early as the story permits.
    """
    p = str(Path(path) if path else _DEFAULT_CONCEPT_PATH)
    mapping: dict[str, dict] = {}
    for concept in _load_concepts(p):
        cid = concept.get("concept_id")
        for term in concept.get("terms", []):
            if not isinstance(term, str) or not term.strip():
                continue
            term = term.strip()
            cur = mapping.get(term)
            if cur is None:
                mapping[term] = concept
                continue
            # Prefer the concept that unlocks earlier (lower chapter), then
            # the wildcard-restriction one, so a term is never blocked later
            # than the story allows.
            def rank(c: dict) -> tuple:
                ch = int(c.get("unlocked_at_chapter") or 10**9)
                wc = 0 if str(c.get("applies_to_character") or "*") == "*" else 1
                return (ch, wc)
            if rank(concept) < rank(cur):
                mapping[term] = concept
    return mapping


def is_term_unlocked(term: str, chapter: int,
                    term_map: dict[str, dict] | None = None,
                    subject: str | None = None) -> bool:
    """True if `term` belongs to a concept already unlocked at `chapter`.

    `subject` is the sentence/beat text used for character-restricted
    concepts: a term whose concept applies only to a named character is
    released only when that character appears in `subject`.
    """
    if term_map is None:
        term_map = term_concept_map()
    concept = term_map.get(term)
    if concept is None:
        return False  # no concept claim → not unlocked (caller keeps static fallback)
    unlock_ch = int(concept.get("unlocked_at_chapter") or 10**9)
    if chapter < unlock_ch:
        return False
    applies = str(concept.get("applies_to_character") or "*")
    if applies == "*":
        return True
    return bool(subject) and applies in subject


def forbidden_for_chapter(chapter: int, static_terms: Iterable[str] | None = None,
                          subject: str | None = None,
                          term_map: dict[str, dict] | None = None) -> list[str]:
    """Forbidden terms at `chapter`: static fallback + all locked concept terms.

    Static terms are those that are NOT claimed by any concept (pseudo-
    scientific/data/modern vocabulary). Concept terms are forbidden only
    while their concept is still locked.
    """
    if term_map is None:
        term_map = term_concept_map()
    static = list(static_terms or [])
    # 1. Static fallback: only the terms no concept claims.
    claimed = set(term_map.keys())
    result: list[str] = [t for t in dict.fromkeys(static) if t not in claimed]
    # 2. Locked concept terms: forbidden while the concept is not unlocked.
    for term, concept in term_map.items():
        if is_term_unlocked(term, chapter, term_map, subject):
            continue
        if term not in result:
            result.append(term)
    return result
