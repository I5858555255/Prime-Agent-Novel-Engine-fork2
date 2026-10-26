"""Timeline persistence belongs to the same project as chapter generation."""
from novel_engine.timeline.timeline_anchors import load_anchors, save_anchor, get_prev_anchor, check_body_against_anchors
from novel_engine.tests.test_commit_path import _make_orch


def test_two_projects_do_not_share_timeline(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    save_anchor({'chapter_id': 1, 'elapsed_since_prev': '起始章', 'jump': False}, first)
    save_anchor({'chapter_id': 1, 'elapsed_since_prev': '十年后', 'jump': True}, second)
    assert load_anchors(first)[0]['jump'] is False
    assert get_prev_anchor(2, second)['jump'] is True
    assert check_body_against_anchors(1, '十年后。', first)
    assert not check_body_against_anchors(1, '十年后。', second)


def test_pipeline_saves_actual_anchor_without_inventing_days(tmp_path):
    obj = _make_orch(tmp_path)
    card = dict(obj._frozen_task_cards[1], timeline_anchor={
        'chapter_start_marker': '当夜子时', 'max_time_progression': '单夜内'})
    obj._frozen_task_cards[1] = card
    obj._enforce_word_count = lambda text, low, high: text
    try:
        result = obj.generate_single_chapter(1)
        assert result['published'], result
        anchor = load_anchors(tmp_path)[0]
        assert anchor['in_world_datetime_start'] == '当夜子时'
        assert anchor['max_time_progression'] == '单夜内'
        assert anchor['elapsed_since_prev'] == '起始章'
    finally:
        obj.close()
