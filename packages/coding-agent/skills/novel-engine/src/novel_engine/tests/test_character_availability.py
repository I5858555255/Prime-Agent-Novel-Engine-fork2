"""Future cast must not become current actors through world-state context."""
import json
from unittest.mock import Mock

import pytest

from novel_engine.agents.chapter_director import ChapterDirector, _render_outline_mandate
from novel_engine.agents.world_simulator import WorldSimulator
from novel_engine.tests.test_publication_stability import runner, commit
from novel_engine.quality.generation_constraints import generation_constraints, repair_constraints


def simulator():
    sim = WorldSimulator.__new__(WorldSimulator)
    sim.characters = {'characters': {
        'C001': {'name': '婴孩', 'first_appearance': 1},
        'C002': {'name': '陈老根', 'first_appearance': 2, 'secret': '不能泄露'},
        'C003': {'name': '远客', 'first_appearance': 100},
    }}
    sim.constraints = {'C002': {'plot_lock': {'identity': '未来身份'}}}
    sim.factions = sim.power_system = {}
    sim.simulate_character_progression = Mock(return_value={})
    return sim


@pytest.mark.parametrize('chapter,available', [(1, {'C001'}), (2, {'C001', 'C002'}), (100, {'C001', 'C002', 'C003'})])
def test_context_unlocks_cast_without_deleting_canonical_state(chapter, available):
    sim = simulator()
    state = sim.build_world_state_for_chapter(chapter)
    assert set(state['characters']) == available
    assert {call.args[0] for call in sim.simulate_character_progression.call_args_list} == available
    assert len(sim.characters['characters']) == 3
    for data in state['future_characters'].values():
        assert set(data) == {'name', 'first_appearance'}


def test_future_constraints_do_not_leak_into_context():
    sim = simulator()
    assert '未来身份' not in sim.get_constraints_summary(1)
    assert '未来身份' in sim.get_constraints_summary(2)
    prompt = _render_outline_mandate({'chapter_outline_task': '降生', 'world_state': sim.build_world_state_for_chapter(1)})
    assert '陈老根' in prompt and '禁止提前出场' in prompt
    assert '不能泄露' not in prompt


def test_writing_and_repairs_receive_the_same_unavailable_cast(tmp_path):
    path = tmp_path / 'memory/world_state/characters.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(simulator().characters, ensure_ascii=False))
    for constraints in (generation_constraints, repair_constraints):
        text = constraints(tmp_path, {'chapter_num': 1})
        assert '陈老根' in text and '禁止实际参与剧情' in text
        assert '不能泄露' not in text
        unlocked = constraints(tmp_path, {'chapter_num': 2})
        assert '陈老根' not in unlocked


def test_publication_snapshot_keeps_future_cast(tmp_path):
    obj = runner(tmp_path)
    obj.simulator.characters['characters']['C002'] = {
        'name': '陈老根', 'first_appearance': 2, 'realm': '凡人'}
    obj.state_machine.commit_chapter = Mock(wraps=obj.state_machine.commit_chapter)
    try:
        assert 'C002' not in obj.simulator.build_world_state_for_chapter(1)['characters']
        result = commit(obj, 1, '村口')
        assert result['published'], result
        snapshot = obj.state_machine.commit_chapter.call_args.kwargs['world_state_snapshot']
        assert snapshot['characters']['C002']['first_appearance'] == 2
        assert obj.simulator.characters['characters']['C002']['first_appearance'] == 2
    finally:
        obj.close()


@pytest.mark.parametrize('actor', ['C002', '陈老根'])
def test_director_rejects_premature_actor_before_writing(tmp_path, actor):
    director = ChapterDirector.__new__(ChapterDirector)
    director.root = tmp_path
    director._bible_cache = {}
    director.simulator = simulator()
    card = {'scene_blueprints': [{'scene_num': 1, 'characters': [actor]}],
            'state_changes': [{'target': actor}]}
    errors = director.validate_task_card(card, 1)
    assert sum('尚未登场' in error for error in errors) == 2
    assert not any('尚未登场' in error for error in director.validate_task_card(card, 2))
