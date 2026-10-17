# -*- coding: utf-8 -*-
"""Round 10：regression test——"_render_outline_mandate" 强制渲染进三处 director prompt。

假 LLM 拦截 user prompt，断言 ch3 任务串（村议/收留/王大牛）出现在三次调用里，
adjacent_tasks 至少出现在 skeleton prompt 中。空 task 时不渲染块。
旧码上此测试应失败（字段只在 context dict，不在 prompt）。
"""
import json
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent.parent / "src"),
)

from novel_engine.agents.chapter_director import (
    ChapterDirector,
    parse_chapter_tasks,
    OUTLINE_FILENAME,
    _render_outline_mandate,
)


_SAMPLE_OUTLINE = """\
# 测试大纲

## 四、第一卷《昆仑遗子》

#### 阶段一(1-51章)

| 章 | 核心任务 |
|---|---|
| 1 | 神魂跨界，陆烬以婴儿形态降生雾隐村外 |
| 2 | 陈老根拾婴归家，取名陆烬 |
| 3 | 村长王大牛主持村议，决定是否收留弃婴，埋下迷雾禁婴传闻 |
| 4 | 陈老根独自抚养陆烬，交代其孤僻隐忍的性格底色 |
| 5 | 陆烬幼年体弱多病，却对浊气异常敏感 |
"""


def _fake_skeleton_response() -> dict:
    """返回结构合法的 4 场景骨架。"""
    return {
        "scene_blueprints": [
            {"scene_num": 1, "narrative_time": "清晨", "location": "村口古树",
             "characters": ["王大牛", "陈老根"], "goal": "召集村民议事",
             "conflict": "是否收留弃婴", "emotion": "紧张犹豫",
             "beats": ["王大牛击鼓聚众", "村民质疑纷纷", "陈老根上前求情"]},
            {"scene_num": 2, "narrative_time": "日中", "location": "祠堂",
             "characters": ["王大牛", "族老甲", "族老乙"], "goal": "表决收留事宜",
             "conflict": "恐惧 vs 怜悯", "emotion": "激烈争执",
             "beats": ["王大牛拍板", "众人争论", "最终妥协"]},
            {"scene_num": 3, "narrative_time": "傍晚", "location": "陈老根家",
             "characters": ["陈老根", "陆烬"], "goal": "照顾婴儿",
             "conflict": "体弱多病难养", "emotion": "怜惜担忧",
             "beats": ["喂食药汤", "婴儿啼哭", "陈老根默默祈祷"]},
            {"scene_num": 4, "narrative_time": "入夜", "location": "村中老槐树下",
             "characters": ["村民甲", "村民乙"], "goal": "传播禁忌说法",
             "conflict": "谣言扩散", "emotion": "窃窃私语",
             "beats": ["议论弃婴来历", "提及迷雾禁区", "约定不得外传"]},
        ]
    }


def _fake_craft_response(skeleton: list) -> dict:
    """在骨架基础上追加 craft 字段，保持 4 场景。"""
    updated = []
    for sc in skeleton:
        sc_copy = dict(sc)
        sc_copy["concrete_events"] = [
            {"event": "召集村议", "observable_action": "击鼓聚众于祠堂"},
            {"event": "表决收留", "observable_action": "众人举手表决"},
        ]
        sc_copy["named_interactions"] = [
            {"characters": ["王大牛", "陈老根"], "interaction_type": "争执",
             "brief": "就弃婴去留争执"}
        ]
        sc_copy["info_reveal_points"] = [
            {"type": "伏笔埋设", "content": "迷雾禁区禁忌说法",
             "anchor_terms": ["迷雾", "禁区"]}
        ]
        sc_copy["protagonist_interiority"] = "陈老根怜惜弃婴命运"
        sc_copy["scene_constraints"] = []
        sc_copy["scene_progression_contract"] = {
            "new_state_or_entity": ["弃婴归陈老根抚养"],
            "irreversible_change": "村议决定收留，迷雾禁婴传闻确立"
        }
        sc_copy["scene_craft_elements"] = {"info_reversal_point": {"present": False, "content": ""}}
        updated.append(sc_copy)
    return {"scene_blueprints": updated}


