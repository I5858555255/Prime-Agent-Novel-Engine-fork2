from novel_engine.pipeline.quality_gate import evaluate_publish
from novel_engine.core.quality_policy import DEFAULT_POLICY

def test_93_with_leak_is_vetoed():
    r = evaluate_publish(score=93, reviewer_issues=[], det_hard=[], leak=["残留【test】"], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is False
    assert any("leak" in x or "泄漏" in x for x in r["reasons"])

def test_88_clean_publishes():
    r = evaluate_publish(score=88, reviewer_issues=[], det_hard=[], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True

def test_length_is_note_only():
    r = evaluate_publish(score=90, reviewer_issues=[], det_hard=["[长度] 超标"], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True
    assert "长度" in r["note"]

def test_dimension_fallback_matches_orchestrator_log():
    # 回退顺序 category → dimension → severity：hard 维度仅 high/medium 时阻断，
    # low 不阻断；note 维度无论 severity 均不阻断但 high 计 advisory。
    from novel_engine.pipeline.pipeline_orchestrator import _review_issue_is_blocking
    # scene_missing is a hard dimension with low severity → should NOT block
    issue = {"dimension": "scene_missing", "severity": "low", "description": "x"}
    r = evaluate_publish(score=90, reviewer_issues=[issue], det_hard=[], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True
    blocking, advisory = _review_issue_is_blocking(DEFAULT_POLICY, issue)
    assert blocking is False
    assert advisory is False


def test_reviewer_dimensions_blocking_behavior():
    # Verify severity-gated dimension→category mappings
    from novel_engine.pipeline.pipeline_orchestrator import _review_issue_is_blocking
    from novel_engine.core.quality_policy import DEFAULT_POLICY

    # Hard dimensions with high/medium severity block
    hard_issues_blocking = [
        {"dimension": "plot_consistency", "severity": "high"},
        {"dimension": "foreshadow_execution", "severity": "medium"},
    ]
    for issue in hard_issues_blocking:
        b, adv = _review_issue_is_blocking(DEFAULT_POLICY, issue)
        assert b is True
        assert adv is False

    # Hard dimensions with low severity do NOT block
    hard_issues_not_blocking = [
        {"dimension": "plot_consistency", "severity": "low"},
        {"dimension": "foreshadow_execution", "severity": "low"},
    ]
    for issue in hard_issues_not_blocking:
        b, adv = _review_issue_is_blocking(DEFAULT_POLICY, issue)
        assert b is False
        assert adv is False

    # Note dimensions with high severity do NOT block but generate advisory
    note_issues_high = [
        {"dimension": "character_consistency", "severity": "high"},
        {"dimension": "style_match", "severity": "high"},
        {"dimension": "pacing", "severity": "high"},
        {"dimension": "innovation", "severity": "high"},
        {"dimension": "hook_strength", "severity": "high"},
        {"dimension": "reader_retention", "severity": "high"},
        {"dimension": "cliffhensity", "severity": "high"},
    ]
    for issue in note_issues_high:
        b, adv = _review_issue_is_blocking(DEFAULT_POLICY, issue)
        assert b is False
        assert adv is True

    # Note dimensions with low/medium severity do NOT block and no advisory
    note_issues_low = [
        {"dimension": "character_consistency", "severity": "low"},
        {"dimension": "pacing", "severity": "medium"},
    ]
    for issue in note_issues_low:
        b, adv = _review_issue_is_blocking(DEFAULT_POLICY, issue)
        assert b is False
        assert adv is False

def test_evaluate_publish_with_hard_dimension():
    # plot_consistency=hard should veto publish even at high score
    issue = {"dimension": "plot_consistency", "severity": "high", "description": "plot drift"}
    r = evaluate_publish(score=90, reviewer_issues=[issue], det_hard=[], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is False

def test_evaluate_publish_with_note_dimension():
    # character_consistency=note should NOT veto publish at high score
    issue = {"dimension": "character_consistency", "severity": "high", "description": "character issue"}
    r = evaluate_publish(score=90, reviewer_issues=[issue], det_hard=[], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True

def test_forced_draft_rate_recorded_not_enforced(tmp_path):
    from novel_engine.pipeline.production_runner import summarize_batch
    rep = summarize_batch([{"published": True}, {"published": False, "draft": True}] * 5)
    assert rep["forced_draft_rate"] == 0.5
    assert rep["paused"] is False  # record-only during observation period

def test_force_best_lands_in_draft_and_counted():
    # Q5 pin: exhausted-best goes to chapters/draft/ (never novel/), with
    # published=False + force_published flag, and counts in forced_draft_rate.
    from novel_engine.pipeline.production_runner import summarize_batch
    forced = {"success": True, "published": False, "force_published": True,
              "note": "best 80 < 88 (hard gate [...])"}
    rep = summarize_batch([forced])
    assert rep["forced_drafts"] == 1
    assert rep["forced_draft_rate"] == 1.0
    assert rep["paused"] is False  # record-only during observation period
    import pathlib
    src = pathlib.Path("novel_engine/pipeline/pipeline_orchestrator.py").read_text(encoding="utf-8")
    assert "force_published" in src
    assert '"draft"' in src
    assert "force-best to draft" in src

def test_forced_draft_predicate_honors_success_and_published(tmp_path):
    from novel_engine.pipeline.production_runner import summarize_batch
    results = [
        {"success": True, "published": True},   # clean publish → not a draft
        {"success": True},                       # published best without flag → not a draft
        {"success": False},                      # failed chapter → draft
        {"success": False, "published": False},  # failed + unpublished → draft
        {"success": True, "published": False},   # unpublished despite success → draft
        {},                                      # missing keys → published (back-compat default)
    ]
    rep = summarize_batch(results)
    assert rep["forced_drafts"] == 3
    assert rep["forced_draft_rate"] == 0.5
    assert rep["paused"] is False
def test_hard_dimension_low_severity_not_blocking():
    # Hard dimension (plot_consistency) with low severity should NOT block
    from novel_engine.pipeline.pipeline_orchestrator import _review_issue_is_blocking
    issue = {"dimension": "plot_consistency", "severity": "low", "description": "minor plot issue"}
    b, adv = _review_issue_is_blocking(DEFAULT_POLICY, issue)
    assert b is False
    assert adv is False
    r = evaluate_publish(score=90, reviewer_issues=[issue], det_hard=[], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True


def test_note_dimension_high_severity_advisory_only():
    # Note dimension with high severity should NOT block but set advisory_high
    from novel_engine.pipeline.pipeline_orchestrator import _review_issue_is_blocking
    issue = {"dimension": "character_consistency", "severity": "high", "description": "character drift"}
    b, adv = _review_issue_is_blocking(DEFAULT_POLICY, issue)
    assert b is False
    assert adv is True
    r = evaluate_publish(score=90, reviewer_issues=[issue], det_hard=[], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True  # advisory only, does not block publish


def test_category_takes_priority_over_dimension():
    # When both category and dimension are present, category wins
    from novel_engine.pipeline.pipeline_orchestrator import _review_issue_is_blocking
    # category=hard dimension but severity=low → category says hard, but severity gate says no
    issue = {"category": "plot_consistency", "dimension": "style_match", "severity": "low", "description": "test"}
    b, adv = _review_issue_is_blocking(DEFAULT_POLICY, issue)
    assert b is False  # category "plot_consistency" maps to hard, but severity=low → no block
    assert adv is False


def test_mixed_issues_blocking_and_advisory():
    # Mixed hard+low and note+high: only hard+high/medium blocks
    from novel_engine.pipeline.pipeline_orchestrator import _review_issue_is_blocking
    issues = [
        {"dimension": "plot_consistency", "severity": "low", "description": "minor"},
        {"dimension": "character_consistency", "severity": "high", "description": "major"},
        {"dimension": "foreshadow_execution", "severity": "medium", "description": "mid"},
    ]
    blocking_count = sum(b for b, _ in (_review_issue_is_blocking(DEFAULT_POLICY, iss) for iss in issues))
    advisory_count = sum(adv for _, adv in (_review_issue_is_blocking(DEFAULT_POLICY, iss) for iss in issues))
    assert blocking_count == 1  # only foreshadow_execution+medium blocks
    assert advisory_count == 1  # only character_consistency+high is advisory


def test_evaluate_publish_with_note_high_dimension_allows_pass():
    # Multiple note+high issues should not prevent publish at sufficient score
    issues = [
        {"dimension": "character_consistency", "severity": "high", "description": "c1"},
        {"dimension": "style_match", "severity": "high", "description": "s1"},
    ]
    r = evaluate_publish(score=90, reviewer_issues=issues, det_hard=[], leak=[], violations=[], policy=DEFAULT_POLICY)
    assert r["publish"] is True
    # No blocking reasons should appear
    assert not any("reviewer_issue_blocking" in reason for reason in r["reasons"])
