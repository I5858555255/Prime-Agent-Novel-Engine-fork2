"""Review must see omitted middle scenes and ground punctuation claims in exact text."""
from unittest.mock import patch
from novel_engine.agents.reviewer_agent import ReviewerAgent, DIM_MAX


def test_review_receives_entire_chapter_and_measured_punctuation():
    reviewer = ReviewerAgent(llm_client=object())
    reviewer._build_setting_block = lambda chapter: ''
    text = '陈老根停住脚步。' * 400 + '独有中部伏笔已经执行。' + '他把木门关好。' * 800
    assert len(text) > 7500
    with patch('novel_engine.agents.reviewer_agent.call_llm', return_value={'scores': DIM_MAX.copy(), 'issues': []}) as call:
        reviewer.review_chapter(1, {}, {}, text, {})
    prompt = call.call_args.kwargs['prompt']
    assert text in prompt
    assert '独有中部伏笔已经执行。' in prompt
    assert '中部省略' not in prompt
    assert '"long_sentence_count": 0' in prompt
    assert '"unhealthy_paragraph_count": 0' in prompt


def test_review_keeps_real_long_sentence_evidence_without_changing_scores():
    reviewer = ReviewerAgent(llm_client=object())
    reviewer._build_setting_block = lambda chapter: ''
    text = '山风吹过门前的老树枝叶摇晃屋里的灯火忽明忽暗陈老根仍然站在窗边听着远处传来的脚步声没有移动。'
    scores = {name: maximum / 2 for name, maximum in DIM_MAX.items()}
    with patch('novel_engine.agents.reviewer_agent.call_llm', return_value={'scores': scores.copy(), 'issues': []}) as call:
        review = reviewer.review_chapter(1, {}, {}, text, {})
    prompt = call.call_args.kwargs['prompt']
    assert '"long_sentence_count": 1' in prompt
    assert text in prompt
    assert review['normalized_score'] == 50
    assert review['scores'] == scores
