"""Per-chapter review journal: append review data to audit/per_chapter_reviews.json.

Called at the end of each chapter generation to persist review scores,
issues, and verdicts for sliding-window quality memory.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from novel_engine.agents.reviewer_agent import DIM_MAX

logger = logging.getLogger(__name__)

# Mirror of DEFAULT_POLICY severity_map from quality_policy.py, used here
# to avoid importing pipeline_orchestrator (which pulls in httpx/LLMClient).
_ADVISORY_MAP = {
    "leak_scaffolding": "hard", "truncation": "hard",
    "verbatim_duplication": "hard", "forbidden_block": "hard",
    "scene_missing": "hard", "plot_consistency": "hard",
    "foreshadow_execution": "hard",
    "length_deviation": "note", "beat_repetition_thematic": "note",
    "hook_missing": "note", "character_consistency": "note",
    "style_match": "note", "pacing": "note", "innovation": "note",
    "hook_strength": "note", "reader_retention": "note",
    "cliffhensity": "note",
}


def save_review_journal(root, chapter_num: int, score: float, review: dict) -> Path:
    """Append one chapter's review to per_chapter_reviews.json.

    Returns the path written (creates parent dirs as needed).
    """
    review_file = Path(root) / 'audit' / 'per_chapter_reviews.json'
    review_file.parent.mkdir(parents=True, exist_ok=True)
    if review_file.exists():
        try:
            existing = json.loads(review_file.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, ValueError):
            existing = {'reviews': []}
    else:
        existing = {'reviews': []}
    existing['reviews'] = [r for r in existing.get('reviews', []) if r.get('chapter_num') != chapter_num]
    # Count note-dimension issues with severity=high (advisory_high signal).
    _advisory_high_count = sum(
        1 for iss in (review.get('issues') or [])
        if _ADVISORY_MAP.get(iss.get('category') or iss.get('dimension'), 'note') == 'note'
        and iss.get('severity', '').lower() == 'high'
    )
    existing['reviews'].append({
        'chapter_num': chapter_num,
        'total_score': score,
        'normalized_score': review.get('normalized_score', score),
        'max_total': review.get('max_total', sum(DIM_MAX.values())),
        'score_schema': review.get('score_schema', 'v2'),
        'verdict': review.get('verdict', ''),
        'scores': review.get('scores', {}),
        'praise': review.get('praise', ''),
        'issues': review.get('issues', []),
        'dim_scores': review.get('dim_scores', {}),
        'advisory_high_count': _advisory_high_count,
    })
    review_file.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding='utf-8')
    return review_file
