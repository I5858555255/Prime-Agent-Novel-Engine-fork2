# -*- coding: utf-8 -*-
"""契约一致性回归：提示词声明的合法字段形态，校验器必须接受。

防止"提示词允许 X 但 validate_* 判 X 失败"的 schema 不一致再次出现
（ch2 死锁根因：DRAFT 允许无对白场景 dialogue_intents=[]，但旧
validate_chapter 用 strings() 要求非空 → 判失败 → 评审 3 轮修不掉）。

本测试不依赖 LLM / 网络；纯结构断言。
"""
from __future__ import annotations

import pytest

from novel_engine.condensed.contracts import (ProductionConfig,
                                              validate_chapter, validate_plan)


def _minimal_plan(chapter=2):
    """PLAN 提示词允许的最小合法规划（3 事件、cast、boundaries、预算=默认 4210）。"""
    return {
        "chapter": chapter,
        "objective": "测试任务",
        "cast": ["陆烬", "陈老根"],
        "events": [
            {"id": f"CH{chapter}-E{i}", "action": f"动作{i}", "cause": f"前因{i}", "outcome": f"结果{i}"}
            for i in range(1, 4)
        ],
        "ending": "结束状态",
        "boundaries": ["不得提前揭露机制"],
        "milestone_ids": [],
        "expanded_han": 4211,
    }


def _scene(sid, cast, beats, dialogue_intents, location="雾隐村"):
    """DRAFT 提示词允许的最小合法场景；dialogue_intents 为提示词允许的任意形态。"""
    return {
        "id": sid,
        "location": location,
        "time": "夜晚",
        "pov": "陆烬",
        "cast": cast,
        "start": "起始",
        "beats": beats,
        "dialogue_intents": dialogue_intents,
        "emotion": "平静",
        "end": "结束",
        "expanded_han": 2106,
        "expansion_weight": 1,
        "expansion_guidance": "按场景扩写",
        "text": "场景文本",
    }


def _chapter_with_scene(plan_chapter=2, dialogue_intents=None):
    """两场景最小章节：S1 用给定 dialogue_intents，S2 正常对白覆盖 E2/E3。"""
    s1 = _scene("S1", ["陆烬"],
                [{"id": "S1-B1", "event_ids": [f"CH{plan_chapter}-E1"],
                  "action": "动作", "cause": "因果", "outcome": "结果"}],
                dialogue_intents)
    s1["expanded_han"] = 2105
    s2 = _scene("S2", ["陈老根"],
                [{"id": "S2-B1", "event_ids": [f"CH{plan_chapter}-E2", f"CH{plan_chapter}-E3"],
                  "action": "动作", "cause": "因果", "outcome": "结果"}],
                ["陈老根向陆烬说明水源"], location="屋内")
    return {
        "chapter": plan_chapter,
        "title": "测试章",
        "story_day": 1,
        "scenes": [s1, s2],
        "state_changes": [],
        "ending": "结束",
    }


# DRAFT 提示词："无对白则注明原因" → 空列表 / 未给 / 注明原因 / 正常对白 / 空串 都合法。
@pytest.mark.parametrize("dialogue_intents", [
    [],                              # 无对白（合法，提示词约定填空数组）
    ["无对白，婴儿感官场景"],          # 注明原因（合法）
    ["陈老根对陆烬说：今晚留水"],       # 正常对白（合法）
])
def test_draft_prompt_allows_scene_without_dialogue(dialogue_intents):
    """提示词允许的 dialogue_intents 形态，校验器不得报"缺少对话意图"。

    提示词约定"无对白填空数组[]"；[] 与 注明原因串 均合法。
    """
    chapter = _chapter_with_scene(2, dialogue_intents)
    plan = _minimal_plan(2)
    errors = validate_chapter(chapter, plan, {}, 0)
    assert not any("对话意图" in e for e in errors), f"误判对话意图：{errors}"


def test_validate_plan_accepts_prompt_minimal_plan():
    """PLAN 提示词允许的最小规划（3 事件、无里程碑）必须通过。"""
    cfg = ProductionConfig()
    errors = validate_plan({"chapters": [_minimal_plan(2)]}, 2, 2, cfg)
    assert not errors, f"最小规划被判失败：{errors}"


def test_validate_plan_rejects_missing_milestone_reference():
    """有里程碑的章节，规划必须引用全部 milestone_ids。"""
    cfg = ProductionConfig()
    plan = _minimal_plan(2)
    milestones = [{"id": "N002", "chapter_target": 2, "description": "测试节点"}]
    plan["milestone_ids"] = []  # 漏引
    errors = validate_plan({"chapters": [plan]}, 2, 2, cfg, milestones=milestones)
    assert any("N002" in e for e in errors)


def test_wuduibai_note_is_not_a_mechanism_term():
    """'无对白'是 DRAFT 提示词允许的合法场景说明，不得被禁词扫描误判为机制词。"""
    # 模拟一个婴儿感官场景：扩写指引含"无对白"，beat 含"无对白"，不得报禁词。
    chapter = _chapter_with_scene(1, [])  # 无对白
    chapter["scenes"][0]["expansion_guidance"] = "无对白；只写感官层（冷、光线、抓握）"
    chapter["scenes"][0]["beats"][0]["action"] = "无对白；陆烬抓握陈老根拇指"
    plan = _minimal_plan(1)
    errors = validate_chapter(chapter, plan, {}, 0)
    forbidden_hits = [e for e in errors if "禁词" in e]
    assert not forbidden_hits, f"'无对白'被误判为禁词：{forbidden_hits}"


def test_negative_directive_not_a_mechanism_leak():
    """SYSTEM 第 6 条允许"不写X/不命名X"负向指令作为边界说明；禁词扫描须剥离，
    避免把'不命名异界神魂'误判为'提到了异界神魂'（ch1 死锁根因）。"""
    chapter = _chapter_with_scene(1, [])
    chapter["scenes"][0]["expansion_guidance"] = (
        "不写自我认知，不写逻辑梳理，不命名异界神魂，不写气感，使用中性观察语言"
    )
    plan = _minimal_plan(1)
    errors = validate_chapter(chapter, plan, {}, 0)
    forbidden_hits = [e for e in errors if "禁词" in e]
    assert not forbidden_hits, f"负向指令被误判为机制词泄漏：{forbidden_hits}"
