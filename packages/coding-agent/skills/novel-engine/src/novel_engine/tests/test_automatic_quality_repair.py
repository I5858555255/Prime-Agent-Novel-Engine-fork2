"""Automatic quality remediation without human queues or relaxed publication gates."""
import json
from unittest.mock import Mock

import pytest

from novel_engine.pipeline.chapter_status import get_status, HALTED
from novel_engine.quality.review_policy import review_on_demand
from novel_engine.tests.test_commit_path import _make_orch


def outcome(score=92, unstable=False, issues=None, deterministic=False):
    return {"score": score, "verdict": "pass" if score >= 88 else "fix",
            "review_unstable": unstable,
            "review": {"normalized_score": score, "issues": issues or [],
                       "deterministic_only": deterministic}}


def prepare(tmp_path, reviews):
    obj = _make_orch(tmp_path)
    obj._enforce_word_count = lambda text, lo, hi: text
    obj.current_novel = "初稿正文。" * 50
    obj._draft_novel = obj.current_novel
    obj._stage_review = Mock(side_effect=reviews)
    obj._rewrite_weak_dimensions = Mock(return_value="后备修复正文。" * 50)
    def repair(*args):
        obj.current_novel += "本轮已经修复具体问题。"
        obj._draft_novel = obj.current_novel
        return (*args[3:6], args[6], None)
    obj._dispatch_remediation = Mock(side_effect=repair)
    return obj


@pytest.mark.parametrize("first", [outcome(92, True), outcome(83), outcome(0, deterministic=True),
    outcome(94, issues=[{"severity": "high", "dimension": "plot_consistency", "description": "地点矛盾"}])])
def test_quality_failures_repair_changed_text_then_recheck(tmp_path, first):
    obj = prepare(tmp_path, [first, outcome()])
    result = {}
    staged = obj._auto_repair_quality(1, {}, {}, {}, result)
    assert staged['auto_quality_passed']
    assert len(result['quality_repairs']) == 2
    assert obj._stage_review.call_args_list[0].args[3] != obj._stage_review.call_args_list[1].args[3]
    assert obj._dispatch_remediation.call_count == 1
    assert not (tmp_path / 'audit/needs_human_review.json').exists()
    obj.close()


def test_still_unstable_at_high_score_never_passes_and_stops_at_limit(tmp_path):
    obj = prepare(tmp_path, [outcome(99, True)] * 3)
    result = {}
    staged = obj._auto_repair_quality(1, {}, {}, {}, result)
    assert not staged['auto_quality_passed']
    assert obj._dispatch_remediation.call_count == 2
    assert obj._stage_review.call_count == 3
    obj.close()


def test_unchanged_failed_text_is_not_reviewed_again(tmp_path):
    obj = prepare(tmp_path, [outcome(70)])
    obj._dispatch_remediation = Mock(side_effect=lambda *a: (a[3], a[4], a[5], a[6], None))
    obj._rewrite_weak_dimensions.return_value = obj.current_novel
    assert not obj._auto_repair_quality(1, {}, {}, {}, {})['auto_quality_passed']
    assert obj._stage_review.call_count == 1
    assert obj._rewrite_weak_dimensions.call_count == 1
    obj.close()


def test_global_repair_fallback_is_reviewed_before_acceptance(tmp_path):
    obj = prepare(tmp_path, [outcome(70), outcome()])
    obj._dispatch_remediation = Mock(side_effect=lambda *a: (a[3], a[4], a[5], a[6], None))
    assert obj._auto_repair_quality(1, {}, {}, {}, {})['auto_quality_passed']
    assert obj._stage_review.call_args_list[1].args[3] == obj._rewrite_weak_dimensions.return_value
    obj.close()


def test_hard_gate_cannot_be_overridden_by_high_score(tmp_path):
    obj = prepare(tmp_path, [outcome(99)] * 3)
    obj._deterministic_quality_gate = Mock(return_value={'passed': False, 'issues': ['时间线越界']})
    assert not obj._auto_repair_quality(1, {}, {}, {}, {})['auto_quality_passed']
    obj.close()


def test_unstable_vote_issues_are_merged_for_remediation():
    votes = iter([outcome(86)['review'], outcome(52, issues=[{'description': '伏笔缺失'}])['review'],
                  outcome(92, issues=[{'description': '道具矛盾'}])['review']])
    result = review_on_demand(None, lambda: next(votes), lambda r: 'fix',
                             {'publication_line': 88, 'soft_publication_line': 85}, {})
    assert result['review_unstable']
    assert {i['description'] for i in result['review']['issues']} == {'伏笔缺失', '道具矛盾'}


