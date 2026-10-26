import json
import sqlite3
import pytest
from novel_engine.pipeline.init_state import ensure_initial_state
from novel_engine.pipeline.reset_state import reset_runtime_state


def test_fresh_reset_then_seed_provides_real_character_facts_and_database(tmp_path):
    reset_runtime_state(tmp_path)
    assert ensure_initial_state(tmp_path)
    data = json.loads((tmp_path / 'memory/world_state/characters.json').read_text())
    assert data['characters']['C001']['name'] == '陆烬'
    assert data['characters']['C001']['location'] == '雾隐村外迷雾边缘'
    with sqlite3.connect(tmp_path / 'runtime/state.db') as db:
        assert db.execute('SELECT COUNT(*) FROM characters').fetchone()[0] == len(data['characters']) == 8
    assert not ensure_initial_state(tmp_path)


def test_existing_custom_characters_are_preserved(tmp_path):
    path = tmp_path / 'memory/world_state/characters.json'
    path.parent.mkdir(parents=True)
    original = json.dumps({'characters': {'CUSTOM': {'name': '自有角色', 'location': '原处'}}})
    path.write_text(original)
    assert not ensure_initial_state(tmp_path)
    assert path.read_text() == original


def test_corrupt_world_state_is_not_silently_overwritten(tmp_path):
    path = tmp_path / 'memory/world_state/characters.json'
    path.parent.mkdir(parents=True)
    path.write_text('{invalid')
    with pytest.raises(ValueError):
        ensure_initial_state(tmp_path)
    assert path.read_text() == '{invalid'