def _fake_metadata_response(skeleton: list, chapter_num: int) -> dict:
    return {
        "chapter_num": chapter_num,
        "protagonist_agency_level": "infant_low",
        "core_goal": "村长王大牛主持村议决定是否收留弃婴，埋下迷雾禁婴传闻",
        "conflicts": {"internal": "陈老根怜惜与担忧", "external": "村民恐惧与排斥"},
        "emotion_curve": {"start": "紧张", "middle": "争执", "climax": "拍板", "end": "余波"},
        "chapter_hook": "迷雾禁婴传闻开始在村中流传",
        "foreshadow_actions": [],
        "chapter_events": [
            {"event_type": "议事", "one_line_summary": "王大牛召集村议表决弃婴去留",
             "participants": ["王大牛", "陈老根", "村民"], "location": "祠堂",
             "narrative_time": "日中", "consequence_state": "收留弃婴，禁婴传闻确立"}
        ],
        "state_changes": [{"type": "character_realm", "target": "陆烬", "new_value": "归陈老根抚养", "chapter": chapter_num}],
        "foreshadow_execution": [],
        "end_state": {
            "narrative_position": "陈老根抱婴入夜独坐",
            "location": "陈老根家",
            "completed_actions": ["村议拍板", "迷雾禁婴传闻确立"],
            "pending_actions": ["婴儿成长"],
            "time_marker": "夜"
        },
        "timeline_anchor": {
            "chapter_start_marker": "清晨",
            "max_time_progression": "清晨至入夜",
            "forbidden_markers": ["次日清晨", "数月后"]
        },
    }


