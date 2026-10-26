from unittest.mock import Mock
import pytest
from novel_engine.pipeline.chapter_journal import append_scene, load_authoritative_scenes
from novel_engine.tests.test_commit_path import _make_orch


@pytest.mark.parametrize('length,score,unstable,adopted', [(4000, 90, False, False), (9000, 40, False, False),
                                                       (9000, 92, True, False), (9000, 75, False, True)])
def test_rejected_whole_rewrite_restores_text_and_journal(tmp_path, length, score, unstable, adopted):
    obj = _make_orch(tmp_path)
    original = '原稿。' * 3000
    candidate = '新' * (length - 1) + '。'
    obj.current_novel = original
    obj._draft_novel = original
    obj._last_review_score = 40
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original})
    obj._rollback_world_to = Mock()
    obj.writer.generate_full_chapter = Mock(return_value=candidate)
    obj.writer.polish_chapter = Mock(side_effect=lambda text, *args, **kwargs: text)
    obj._journal_validated_scenes = lambda chapter: append_scene(tmp_path, chapter, {'scene_id': 1, 'scene_text': candidate})
    obj._finalize_current_novel = lambda chapter: obj.current_novel
    obj._stage_review = Mock(return_value={'score': score, 'verdict': 'fix', 'review_unstable': unstable, 'review': {}})
    assert obj._low_score_rewrite(1, {}, {}, {}) == adopted
    assert obj.current_novel == (candidate if adopted else original)
    assert obj._draft_novel == (candidate if adopted else original)
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == (candidate if adopted else original)
    if length < 8000:
        obj._stage_review.assert_not_called()
    obj.close()


@pytest.mark.parametrize('length,latin,accepted', [(4000, False, False), (9000, False, True), (9000, True, False)])
def test_editor_revision_is_validated_before_adoption(tmp_path, monkeypatch, length, latin, accepted):
    obj = _make_orch(tmp_path)
    original = '原稿。' * 3000
    candidate = '新' * length + (' foreign' if latin else '。')
    obj.current_novel = original
    monkeypatch.setattr('novel_engine.pipeline.pipeline_orchestrator.call_llm', lambda *a, **k: candidate)
    revised = obj._rewrite_weak_dimensions(original, {'issues': []})
    assert revised == (candidate if accepted else None)
    assert obj.current_novel == original
    obj.close()


@pytest.mark.parametrize('gate_passed', [False, True])
def test_deterministic_anchor_repair_persists_only_after_gate_pass(tmp_path, gate_passed):
    import json
    obj = _make_orch(tmp_path)
    obj.config['llm']['use_mock'] = False
    original = '李淳的记忆消失在黑暗之中。' + '雾水打湿了衣角。' * 100
    replacement = '陆烬前世名为李淳，记忆消失在黑暗之中。'
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original, 'hook': '保留钩子', 'beats': ['降生']})
    obj.polish_router = Mock()
    obj.polish_router.chat_completion.return_value = {'content': json.dumps({'edits': [
        {'scene_id': 1, 'op': 'replace_span', 'anchor': '李淳的记忆消失在黑暗之中。', 'replacement': replacement}]}, ensure_ascii=False)}
    obj._enforce_word_count = lambda text, *a: text
    obj._deterministic_quality_gate = lambda *a: {'passed': gate_passed, 'issues': []}
    result = obj._det_fulltext_fix(original, ['[称谓] 缺少同指锚点'], 1, {})
    row = load_authoritative_scenes(tmp_path, 1)[0]
    assert bool(result) == gate_passed
    assert row['scene_text'] == (original.replace('李淳的记忆消失在黑暗之中。', replacement) if gate_passed else original)
    assert row['hook'] == '保留钩子' and row['beats'] == ['降生']
    obj.close()
