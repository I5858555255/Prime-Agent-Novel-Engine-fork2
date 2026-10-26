"""Localized repair preserves unedited prose and journals only validated candidates."""
from unittest.mock import Mock
import pytest
from novel_engine.core.execution import BudgetExceeded
from novel_engine.agents.scene_schema import SceneOutput
from novel_engine.pipeline.chapter_journal import append_scene, load_authoritative_scenes
from novel_engine.tests.test_commit_path import _make_orch

ANCHOR = '他盯着树梢的红光，却没有注意到脚边的孩子。'
REPLACEMENT = '陈老根放下提灯，俯身确认孩子仍有呼吸。'


@pytest.mark.parametrize('anchor,replacement,hard_pass,adopted', [
    (ANCHOR, REPLACEMENT, True, True),
    ('银色战舰停泊月球基地。', REPLACEMENT, True, False),
    (ANCHOR, 'A foreign sentence ends here.', True, False),
    (ANCHOR, REPLACEMENT, False, False)])
def test_paragraph_edit_validates_before_journal_and_preserves_other_prose(tmp_path, monkeypatch, anchor, replacement, hard_pass, adopted):
    obj = _make_orch(tmp_path)
    obj.config['llm']['use_mock'] = False
    original = ANCHOR + '\n' + '他站在门边。' * 1600
    scene = SceneOutput(1, original, '原钩子')
    obj.writer.last_scenes = [scene]
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original, 'hook': '原钩子', 'beats': ['确认气息']})
    obj._deterministic_quality_gate = Mock(return_value={'passed': hard_pass, 'issues': []})
    request = Mock(return_value={'edits': [{'span_id': 0 if anchor == ANCHOR else 99999, 'replacement': replacement}]})
    monkeypatch.setattr('novel_engine.pipeline.pipeline_orchestrator.call_llm', request)
    card = {'chapter_num': 1, 'scene_blueprints': [{'scene_num': 1, 'characters': ['C002'],
            'concrete_events': ['按既定蓝图确认呼吸，不取出残页']}]}
    outcome = obj._repair_review_spans(1, card, {'issues': [{'dimension': 'plot_consistency', 'scene_ids': [1], 'description': '确认婴儿呼吸'}]})
    expected = original.replace(ANCHOR, REPLACEMENT) if adopted else original
    assert bool(outcome) == adopted
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == expected
    assert load_authoritative_scenes(tmp_path, 1)[0]['hook'] == '原钩子'
    assert scene.scene_text == expected
    if adopted:
        assert outcome[0] == expected
        assert expected.endswith('他站在门边。' * 1600)
    request.assert_called_once()
    assert '按既定蓝图确认呼吸，不取出残页' in request.call_args.args[0]
    obj.close()


def test_paragraph_repair_budget_exception_propagates(tmp_path, monkeypatch):
    obj = _make_orch(tmp_path)
    obj.config['llm']['use_mock'] = False
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': ANCHOR})
    monkeypatch.setattr('novel_engine.pipeline.pipeline_orchestrator.call_llm', Mock(side_effect=BudgetExceeded('quota')))
    with pytest.raises(BudgetExceeded):
        obj._repair_review_spans(1, {}, {'issues': [{'description': '修复因果'}]})
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == ANCHOR
    obj.close()
