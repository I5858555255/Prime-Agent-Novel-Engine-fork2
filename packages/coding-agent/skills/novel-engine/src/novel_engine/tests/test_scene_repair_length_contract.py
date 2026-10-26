"""Scene repairs must not perpetuate short drafts or introduce language pollution."""
from unittest.mock import Mock
import pytest
from novel_engine.agents.scene_schema import SceneOutput
from novel_engine.pipeline.chapter_journal import append_scene, load_authoritative_scenes
from novel_engine.tests.test_publication_stability import runner


@pytest.mark.parametrize('length,latin,accepted', [(1800, False, False), (2200, False, True), (2200, True, False)])
def test_scene_repair_respects_frozen_floor_and_keeps_original_on_rejection(tmp_path, length, latin, accepted, monkeypatch):
    obj = runner(tmp_path)
    monkeypatch.setattr("novel_engine.pipeline.pipeline_orchestrator.call_llm", lambda *a, **k: None)
    original = '旧' * 1700 + '。'
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original})
    candidate = '新' * length + (' foreign' if latin else '') + '。'
    obj.writer.generate_scene = Mock(return_value=SceneOutput(1, candidate, ''))
    card = {'chapter_num': 1, 'scene_blueprints': [{'scene_num': 1, 'word_count_target': 2500}]}
    review = {'issues': [{'dimension': 'plot_consistency', 'severity': 'high', 'scene_ids': [1], 'description': '补足动作衔接'}]}
    result = obj._patch_weak_scenes(original, review, card, {}, chapter_num=1)
    assert bool(result) == accepted
    assert '不得少于2000字' in obj.writer.generate_scene.call_args.kwargs['fix_directive']
    saved = load_authoritative_scenes(tmp_path, 1)[0]['scene_text']
    assert saved == (candidate if accepted else original)
    assert obj._scene_regen_used[(1, 1)] == 1
    obj.close()


@pytest.mark.parametrize('expanded_length,accepted', [(1900, False), (2300, True)])
def test_short_repaired_scene_gets_one_bounded_expansion(tmp_path, monkeypatch, expanded_length, accepted):
    obj = runner(tmp_path)
    original = '旧' * 2200 + '。'
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original})
    obj.writer.generate_scene = Mock(return_value=SceneOutput(1, '修' * 1500 + '。', ''))
    expanded = '补' * expanded_length + '。'
    refine = Mock(return_value=expanded)
    monkeypatch.setattr('novel_engine.pipeline.pipeline_orchestrator.call_llm', refine)
    card = {'chapter_num': 1, 'scene_blueprints': [{'scene_num': 1, 'word_count_target': 2500}]}
    review = {'issues': [{'dimension': 'plot_consistency', 'severity': 'high', 'scene_ids': [1], 'description': '补动作衔接'}]}
    result = obj._patch_weak_scenes(original, review, card, {}, chapter_num=1)
    assert bool(result) == accepted
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == (expanded if accepted else original)
    refine.assert_called_once()
    obj.close()


def test_scope_rejected_scene_patch_restores_authoritative_journal(tmp_path, monkeypatch):
    from novel_engine.tests.test_commit_path import _make_orch
    from novel_engine.pipeline.gates import BestCandidateState
    obj = _make_orch(tmp_path)
    original = '原稿没有越界设定。' * 300
    candidate = original + '气感已经觉醒。'
    append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': original})
    obj.current_novel = original
    obj._draft_novel = original
    obj._enforce_word_count = lambda text, *a: text
    def patch(*args, **kwargs):
        append_scene(tmp_path, 1, {'scene_id': 1, 'scene_text': candidate})
        return candidate
    obj._patch_weak_scenes = patch
    monkeypatch.setattr('novel_engine.quality.scope_gate.detect_scope_violations',
                        lambda *a, **k: {'hard': [{'term': '气感'}]})
    best = BestCandidateState(best_score=80, best_novel=original, best_draft=original, best_journal_snapshot=None)
    obj._dispatch_remediation(1, {}, {}, original, original, best, 1, {}, {'score': 80, 'review': {}}, {})
    assert obj.current_novel == original
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == original
    obj.close()
