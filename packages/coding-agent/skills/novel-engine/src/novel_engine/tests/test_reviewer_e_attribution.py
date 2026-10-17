# -*- coding: utf-8 -*-
"""E 包：reviewer 扣分归因 scene_ids + 场景级返工指令注入（确定性，不打网络）。"""
from novel_engine.agents.reviewer_agent import _coerce_scene_ids, _normalize_review
from novel_engine.agents.scene_schema import build_scene_prompt


def test_coerce_scene_ids_variants():
    assert _coerce_scene_ids(None) == []
    assert _coerce_scene_ids(3) == [3]
    assert _coerce_scene_ids("场景3与场景4") == [3, 4]
    assert _coerce_scene_ids([1, "2", 1, "3"]) == [1, 2, 3]
    assert _coerce_scene_ids([0, -1, 2]) == [2]
    assert _coerce_scene_ids(True) == []
    assert _coerce_scene_ids({"x": 1}) == []


def test_normalize_review_populates_scene_ids():
    review = {
        "chapter_num": 1,
        "scores": {k: v for k, v in zip(
            ["plot_consistency", "character_consistency", "foreshadow_execution",
             "style_match", "pacing", "innovation", "hook_strength",
             "reader_retention", "cliffhensity"],
            [20, 16, 16, 12, 8, 8, 6, 6, 4])},
        "issues": [
            {"dimension": "pacing", "severity": "high",
             "scene_ids": "场景3", "description": "与场景2重复", "suggested_fix": "删除重复"},
        ],
    }
    out = _normalize_review(dict(review))
    assert out["issues"][0]["scene_ids"] == [3]
    assert isinstance(out["normalized_score"], float)


def _card():
    bp = lambda n: {"scene_num": n, "location": "村", "characters": ["C001"],
                    "goal": "一个足够充实的目标描述内容", "conflict": "c", "emotion": "e",
                    "word_count_target": 2500,
                    "beats": ["第一拍事件", "第二拍事件", "第三拍事件"]}
    return {"chapter_num": 1, "core_goal": "核心目标", "chapter_hook": "章末钩子",
            "scene_blueprints": [bp(1), bp(2)]}


def test_scene_prompt_includes_fix_directive_only_when_given():
    card = _card()
    bps = card["scene_blueprints"]
    p1 = build_scene_prompt(card, bps[0], bps, fix_directive="删除与场景2重复的围观描写")
    assert "审查返工指令" in p1
    assert "删除与场景2重复的围观描写" in p1
    p2 = build_scene_prompt(card, bps[0], bps)
    assert "审查返工指令" not in p2
