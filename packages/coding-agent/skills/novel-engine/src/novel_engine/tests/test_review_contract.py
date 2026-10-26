"""Invalid review payloads must not become literary scores or use another project's rules."""
import json
from unittest.mock import Mock
import pytest
from novel_engine.agents.reviewer_agent import (
    DIM_MAX, ReviewerAgent, ReviewResponseError, _normalize_review, build_scene_boundaries,
)


@pytest.mark.parametrize('missing', list(DIM_MAX))
def test_missing_score_is_a_protocol_error_instead_of_zero(missing):
    scores = dict(DIM_MAX)
    scores.pop(missing)
    with pytest.raises(ReviewResponseError):
        _normalize_review({'scores': scores})


@pytest.mark.parametrize('bad', [None, True, 'unknown', float('nan'), float('inf'), -1, 26])
def test_non_numeric_non_finite_and_out_of_range_scores_are_rejected(bad):
    with pytest.raises(ReviewResponseError):
        _normalize_review({'scores': dict(DIM_MAX, plot_consistency=bad)})


@pytest.mark.parametrize('ratio,reported,expected', [(1, 1, 100), (0, 100, 0), (0.5, 99, 50)])
def test_model_supplied_total_never_overrides_dimension_arithmetic(ratio, reported, expected):
    review = _normalize_review({'scores': {k: v * ratio for k, v in DIM_MAX.items()},
                                'normalized_score': reported, 'total_score': 999})
    assert review['normalized_score'] == expected
    assert review['total_score'] == round(120 * ratio)


def test_review_uses_project_bible_policy_and_only_forbidden_list(tmp_path):
    bible = tmp_path / 'bible'
    bible.mkdir()
    (bible / 'character_bible.md').write_text('本项目人物甲。')
    (bible / 'style_bible.md').write_text('本项目风格甲。')
    (bible / 'author_intent.md').write_text('## 第一阶段：试验（第1-9章）\n\n'
        '- chapter_range: [1, 9]\n  theme: "合法主题"\n  forbidden:\n    - "禁止泄露真相甲"\n  note: "普通说明"\n')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/runtime_config.json').write_text(json.dumps({'quality_policy': {'publication_line': 97}}))
    agent = ReviewerAgent(llm_client=object(), project_root=tmp_path)
    block = agent._build_setting_block(1)
    assert '本项目人物甲' in block and '本项目风格甲' in block
    forbidden = block.split('【当前卷禁止事项】')[1].split('\n')[0]
    assert forbidden == '禁止泄露真相甲'
    assert agent.grade_review({'normalized_score': 95}) == 'fix'


def test_schema_error_gets_one_format_correction_and_raw_audit(tmp_path, monkeypatch):
    request = Mock(side_effect=[{'scores': {'plot_consistency': 25}}, {'scores': dict(DIM_MAX), 'issues': []}])
    monkeypatch.setattr('novel_engine.agents.reviewer_agent.call_llm', request)
    agent = ReviewerAgent(llm_client=object(), project_root=tmp_path)
    review = agent.review_chapter(1, {}, {}, '陈老根推开木门。', {})
    assert review['normalized_score'] == 100
    assert request.call_count == 2
    assert all(c.kwargs['temperature'] == 0 for c in request.call_args_list)
    assert '上次响应格式无效' in request.call_args.kwargs['prompt']
    audits = [json.loads(p.read_text()) for p in (tmp_path / 'audit/reviews').glob('*.json')]
    assert len(audits) == 2
    assert sum('technical_error' in a for a in audits) == 1
    assert len({a['text_hash'] for a in audits}) == 1
    assert len({a['input_hash'] for a in audits}) == 2


def test_two_invalid_responses_stop_without_scoring(tmp_path, monkeypatch):
    request = Mock(return_value={'scores': {'plot_consistency': 25}})
    monkeypatch.setattr('novel_engine.agents.reviewer_agent.call_llm', request)
    with pytest.raises(ReviewResponseError):
        ReviewerAgent(llm_client=object(), project_root=tmp_path).review_chapter(1, {}, {}, '正文。', {})
    assert request.call_count == 2


def test_valid_low_review_is_not_retried_to_get_a_better_score(tmp_path, monkeypatch):
    request = Mock(return_value={'scores': {k: v * 0.4 for k, v in DIM_MAX.items()}, 'issues': []})
    monkeypatch.setattr('novel_engine.agents.reviewer_agent.call_llm', request)
    review = ReviewerAgent(llm_client=object(), project_root=tmp_path).review_chapter(1, {}, {}, '正文。', {})
    assert review['normalized_score'] == 40
    request.assert_called_once()


def test_scene_boundaries_match_actual_words_after_punctuation_changes():
    scenes = [{'scene_id': 1, 'scene_text': '陈老根推开木门，提灯走到院子里。'},
              {'scene_id': 2, 'scene_text': '婴儿突然哭了起来。院外传来了脚步声。'}]
    text = '陈老根推开木门。\n提灯走到院子里。\n\n婴儿突然哭了起来。院外传来了脚步声。'
    ranges = build_scene_boundaries(text, scenes)
    assert [r['scene_id'] for r in ranges] == [1, 2]
    assert ranges[0]['end'] <= ranges[1]['start']
    assert text[ranges[1]['start']:].startswith('婴儿突然哭了起来')
    assert build_scene_boundaries(text, [dict(scenes[0], scene_text='已经发生了完全不同的事情。')]) == []


def test_review_prompt_has_no_concrete_deduction_example_from_this_book():
    assert '雾中围观/火把/红眼' not in ReviewerAgent.SYSTEM_PROMPT
    assert '把场景3推进到陈老根出场' not in ReviewerAgent.SYSTEM_PROMPT


@pytest.mark.parametrize('limit,completed', [(1, False), (2, True)])
def test_format_correction_shares_the_chapter_review_call_budget(tmp_path, monkeypatch, limit, completed):
    from types import SimpleNamespace
    from novel_engine.core.execution import Execution, BudgetExceeded
    owner = SimpleNamespace(config={'pipeline': {'attempt_budget': {'max_review_calls': limit}}}, root=tmp_path)
    execution = Execution(owner, 1)
    client = SimpleNamespace(_chapter_execution=execution)
    execution.budget.review(vote=True)
    request = Mock(side_effect=[{'scores': {}}, {'scores': dict(DIM_MAX), 'issues': []}])
    monkeypatch.setattr('novel_engine.agents.reviewer_agent.call_llm', request)
    agent = ReviewerAgent(llm_client=client, project_root=tmp_path)
    if completed:
        assert agent.review_chapter(1, {}, {}, '正文。', {})['normalized_score'] == 100
    else:
        with pytest.raises(BudgetExceeded):
            agent.review_chapter(1, {}, {}, '正文。', {})
    assert request.call_count == limit
    assert execution.budget.review_calls == limit


def test_editing_bible_invalidates_review_context_fingerprint(tmp_path):
    from novel_engine.agents.reviewer_agent import review_context_fingerprint
    bible = tmp_path / 'bible'
    bible.mkdir()
    target = bible / 'style_bible.md'
    target.write_text('风格甲。')
    before = review_context_fingerprint(tmp_path, {'chapter_num': 1})
    assert review_context_fingerprint(tmp_path, {'chapter_num': 1}) == before
    target.write_text('风格乙。')
    assert review_context_fingerprint(tmp_path, {'chapter_num': 1}) != before