class _CapturingLLMClient:
    """假 LLM：记录每次 chat_completion 收到的 user prompt，按调用序号返回骨架/craft/metadata。"""

    def __init__(self):
        self.captured_prompts: list[str] = []
        self._call_count = 0

    def chat_completion(self, messages: list[dict], **kwargs: Any) -> dict:
        user_prompt = ""
        for m in messages:
            if m.get("role") == "user":
                user_prompt = m.get("content", "")
        self.captured_prompts.append(user_prompt)
        self._call_count += 1
        n = self._call_count

        if n == 1:
            # Call 1: skeleton — 4 场景
            body = {
                "scene_blueprints": [
                    {"scene_num": 1, "narrative_time": "清晨", "location": "村口古树",
                     "characters": ["王大牛", "陈老根"], "goal": "召集村民议事",
                     "conflict": "是否收留弃婴", "emotion": "紧张犹豫",
                     "beats": ["王大牛击鼓聚众", "村民质疑纷纷", "陈老根上前求情"]},
                    {"scene_num": 2, "narrative_time": "日中", "location": "祠堂",
                     "characters": ["王大牛", "族老甲"], "goal": "表决收留事宜",
                     "conflict": "恐惧 vs 怜悯", "emotion": "激烈争执",
                     "beats": ["王大牛拍板", "众人争论", "最终妥协"]},
                    {"scene_num": 3, "narrative_time": "傍晚", "location": "陈老根家",
                     "characters": ["陈老根", "陆烬"], "goal": "照顾婴儿",
                     "conflict": "体弱多病难养", "emotion": "怜惜担忧",
                     "beats": ["喂食药汤", "婴儿啼哭", "陈老根默默祈祷"]},
                    {"scene_num": 4, "narrative_time": "入夜", "location": "老槐树下",
                     "characters": ["村民甲", "村民乙"], "goal": "传播禁忌说法",
                     "conflict": "谣言扩散", "emotion": "窃窃私语",
                     "beats": ["议论弃婴来历", "提及迷雾禁区", "约定不得外传"]},
                ]
            }
        elif n == 2:
            # Call 2: craft — 在同一骨架上追加 craft 字段（保持 4 场景）
            body = {
                "scene_blueprints": [
                    {"scene_num": 1, "narrative_time": "清晨", "location": "村口古树",
                     "characters": ["王大牛", "陈老根"], "goal": "召集村民议事",
                     "conflict": "是否收留弃婴", "emotion": "紧张犹豫",
                     "beats": ["王大牛击鼓聚众", "村民质疑纷纷", "陈老根上前求情"],
                     "concrete_events": [{"event": "击鼓聚众", "observable_action": "王大牛敲鼓"},
                                         {"event": "村民争执", "observable_action": "众人争吵"}],
                     "named_interactions": [{"characters": ["王大牛", "陈老根"], "interaction_type": "争执", "brief": "就弃婴去留争执"}],
                     "info_reveal_points": [{"type": "伏笔埋设", "content": "迷雾禁区禁忌", "anchor_terms": ["迷雾", "禁区"]}],
                     "protagonist_interiority": "陈老根怜惜弃婴",
                     "scene_constraints": [],
                     "scene_progression_contract": {"new_state_or_entity": ["弃婴归陈老根"], "irreversible_change": "村议收留决定"},
                     "scene_craft_elements": {"info_reversal_point": {"present": False, "content": ""}},
                     },
                    {"scene_num": 2, "narrative_time": "日中", "location": "祠堂",
                     "characters": ["王大牛", "族老甲"], "goal": "表决收留事宜",
                     "conflict": "恐惧 vs 怜悯", "emotion": "激烈争执",
                     "beats": ["王大牛拍板", "众人争论", "最终妥协"],
                     "concrete_events": [{"event": "举手表决", "observable_action": "众人举手"},
                                         {"event": "王大牛宣布结果", "observable_action": "拍板收留"}],
                     "named_interactions": [{"characters": ["王大牛", "族老甲"], "interaction_type": "表决", "brief": "表决弃婴去留"}],
                     "info_reveal_points": [{"type": "伏笔埋设", "content": "禁婴传闻确立", "anchor_terms": ["禁婴", "传闻"]}],
                     "protagonist_interiority": "王大牛权衡村议压力",
                     "scene_constraints": [],
                     "scene_progression_contract": {"new_state_or_entity": ["决议通过"], "irreversible_change": "村议拍板收留"},
                     "scene_craft_elements": {"info_reversal_point": {"present": False, "content": ""}},
                     },
                    {"scene_num": 3, "narrative_time": "傍晚", "location": "陈老根家",
                     "characters": ["陈老根", "陆烬"], "goal": "照顾婴儿",
                     "conflict": "体弱多病难养", "emotion": "怜惜担忧",
                     "beats": ["喂食药汤", "婴儿啼哭", "陈老根默默祈祷"],
                     "concrete_events": [{"event": "喂药", "observable_action": "陈老根喂汤药"},
                                         {"event": "婴儿啼哭", "observable_action": "陆烬哭闹"}],
                     "named_interactions": [{"characters": ["陈老根", "陆烬"], "interaction_type": "照料", "brief": "陈老根喂药照料"}],
                     "info_reveal_points": [{"type": "伏笔埋设", "content": "陆烬对浊气敏感", "anchor_terms": ["浊气", "敏感"]}],
                     "protagonist_interiority": "陈老根忧 Child 命运",
                     "scene_constraints": [],
                     "scene_progression_contract": {"new_state_or_entity": ["婴儿安稳"], "irreversible_change": "陈老根正式收养"},
                     "scene_craft_elements": {"info_reversal_point": {"present": False, "content": ""}},
                     },
                    {"scene_num": 4, "narrative_time": "入夜", "location": "老槐树下",
                     "characters": ["村民甲", "村民乙"], "goal": "传播禁忌说法",
                     "conflict": "谣言扩散", "emotion": "窃窃私语",
                     "beats": ["议论弃婴来历", "提及迷雾禁区", "约定不得外传"],
                     "concrete_events": [{"event": "议论弃婴", "observable_action": "村民交头接耳"},
                                         {"event": "约定保密", "observable_action": "握手立誓"}],
                     "named_interactions": [{"characters": ["村民甲", "村民乙"], "interaction_type": "对话", "brief": "商量禁婴说法"}],
                     "info_reveal_points": [{"type": "伏笔埋设", "content": "迷雾禁区与弃婴关联", "anchor_terms": ["迷雾", "弃婴"]}],
                     "protagonist_interiority": "村民畏惧迷雾禁区",
                     "scene_constraints": [],
                     "scene_progression_contract": {"new_state_or_entity": ["禁婴传闻流传"], "irreversible_change": "村中形成禁忌说法"},
                     "scene_craft_elements": {"info_reversal_point": {"present": False, "content": ""}},
                     },
                ]
            }
        else:
            # Call 3+: metadata
            body = {
                "chapter_num": 3,
                "protagonist_agency_level": "infant_low",
                "core_goal": "村长王大牛主持村议决定是否收留弃婴，埋下迷雾禁婴传闻",
                "conflicts": {"internal": "陈老根怜惜与担忧", "external": "村民恐惧与排斥"},
                "emotion_curve": {"start": "紧张", "middle": "争执", "climax": "拍板", "end": "余波"},
                "chapter_hook": "迷雾禁婴传闻开始在村中流传",
                "foreshadow_actions": [],
                "chapter_events": [
                    {"event_type": "议事", "one_line_summary": "王大牛召集村议表决弃婴去留",
                     "participants": ["王大牛", "陈老根", "村民"], "location": "祠堂",
                     "narrative_time": "日中", "consequence_state": "收留弃婴，禁婴传闻确立"}
                ],
                "state_changes": [{"type": "character_realm", "target": "陆烬", "new_value": "归陈老根抚养", "chapter": 3}],
                "foreshadow_execution": [],
                "end_state": {
                    "narrative_position": "陈老根抱婴入夜独坐",
                    "location": "陈老根家",
                    "completed_actions": ["村议拍板", "迷雾禁婴传闻确立"],
                    "pending_actions": ["婴儿成长"],
                    "time_marker": "夜"
                },
                "timeline_anchor": {
                    "chapter_start_marker": "清晨",
                    "max_time_progression": "清晨至入夜",
                    "forbidden_markers": ["次日清晨", "数月后"]
                },
            }
        return {
            "content": json.dumps(body, ensure_ascii=False),
            "finish_reason": "stop",
            "_usage": {"completion_tokens": 120, "prompt_tokens": 400},
        }


