"""Failure triage for a validator-driven repair campaign.

Classify a validation error string into one of 6 buckets so the runner can
pick the fix branch and the attempt budget. Pure function of the error text;
no filesystem coupling. The 6 buckets and their budgets are documented in
SKILL.md; this module is the single source of truth for the classification.

Usage:
    from triage import classify
    bucket = classify("场景S2扩写指引含禁词['调息']")
    # -> "forbidden_term"
"""
from __future__ import annotations

import re

# Ordered rule list: first match wins. Keep specific patterns first; the
# structural catch-all (缺少 / 场景 / 事件 / beat / scene) is last.
_RULES: list[tuple[str, "re.Pattern[str]"]] = [
    ("forbidden_term", re.compile(r"禁词|forbidden", re.I)),
    ("cast", re.compile(r"缺少人物|对话意图|cast|人物", re.I)),
    ("budget", re.compile(r"扩写预算|expansion|预算|budget", re.I)),
    ("title", re.compile(r"标题|title", re.I)),
    ("structural", re.compile(r"缺少|broken|场景|事件|coverage|beat|scene", re.I)),
]


def classify(error_text) -> str:
    """Map a validation error string (or a list of them) to a triage bucket.

    Returns one of: forbidden_term, cast, budget, title, structural, unknown.
    `unknown` is the default and gets the smallest attempt budget.
    """
    if isinstance(error_text, (list, tuple)):
        error_text = " | ".join(str(e) for e in error_text)
    t = str(error_text)
    for name, pat in _RULES:
        if pat.search(t):
            return name
    return "unknown"


def classify_failure(failure: dict | None) -> str:
    """Classify from a failure record (a dict with chapter + errors/reason)."""
    if not failure:
        return "unknown"
    text = " ".join(str(v) for v in failure.values() if isinstance(v, str))
    errs = failure.get("errors") or failure.get("reason") or ""
    if isinstance(errs, list):
        errs = " | ".join(str(e) for e in errs)
    return classify(text + " " + str(errs))


def attempt_budget(bucket: str) -> int:
    """Number of fix attempts allowed for a bucket before the stop-fix-restart
    loop hands off to the operator. `unknown` is capped low deliberately."""
    return 2 if bucket == "unknown" else 12
