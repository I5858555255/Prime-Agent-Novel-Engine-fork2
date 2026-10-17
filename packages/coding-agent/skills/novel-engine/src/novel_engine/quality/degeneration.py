# -*- coding: utf-8 -*-
"""Degenerate-output detection (CC round-7 P0-2).

The Agnes flash model occasionally answers a near-empty slacker response and, when
rescued at a *higher* temperature, collapses into a self-repetition loop (the same
~20+ char block emitted several times). These are pure string helpers so the rescue
ladder can be unit-tested offline without any LLM.
"""
from __future__ import annotations

import re

# A repeated substring of this many CJK chars is long enough that a coincidence is
# essentially impossible, so it is a reliable "degenerate loop" signal.
DEFAULT_SELF_NGRAM = 20


def normalize_for_repeat(text: str) -> str:
    """Collapse all whitespace so a paragraph repeated across blank lines still
    matches, but keep punctuation/content characters intact."""
    return re.sub(r"\s+", "", text or "")


def find_self_repetition(text: str, n: int = DEFAULT_SELF_NGRAM) -> tuple[bool, str]:
    """Return (True, sample) when any length-``n`` content substring appears at
    least twice in the same text; otherwise (False, "").

    Single linear pass with a sliding window → O(len) time and memory.
    """
    norm = normalize_for_repeat(text)
    if len(norm) < n + 1:
        return False, ""
    seen: set[str] = set()
    for i in range(len(norm) - n + 1):
        gram = norm[i:i + n]
        if gram in seen:
            return True, gram
        seen.add(gram)
    return False, ""


def is_near_empty(text: str, target: int, floor: int | None = None) -> bool:
    """Rescue trigger (tighter than the scene validation floor).

    CC round-7: a scene shorter than ``max(target*0.15, 150)`` chars is a near-empty
    slacker response that must enter the penalty-based rescue ladder rather than the
    normal 0.4-floor regeneration path.
    """
    threshold = int(floor if floor is not None else max(float(target) * 0.15, 150.0))
    return len(text or "") < threshold
