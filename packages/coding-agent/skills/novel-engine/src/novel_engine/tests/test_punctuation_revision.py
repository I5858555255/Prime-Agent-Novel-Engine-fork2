from unittest.mock import Mock
import pytest
from novel_engine.quality.punctuation_revision import revise_long_sentences
from novel_engine.quality.punctuation_health import strip_all_punctuation

LONG = '只有空气剧烈震颤的感觉喉咙仿佛被一只无形的手死死掐住呼吸肌群拼命舒缩试图挤出一口新鲜空气。'
FIXED = '只有空气剧烈震颤的感觉。喉咙仿佛被一只无形的手死死掐住。呼吸肌群拼命舒缩，试图挤出一口新鲜空气。'


def test_punctuation_only_changes_keep_every_character():
    revise = Mock(return_value={'edits': [{'index': 0, 'text': FIXED}]})
    text, count = revise_long_sentences(LONG, revise)
    assert count == 1 and text == FIXED
    assert strip_all_punctuation(text) == strip_all_punctuation(LONG)


@pytest.mark.parametrize('proposal', [FIXED.replace('空气', '雾气', 1), '“' + LONG + '”',
                                     FIXED.replace('。', '。\n', 1), LONG])
def test_unsafe_or_ineffective_edits_are_rejected(proposal):
    text, count = revise_long_sentences(LONG, lambda items: {'edits': [{'index': 0, 'text': proposal}]})
    assert count == 0 and text == LONG


def test_clean_text_costs_no_request():
    revise = Mock()
    assert revise_long_sentences('陈老根停住脚步。', revise) == ('陈老根停住脚步。', 0)
    revise.assert_not_called()


def test_duplicate_edit_indices_do_not_apply_twice():
    text, count = revise_long_sentences(LONG, lambda items: {'edits': [{'index': 0, 'text': FIXED}] * 2})
    assert text == LONG and count == 0


def test_punctuation_repairs_update_scene_and_journal_without_scene_markers(tmp_path, monkeypatch):
    from novel_engine.tests.test_commit_path import _make_orch
    from novel_engine.agents.scene_schema import SceneOutput
    from novel_engine.pipeline.chapter_journal import append_scene, load_authoritative_scenes
    obj = _make_orch(tmp_path)
    scenes = [SceneOutput(1, LONG, ''), SceneOutput(2, '他关好木门。', '')]
    obj.writer.last_scenes = scenes
    text = '\n\n'.join(scene.scene_text for scene in scenes)
    obj.current_novel = text
    obj._draft_novel = text
    for scene in scenes:
        append_scene(tmp_path, 1, {'scene_id': scene.scene_id, 'scene_text': scene.scene_text})
    monkeypatch.setattr('novel_engine.pipeline.pipeline_orchestrator.call_llm',
                        lambda *a, **k: {'edits': [{'index': 0, 'text': FIXED}]})
    revised, accepted = obj._repair_review_punctuation(1, text, scenes)
    assert accepted == 1 and FIXED in revised
    assert scenes[0].scene_text == FIXED
    assert scenes[1].scene_text == '他关好木门。'
    assert load_authoritative_scenes(tmp_path, 1)[0]['scene_text'] == FIXED
    obj.close()


def test_large_chapter_repairs_all_measured_sentences_with_one_request():
    sentences = [LONG.replace('空气', term) for term in ['寒风', '雨水', '浓雾', '冷气', '雾气', '热气', '霜雪', '夜风', '晨风', '山风']]
    sentences = [text.replace('喉咙', term) for text in sentences for term in ['双臂', '双腿', '腰间', '腹部', '肩头']]
    text = '\n'.join(sentences)
    revise = Mock(side_effect=lambda items: {'edits': [
        {'index': item['index'], 'text': item['text'].replace('的感觉', '的感觉。').replace('掐住', '掐住。')}
        for item in items]})
    repaired, count = revise_long_sentences(text, revise)
    assert count == 50
    assert strip_all_punctuation(repaired) == strip_all_punctuation(text)
    revise.assert_called_once()


@pytest.mark.parametrize('word', ['呼吸', '周围', '婴孩', '身体'])
def test_hard_run_split_preserves_words_straddling_character_limit(word):
    from novel_engine.quality.punctuation_health import repair_paragraph_long_runs, check_text_punctuation
    text = '山' * 44 + word + '树' * 40
    fixed, _ = repair_paragraph_long_runs(text)
    assert word in fixed
    assert word[0] + '。\n' + word[1:] not in fixed
    assert strip_all_punctuation(fixed) == text
    assert check_text_punctuation(fixed) == []