def test_failure_is_saved_locally_without_human_queue_or_publication(tmp_path):
    obj = _make_orch(tmp_path)
    obj._enforce_word_count = lambda text, lo, hi: text
    obj._stage_review = Mock(return_value=outcome(71, True))
    obj._dispatch_remediation = Mock(side_effect=lambda *a: (a[3], a[4], a[5], a[6], None))
    obj._rewrite_weak_dimensions = Mock(return_value=None)
    result = obj.generate_single_chapter(1)
    assert not result['success'] and not result['published']
    assert result['quality_repair_exhausted'] and not result['pending_human_review']
    assert get_status(tmp_path, 1) == HALTED
    assert obj.state_machine.current_phase.value == 'FAIL'
    assert not (tmp_path / 'audit/needs_human_review.json').exists()
    assert not (tmp_path / 'chapters/novel/chapter_1.txt').exists()
    assert json.loads(open(result['quality_diagnostic_path']).read())['repairs']
    assert open(result['draft_path']).read()
    assert obj.generate_single_chapter(2)['predecessor_blocked']
    obj.close()


def test_budget_exhaustion_preserves_failed_draft_without_new_budget(tmp_path):
    obj = _make_orch(tmp_path)
    obj._enforce_word_count = lambda text, lo, hi: text
    obj._stage_review = Mock(return_value=outcome(71))
    def exhaust(*args):
        from novel_engine.core.execution import current_execution
        current_execution(obj).budget._stop('test repair quota exhausted')
    obj._dispatch_remediation = Mock(side_effect=exhaust)
    result = obj.generate_single_chapter(1)
    assert result['budget_exhausted'] and not result['pending_human_review']
    assert result['quality_repair_exhausted']
    assert result['score'] == 71
    assert result['quality_repairs'][0]['score'] == 71
    diagnostic = json.loads(open(result['quality_diagnostic_path']).read())
    assert diagnostic['score'] == 71 and diagnostic['repairs']
    assert get_status(tmp_path, 1) == HALTED
    assert obj.state_machine.current_phase.value == 'FAIL'
    assert open(result['draft_path']).read()
    obj.close()


def test_repaired_chapter_reaches_formal_publication(tmp_path):
    from novel_engine.core.state_machine import ChapterPhase
    obj = _make_orch(tmp_path)
    obj._enforce_word_count = lambda text, lo, hi: text
    reviews = iter([outcome(93, True), outcome(93)])
    def review(*args):
        if obj.state_machine.current_phase != ChapterPhase.REVIEW:
            obj.state_machine.transition(ChapterPhase.REVIEW)
        return next(reviews)
    obj._stage_review = Mock(side_effect=review)
    def repair(*args):
        obj.current_novel += "陈老根把婴儿抱进屋，关好木门。"
        obj._draft_novel = obj.current_novel
        return (obj.current_novel, obj._draft_novel, args[5], args[6], None)
    obj._dispatch_remediation = Mock(side_effect=repair)
    result = obj.generate_single_chapter(1)
    assert result['success'] and result['published'], result
    assert obj.checkpoint_mgr.verify_published(1)
    assert "陈老根把婴儿抱进屋" in (tmp_path / 'chapters/novel/chapter_1.txt').read_text()
    assert obj._dispatch_remediation.call_count == 1
    obj.close()


def test_exhausted_repairs_do_not_start_outer_infrastructure_retry(tmp_path):
    obj = _make_orch(tmp_path)
    obj.generate_single_chapter = Mock(return_value={'success': False, 'quality_repair_exhausted': True})
    result = obj._run_with_retry(1)
    assert result['quality_repair_exhausted']
    assert obj.generate_single_chapter.call_count == 1
    obj.close()


@pytest.mark.parametrize('message', ['Task card validation failed: inconsistent scene times',
    'Call 2 produced 0 scenes (expected 4) after feedback retry'])
def test_planning_validation_uses_bounded_replan_without_human_handoff(tmp_path, message):
    obj = _make_orch(tmp_path)
    obj._frozen_task_cards.clear()
    obj._stage_directing = Mock(side_effect=ValueError(message))
    result = obj._run_with_retry(1)
    assert obj._stage_directing.call_count == 2
    assert result['quality_repair_exhausted'] and not result['pending_human_review']
    assert obj.state_machine.current_phase.value == 'FAIL'
    assert not (tmp_path / 'audit/needs_human_review.json').exists()
    obj.close()


def test_continuity_gap_with_replan_receives_one_new_plan(tmp_path):
    obj = _make_orch(tmp_path)
    calls = []
    def generate(chapter):
        calls.append(chapter)
        if len(calls) == 1:
            return {'success': False, 'quality_replan': True, 'quality_gap_continue': True,
                    'errors': ['第三场不可提前抱起婴儿']}
        assert '第三场不可提前抱起婴儿' in obj._planning_feedback[chapter]
        assert not obj._boundary_hard
        return {'success': True}
    obj.generate_single_chapter = generate
    obj._boundary_hard = ['obsolete first-plan issue']
    result = obj._run_with_retry(1)
    assert result['success'] and len(calls) == 2
    archived = list((tmp_path / 'runtime/replans').glob('*/planning.json'))
    assert len(archived) == 1
    assert json.loads(archived[0].read_text())['task_card']['core_goal'] == 'test goal'
    obj.close()


