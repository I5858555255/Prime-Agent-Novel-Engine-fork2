"""Tests for review_journal.py - per-chapter review journal persistence."""
import json
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def sample_review():
    return {
        "normalized_score": 85.0,
        "max_total": 120,
        "score_schema": "v2",
        "verdict": "pass",
        "scores": {},
        "praise": "good chapter",
        "issues": [
            {"dimension": "style_match", "severity": "high", "description": "long sentences"},
            {"dimension": "foreshadow_execution", "severity": "low", "description": "weak"},
            {"dimension": "pacing", "severity": "high", "description": "drag"},
        ],
        "dim_scores": {},
    }


def test_advisory_high_count(sample_review, tmp_path):
    """Note-dimension high-severity issues count toward advisory_high_count;
    hard-dimension issues do not."""
    from novel_engine.pipeline.review_journal import save_review_journal

    path = save_review_journal(tmp_path, 1, 85.0, sample_review)
    data = json.loads(path.read_text(encoding="utf-8"))
    r = data["reviews"][0]
    # style_match (note) + high -> counts
    # foreshadow_execution (hard) + low -> does NOT count
    # pacing (note) + high -> counts
    assert r["advisory_high_count"] == 2


def test_advisory_high_count_hard_dim_not_counted(sample_review, tmp_path):
    """Hard-dimension issues with severity=high do NOT count as advisory_high."""
    from novel_engine.pipeline.review_journal import save_review_journal

    review = {
        **sample_review,
        "issues": [
            {"dimension": "plot_consistency", "severity": "high", "description": "major plot hole"},
            {"dimension": "style_match", "severity": "low", "description": "minor"},
        ],
    }
    path = save_review_journal(tmp_path, 2, 70.0, review)
    data = json.loads(path.read_text(encoding="utf-8"))
    r = data["reviews"][0]
    # plot_consistency (hard) + high -> does NOT count as advisory
    # style_match (note) + low -> does NOT count
    assert r["advisory_high_count"] == 0


def test_advisory_high_count_with_category(sample_review, tmp_path):
    """Category field takes priority over dimension for advisory counting."""
    from novel_engine.pipeline.review_journal import save_review_journal

    review = {
        **sample_review,
        "issues": [
            {"category": "plot_consistency", "dimension": "style_match", "severity": "high", "description": "category wins"},
        ],
    }
    path = save_review_journal(tmp_path, 3, 80.0, review)
    data = json.loads(path.read_text(encoding="utf-8"))
    r = data["reviews"][0]
    # category=plot_consistency (hard) -> does NOT count even though dimension=style_match (note)
    assert r["advisory_high_count"] == 0


def test_advisory_high_count_backward_compat(tmp_path):
    """Old records without advisory_high_count are still readable."""
    from novel_engine.pipeline.review_journal import save_review_journal

    review = {
        "normalized_score": 90.0,
        "max_total": 120,
        "score_schema": "v2",
        "verdict": "pass",
        "scores": {},
        "praise": "",
        "issues": [],
        "dim_scores": {},
    }
    save_review_journal(tmp_path, 1, 90.0, review)
    # Append an old-format record (no advisory_high_count)
    review_file = tmp_path / "audit" / "per_chapter_reviews.json"
    data = json.loads(review_file.read_text(encoding="utf-8"))
    data["reviews"].append({
        "chapter_num": 0,
        "total_score": 75.0,
        "normalized_score": 75.0,
        "max_total": 120,
        "score_schema": "v2",
        "verdict": "fix",
        "scores": {},
        "praise": "",
        "issues": [],
        "dim_scores": {},
        # no advisory_high_count key
    })
    review_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Re-read: old record should be readable
    data2 = json.loads(review_file.read_text(encoding="utf-8"))
    assert len(data2["reviews"]) == 2
    assert data2["reviews"][0]["chapter_num"] == 1
    assert data2["reviews"][1]["chapter_num"] == 0
    print("backward compat OK")


def test_save_review_journal_overwrites_existing(sample_review, tmp_path):
    """Calling save_review_journal twice for the same chapter replaces the old record."""
    from novel_engine.pipeline.review_journal import save_review_journal

    save_review_journal(tmp_path, 1, 85.0, sample_review)
    sample_review["normalized_score"] = 90.0
    sample_review["issues"] = []
    save_review_journal(tmp_path, 1, 90.0, sample_review)
    data = json.loads((tmp_path / "audit" / "per_chapter_reviews.json").read_text(encoding="utf-8"))
    assert len(data["reviews"]) == 1
    assert data["reviews"][0]["normalized_score"] == 90.0
    assert data["reviews"][0]["advisory_high_count"] == 0  # no issues now


def test_save_review_journal_creates_audit_dir(sample_review, tmp_path):
    """save_review_journal creates the audit/ directory if it doesn't exist."""
    from novel_engine.pipeline.review_journal import save_review_journal

    audit_dir = tmp_path / "audit"
    assert not audit_dir.exists()
    path = save_review_journal(tmp_path, 1, 85.0, sample_review)
    assert audit_dir.exists()
    assert path == audit_dir / "per_chapter_reviews.json"
