"""Real-run regression: metadata must retain canonical foreshadow and state identities."""
from unittest.mock import Mock
import pytest
from novel_engine.agents.chapter_director import ChapterDirector


def metadata_prompt(chapter, foreshadows):
    director = ChapterDirector.__new__(ChapterDirector)
    director._check_budget = Mock()
    director._call_with_meta = Mock(return_value=({'chapter_num': chapter}, 'stop', {}))
    context = {'constraints': '', 'foreshadows': foreshadows,
               'world_state': {'characters': {'C002': {'name': '陈老根', 'realm': '凡人', 'location': '村口'}}}}
    director._call_chapter_metadata(chapter, context, '', [
        {'scene_num': 1, 'goal': '发现婴孩', 'location': '村外', 'characters': ['C002'],
         'concrete_events': [{'event': '陈老根寻婴', 'observable_action': '提灯拨开雾气'}],
         'narrative_time': '子时', 'latest_permissible_moment': '寅时'}])
    return director._call_with_meta.call_args.args[0]


def test_metadata_uses_due_canonical_foreshadow_without_future_revelations():
    prompt = metadata_prompt(1, [{'id': 'F004', 'plant_chapter': 1,
        'plant_context': '迷雾边缘降生，引发村民恐慌', 'resolve_method': '未来秘密不得提前写入',
        'clue_plan': [{'chapter': 5, 'method': '未来章节提示'}]}])
    assert 'F004' in prompt and '迷雾边缘降生' in prompt
    assert 'F001' not in prompt and 'F002' not in prompt
    assert '未来秘密不得提前写入' not in prompt and '未来章节提示' not in prompt
    assert 'C002' in prompt and 'target 必须' in prompt
    assert '子时' in prompt and '寅时' in prompt


def test_metadata_no_due_action_does_not_repeat_original_plant():
    prompt = metadata_prompt(2, [{'id': 'F004', 'plant_chapter': 1, 'plant_context': '不应重复演出的降生'}])
    assert 'F004' in prompt
    assert '不应重复演出的降生' not in prompt
    assert '本章计划埋设/提示：[]' in prompt


def test_metadata_clue_at_current_chapter_keeps_id_and_method():
    prompt = metadata_prompt(5, [{'id': 'F004', 'plant_chapter': 1, 'clue_plan': [
        {'chapter': 5, 'method': '对雾气异常敏感', 'intensity': '隐晦提示'},
        {'chapter': 43, 'method': '未来呼吸法线索'}]}])
    assert '对雾气异常敏感' in prompt and '隐晦提示' in prompt
    assert '未来呼吸法线索' not in prompt


def test_scene_time_cannot_contradict_chapter_forbidden_markers(tmp_path):
    director = ChapterDirector.__new__(ChapterDirector)
    director.root = tmp_path
    director._bible_cache = {}
    card = {'core_goal': '发现婴孩', 'conflicts': {}, 'emotion_curve': {}, 'chapter_hook': '回家',
            'timeline_anchor': {'max_time_progression': '一夜之内', 'forbidden_markers': ['清晨']},
            'scene_blueprints': [{'scene_num': n, 'location': '村口', 'characters': ['C001'],
                'goal': '观察', 'conflict': '危险', 'emotion': '紧张', 'narrative_time': t}
                for n, t in [(1, '子夜'), (2, '清晨')]]}
    errors = director.validate_task_card(card, 1)
    assert any('超出章级跨度' in error and '清晨' in error for error in errors)
    card['timeline_anchor']['forbidden_markers'] = []
    assert not any('与章级锚点冲突' in error for error in director.validate_task_card(card, 1))


def test_planned_dawn_respects_same_configured_night_gate(tmp_path):
    import json
    from novel_engine.quality.generation_constraints import scene_time_conflicts
    config = tmp_path / 'config/leak_terms/test.json'
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({'chapters': [1, 9], 'night_anchor_markers': ['深夜'], 'dawn_markers': ['黎明']}))
    card = {'chapter_num': 1, 'timeline_anchor': {'chapter_start_marker': '深夜', 'max_time_progression': '深夜至午后'},
            'scene_blueprints': [{'scene_num': 2, 'narrative_time': '黎明'}]}
    assert scene_time_conflicts(tmp_path, card)
    card['timeline_anchor']['max_time_progression'] = '当日'
    assert not scene_time_conflicts(tmp_path, card)
    card['chapter_num'] = 10
    card['timeline_anchor']['max_time_progression'] = '深夜'
    assert not scene_time_conflicts(tmp_path, card)


def test_time_repair_contract_returns_corrected_narrative_time():
    director = ChapterDirector.__new__(ChapterDirector)
    director._check_budget = Mock()
    director._call_with_meta = Mock(return_value=({'scene_blueprints': [
        {'scene_num': n, 'narrative_time': '子时'} for n in range(1, 5)]}, 'stop', {}))
    result = director._call_scene_craft(1, {}, '', [
        {'scene_num': n, 'narrative_time': '当夜'} for n in range(1, 5)], ['时间顺序冲突'])
    prompt = director._call_with_meta.call_args.args[0]
    assert '必须同时返回 narrative_time 字段' in prompt
    assert all(scene['narrative_time'] == '子时' for scene in result)


@pytest.mark.parametrize('cached', [False, True])
def test_time_fix_refreshes_chapter_event_metadata(tmp_path, cached):
    director = ChapterDirector.__new__(ChapterDirector)
    director.root = tmp_path
    director._try_template_task_card = Mock(return_value=None)
    director._build_shared_context = Mock(return_value=({}, ''))
    before = [{'scene_num': n, 'location': '村口', 'goal': '发现婴孩', 'narrative_time': '清晨'} for n in range(1, 5)]
    after = [dict(scene, narrative_time='深夜') for scene in before]
    director._call_scene_skeleton = Mock(return_value=before)
    director._call_scene_craft = Mock(side_effect=[before, after])
    original_meta = {'chapter_num': 1, 'core_goal': '发现婴孩', 'conflicts': {}, 'emotion_curve': {},
                     'chapter_hook': '门内异声', 'chapter_events': [{'narrative_time': '清晨'}]}
    corrected_meta = dict(original_meta, chapter_events=[{'narrative_time': '深夜'}])
    director._call_chapter_metadata = Mock(side_effect=[original_meta, corrected_meta])
    director.validate_task_card = Mock(side_effect=[['超出章级跨度：清晨与当夜锚点冲突'], []])
    result = director.generate_task_card_cached(1, {}) if cached else director.generate_task_card(1)
    assert director._call_chapter_metadata.call_count == 2
    assert director._call_chapter_metadata.call_args.args[3] == after
    assert result['chapter_events'][0]['narrative_time'] == '深夜'
    assert all(scene['narrative_time'] == '深夜' for scene in result['scene_blueprints'])


def test_metadata_retains_scene_actors_and_concrete_actions():
    prompt = metadata_prompt(1, [])
    assert '陈老根寻婴' in prompt and '提灯拨开雾气' in prompt
    assert '不得把所有动作都写成主角执行' in prompt
