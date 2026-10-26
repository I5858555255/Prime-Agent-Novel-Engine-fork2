from unittest.mock import Mock
from novel_engine.agents.scene_schema import SceneOutput, build_scene_prompt
from novel_engine.agents.writer_agent import WriterAgent


def test_later_specific_action_is_forbidden_before_its_scene():
    blueprints = [{'scene_num': 2, 'goal': '发现声源', 'beats': ['辨认婴儿形态']},
                  {'scene_num': 3, 'goal': '介入现场', 'beats': ['挡在婴前']},
                  {'scene_num': 4, 'goal': '暂离现场', 'beats': ['抱起婴儿']}]
    prompt = build_scene_prompt({'chapter_num': 1}, blueprints[1], blueprints)
    assert '后续场景4尚未发生，不得提前执行：暂离现场；具体事件点：抱起婴儿' in prompt
    assert '前序场景2仅作已发生信息承接，不得重演' in prompt


def test_concurrent_plans_are_not_described_as_already_completed(tmp_path):
    writer = WriterAgent(llm_client=object(), project_root=tmp_path)
    writer.generate_scene = Mock(side_effect=lambda card, bp, *a, **k: SceneOutput(bp['scene_num'], '合格正文。', ''))
    writer.generate_scenes({'chapter_num': 1, 'scene_blueprints': [
        {'scene_num': 1, 'goal': '发现婴儿'}, {'scene_num': 2, 'goal': '抱起婴儿'}]}, {'synopsis': ''})
    for call in writer.generate_scene.call_args_list:
        assert '全章场景分工' in call.args[4]
        assert '后续尚未发生' in call.args[4]
        assert '已覆盖情节' not in call.args[4]


def test_next_chapter_task_survives_metadata_merge_and_generation_guardrails(tmp_path):
    from novel_engine.agents.chapter_director import ChapterDirector
    from novel_engine.quality.generation_constraints import generation_constraints
    director = ChapterDirector.__new__(ChapterDirector)
    meta = {'chapter_num': 99, 'core_goal': '出生异象'}
    card = director._merge_task_cards([{'scene_num': 1}], meta, 1,
        {'chapter_outline_task': '婴儿降生引发村民恐慌', 'next_chapter_task': '陈老根收养并起名'})
    guardrails = generation_constraints(tmp_path, card)
    assert card['outline_boundary']['next_task'] == '陈老根收养并起名'
    assert '下一章尚未发生的核心任务（本章不得提前完成）：陈老根收养并起名' in guardrails
    assert '长期经历' in guardrails
    assert meta['chapter_num'] == 99 and 'outline_boundary' not in meta