def test_replanned_card_is_saved_for_restart_and_receives_failure_feedback(tmp_path):
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    from novel_engine.core.state_machine import ChapterPhase
    obj = _make_orch(tmp_path)
    obj._global_target = 10000
    obj._planning_feedback = {1: ['第三场不得提前抱起婴儿']}
    obj.state_machine.current_chapter = 1
    obj.state_machine.current_phase = ChapterPhase.WORLD_SIM
    obj._phase_cache.get = Mock()
    obj.director.generate_task_card_cached = Mock(return_value={
        'chapter_num': 1, 'core_goal': '新规划', 'scene_blueprints': [
            {'scene_num': n, 'goal': '推进事件', 'word_count_target': 2500, 'location': '村口', 'beats': ['观察']} for n in range(1, 5)]})
    obj.director.validate_task_card = Mock(return_value=[])
    card = PipelineOrchestrator._stage_directing(obj, 1, {}, bypass_cache=True)
    obj._phase_cache.get.assert_not_called()
    assert obj.director.generate_task_card_cached.call_args.kwargs['feedback'] == ['第三场不得提前抱起婴儿']
    files = list((tmp_path / 'cache/chapter_1').glob('director_*.json'))
    assert len(files) == 1
    assert json.loads(files[0].read_text())['payload']['core_goal'] == card['core_goal'] == '新规划'
    obj.close()


def test_paragraph_fallback_is_reviewed_without_full_rewrite(tmp_path):
    obj = prepare(tmp_path, [outcome(70), outcome()])
    obj._dispatch_remediation = Mock(side_effect=lambda *a: (a[3], a[4], a[5], a[6], None))
    revised = obj.current_novel + '陈老根俯身确认呼吸。'
    obj._repair_review_spans = Mock(return_value=(revised, revised))
    assert obj._auto_repair_quality(1, {}, {}, {}, {})['auto_quality_passed']
    obj._repair_review_spans.assert_called_once()
    obj._rewrite_weak_dimensions.assert_not_called()
    assert obj._stage_review.call_args_list[1].args[3] == revised
    obj.close()


def test_failed_paragraph_pass_escalates_to_scene_repair(tmp_path):
    obj = prepare(tmp_path, [outcome(70), outcome(69), outcome(92)])
    revised = obj.current_novel + '局部修订未能解决节奏问题。'
    obj._repair_review_spans = Mock(return_value=(revised, revised))
    assert obj._auto_repair_quality(1, {}, {}, {}, {})['auto_quality_passed']
    obj._repair_review_spans.assert_called_once()
    obj._dispatch_remediation.assert_called_once()
    assert '本轮已经修复具体问题' in obj._stage_review.call_args_list[2].args[3]
    obj.close()


def test_exhausted_repairs_keep_best_reviewed_text_and_matching_journal(tmp_path):
    from novel_engine.pipeline.chapter_journal import append_scene, load_authoritative_scenes
    obj = prepare(tmp_path, [outcome(80), outcome(70), outcome(75)])
    original = obj.current_novel
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original})
    def repair(*args):
        obj.current_novel += '新的修订内容。'
        obj._draft_novel = obj.current_novel
        append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': obj.current_novel})
        return (*args[3:6], args[6], None)
    obj._dispatch_remediation = Mock(side_effect=repair)
    result = {}
    staged = obj._auto_repair_quality(1, {}, {}, {}, result)
    assert not staged['auto_quality_passed'] and staged['score'] == 80
    assert result['best_candidate_retained']
    assert obj.current_novel == original and obj._draft_novel == original
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == original
    assert [r['score'] for r in result['quality_repairs']] == [80, 70, 75]
    obj.close()


def test_repair_budget_exception_restores_last_reviewed_candidate(tmp_path):
    from novel_engine.core.execution import BudgetExceeded
    from novel_engine.pipeline.chapter_journal import append_scene, load_authoritative_scenes
    obj = prepare(tmp_path, [outcome(80)])
    original = obj.current_novel
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original})
    def repair(*args):
        obj.current_novel = '尚未经过审查的候选稿。'
        append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': obj.current_novel})
        raise BudgetExceeded('quota')
    obj._dispatch_remediation = repair
    with pytest.raises(BudgetExceeded):
        obj._auto_repair_quality(1, {}, {}, {}, {})
    assert obj.current_novel == original
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == original
    obj.close()


def test_invalid_review_protocol_does_not_regenerate_chapter_or_request_human(tmp_path):
    from novel_engine.agents.reviewer_agent import ReviewResponseError
    obj = _make_orch(tmp_path)
    obj._enforce_word_count = lambda text, *args: text
    obj._stage_review = Mock(side_effect=ReviewResponseError('missing dimension'))
    result = obj._run_with_retry(1)
    assert result['review_protocol_failed'] and result['quality_repair_exhausted']
    assert not result['pending_human_review'] and not result['success']
    obj._stage_review.assert_called_once()
    assert open(result['draft_path']).read()
    obj.close()
