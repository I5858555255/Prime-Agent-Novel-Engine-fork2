"""Publish decision logic for chapter generation.

Encapsulates the decision of whether a chapter can be published based on
score, deterministic gate issues, leak issues, and forbidden violations.
"""
from __future__ import annotations

import logging
from pathlib import Path

from novel_engine.core.quality_policy import load_quality_policy, is_blocking
from novel_engine.pipeline.quality_gate import evaluate_publish

logger = logging.getLogger(__name__)


def decide_can_publish(
    score: float,
    review: dict,
    det_issues: list,
    leak_issues: list,
    violations: list,
    policy: dict | None = None,
) -> tuple[bool, dict]:
    """Decide whether a chapter can be published.

    Returns (can_publish, verdict_dict).
    """
    if policy is None:
        policy = load_quality_policy(Path.cwd())
    
    publication_line = int(policy['publication_line'])
    has_high_issue = any(
        is_blocking(policy, iss.get('dimension') or iss.get('severity', ''))
        for iss in (review.get('issues') or [])
    )
    
    # Gray-band release (85-87.9) with gates passed and no high issues
    _soft_line = int(policy.get('soft_publication_line', publication_line - 3))
    # Note: gray_band_release should be set by caller if applicable
    
    # Use evaluate_publish from quality_gate for the main decision
    verdict = evaluate_publish(
        score=score,
        reviewer_issues=review.get('issues', []),
        det_hard=det_issues,
        leak=leak_issues,
        violations=violations,
        policy=policy,
        dim_scores=review.get('dim_scores', {}),
        total_score=score,
    )
    
    can_publish = verdict['publish']
    return can_publish, verdict