def _build_director(tmp_path: Path) -> tuple[ChapterDirector, _CapturingLLMClient]:
    """构造带假 LLM 的 ChapterDirector，注入测试大纲。"""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "planning").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "runtime_config.json").write_text(
        '{"llm":{"use_mock":true}}', encoding="utf-8")
    (tmp_path / "docs" / OUTLINE_FILENAME).write_text(_SAMPLE_OUTLINE, encoding="utf-8")
    # 最小 volumes/plot_graph 避免 keyerror
    (tmp_path / "config" / "planning" / "volumes.json").write_text(
        json.dumps({"volumes": [{"id": "V01", "chapter_range": [1, 51]}]}, ensure_ascii=False),
        encoding="utf-8")
    (tmp_path / "config" / "planning" / "plot_graph.json").write_text(
        json.dumps({"nodes": [
            {"chapter_target": 3, "description": "村长王大牛主持村议", "type": "main_plot"},
            {"chapter_target": 2, "description": "陈老根拾婴", "type": "main_plot"},
            {"chapter_target": 4, "description": "陈老根抚养陆烬", "type": "main_plot"},
        ]}), encoding="utf-8")
    (tmp_path / "config" / "foreshadow").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "foreshadow" / "registry.json").write_text(
        '{"foreshadows": []}', encoding="utf-8")
    fake_llm = _CapturingLLMClient()
    director = ChapterDirector(tmp_path, llm_client=fake_llm)
    return director, fake_llm


def test_outline_mandate_in_all_three_prompts(tmp_path):
    """ch3 时三次 prompt 都含"村议"/"收留"/"王大牛"等任务关键串。"""
    director, fake_llm = _build_director(tmp_path)
    card = director.generate_task_card(3)
    assert len(fake_llm.captured_prompts) >= 3, f"Expected at least 3 calls, got {len(fake_llm.captured_prompts)}"
    for i, prompt in enumerate(fake_llm.captured_prompts[:3]):
        assert "村议" in prompt or "收留" in prompt, \
            f"Call {i+1} prompt missing ch3 task key '村议'/'收留': {prompt[:200]}"
        assert "王大牛" in prompt, \
            f"Call {i+1} prompt missing named entity '王大牛': {prompt[:200]}"
        assert "最高优先级" in prompt or "核心事件" in prompt, \
            f"Call {i+1} prompt missing mandate header: {prompt[:200]}"
    # adjacent_tasks 至少在 skeleton prompt 中出现
    assert any("陈老根独自抚养" in p for p in fake_llm.captured_prompts[:3]), \
        "adjacent_tasks (ch4) not found in any prompt"


def test_outline_mandate_empty_task_no_block(tmp_path):
    """chapter_outline_task 为空时，mandate 区块不渲染。"""
    director, fake_llm = _build_director(tmp_path)
    ctx, prior = director._build_shared_context(99)  # ch99 不在大纲里
    assert ctx["chapter_outline_task"] == ""
    mandate = _render_outline_mandate(ctx)
    assert mandate == "", f"Expected empty mandate for empty task, got: {mandate[:100]}"
    cap_before = len(fake_llm.captured_prompts)
    director._call_scene_skeleton(99, ctx, prior)
    captured = fake_llm.captured_prompts[cap_before]
    assert "大纲核心任务" not in captured, "Mandate block should not appear when task is empty"


def test_outline_mandate_pure_function_ch3():
    """纯函数直接测试 ch3 context。"""
    ctx = {
        "chapter_outline_task": "村长王大牛主持村议，决定是否收留弃婴，埋下迷雾禁婴传闻",
        "adjacent_tasks": "第2章：陈老根拾婴归家，取名陆烬；第4章：陈老根独自抚养陆烬",
        "outline_override_hint": "（大纲细纲优先于plot_graph节点）",
    }
    block = _render_outline_mandate(ctx)
    assert "村长王大牛主持村议" in block
    assert "收留弃婴" in block
    assert "王大牛" in block
    assert "最高优先级" in block
    assert "禁止泛称" in block or "族老" in block or "里正" in block
    assert "第2章" in block
    assert "第4章" in block
    assert "大纲细纲优先" in block


def test_outline_mandate_empty_returns_blank():
    block = _render_outline_mandate({"chapter_outline_task": ""})
    assert block == ""
    block2 = _render_outline_mandate({})
    assert block2 == ""
