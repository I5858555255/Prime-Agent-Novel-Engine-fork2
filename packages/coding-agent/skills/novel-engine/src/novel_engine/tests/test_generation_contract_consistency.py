from unittest.mock import Mock
import pytest
from novel_engine.agents.writer_agent import WriterAgent
from novel_engine.agents.scene_schema import SceneOutput, build_scene_prompt


def test_scene_prompt_has_one_length_contract_and_novel_prose_inside_json():
    assert '单场景正文不超过800字' not in WriterAgent.SCENE_SYSTEM_PROMPT
    assert '严禁输出散文体' not in WriterAgent.SCENE_SYSTEM_PROMPT
    assert '整场字数统一服从蓝图目标' in WriterAgent.SCENE_SYSTEM_PROMPT
    assert 'JSON 对象之外禁止输出裸文本' in WriterAgent.SCENE_SYSTEM_PROMPT
    assert '单人场景不得为了对手戏虚构人物' in WriterAgent.SCENE_SYSTEM_PROMPT


def test_future_concrete_event_and_result_are_excluded_from_current_scene():
    first = {'scene_num': 1, 'goal': '降生', 'characters': ['婴孩'], 'word_count_target': 2500, 'beats': ['实体出现']}
    second = {'scene_num': 2, 'goal': '哭声', 'beats': ['求救'],
              'concrete_events': [{'event': '哭声造成草木枯萎', 'observable_action': '草叶发黑'}],
              'irreversible_state_delta': '周围植物已经枯死'}
    prompt = build_scene_prompt({'chapter_num': 1, 'scene_blueprints': [first, second]}, first, [first, second])
    assert '首次发生的具体动作' in prompt and '哭声造成草木枯萎' in prompt
    assert '尚未成立的结果状态：周围植物已经枯死' in prompt
    assert '逗号不算句末' in prompt


@pytest.mark.parametrize('original,candidate,accepted', [(2200, 1500, False), (2200, 2000, True),
    (1900, 1700, False), (1900, 2050, True), (4000, 2500, True)])
def test_polish_never_undoes_length_floor_but_can_trim_overlong_scene(tmp_path, original, candidate, accepted):
    client = Mock()
    client.concurrency = 1
    client.chat_completion.return_value = {'content': '修' * (candidate - 1) + '。'}
    writer = WriterAgent(llm_client=client, project_root=tmp_path)
    scene = SceneOutput(1, '原' * (original - 1) + '。', '原钩子')
    result, flags = writer.polish_scenes([scene], {'chapter_num': 1, 'scene_blueprints': [
        {'scene_num': 1, 'word_count_target': 2500}]}, client=client)
    assert flags == [accepted]
    assert len(result[0].scene_text) == (candidate if accepted else original)
    assert result[0].hook == '原钩子'


def test_director_receives_actual_word_budget_and_next_chapter_boundary(tmp_path):
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    director.root = tmp_path
    director._check_budget = Mock()
    director._call_with_meta = Mock(return_value=({'scene_blueprints': [{'scene_num': i} for i in range(1,5)]}, 'stop', {}))
    context = {'constraints': '', 'relevant_plot_nodes': [], 'bible': {'world': '', 'character': '', 'style': ''},
               'chapter_outline_task': '降生异象引发村民恐慌', 'next_chapter_task': '陈老根拾婴归家并起名',
               'world_state': {'characters': {'C002': {'name': '陈老根'}}}}
    director._call_scene_skeleton(1, context, '')
    prompt = director._call_with_meta.call_args.args[0]
    assert '全章目标10000字' in prompt and '[2500, 2500, 2500, 2500]' in prompt
    assert '先后发生、互相推进的微事件' in prompt
    assert '不能让承担恐慌、应对等行动的人物在characters中消失' in prompt
    assert '未建档普通配角用具体身份' in director._SKELETON_SYSTEM
    assert '下一章尚未发生的核心任务：陈老根拾婴归家并起名' in prompt
    assert 'C002' in prompt and '陈老根' in prompt
    assert '林远' not in director._SKELETON_SYSTEM
    assert 'F001' not in director._METADATA_SYSTEM


def test_infant_restrictions_leave_planned_adult_observers_available():
    bp = {'scene_num': 1, 'characters': ['C001', '守夜人'], 'beats': ['守夜人尝试靠近婴儿']}
    prompt = build_scene_prompt({'chapter_num': 1, 'protagonist_agency_level': 'infant_low'}, bp, [bp])
    assert '仅主角的心理活动' in prompt
    assert '上述限制不适用于蓝图中的普通成人配角' in prompt


def test_truncated_craft_retry_retains_schema_and_time_feedback():
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    director._check_budget = Mock()
    calls = []
    def respond(prompt, max_tokens, system_prompt):
        assert max_tokens == 3000
        calls.append(prompt)
        if len(calls) == 1:
            return {}, 'length', {'completion_tokens': max_tokens}
        assert '{"scene_blueprints":[...]}' in prompt
        assert '第二场不得推进到黎明' in prompt
        return {'scene_blueprints': [{'scene_num': n} for n in range(1, 5)]}, 'stop', {}
    director._call_with_meta = respond
    scenes = director._call_scene_craft(2, {}, '', [{'scene_num': n} for n in range(1, 5)], ['第二场不得推进到黎明'])
    assert len(calls) == 2 and len(scenes) == 4


def test_unintroduced_future_actor_cannot_appear_under_an_anonymous_label():
    first = {'scene_num': 1, 'goal': '降生', 'characters': ['C001'], 'beats': ['实体出现']}
    second = {'scene_num': 2, 'goal': '村民发现', 'characters': ['C001', 'C002']}
    prompt = build_scene_prompt({'chapter_num': 1}, first, [first, second])
    assert '即使不具名也不能提前安排动作：C002' in prompt


def test_repairs_receive_frozen_events_instead_of_treating_review_as_new_canon(tmp_path):
    from novel_engine.quality.generation_constraints import repair_constraints
    card = {'chapter_num': 1, 'scene_blueprints': [{'scene_num': 1, 'characters': ['C001'],
             'goal': '出生', 'concrete_events': [{'event': '红光出现'}]}],
            'foreshadow_actions': [{'foreshadow_id': 'F004', 'action': '异象'}]}
    contract = repair_constraints(tmp_path, card)
    assert '冻结的修复范围' in contract and '红光出现' in contract and 'C001' in contract and 'F004' in contract
    assert '审稿意见是待核验的修复建议，不是新的故事设定' in contract
