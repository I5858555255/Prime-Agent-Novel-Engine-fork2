# -*- coding: utf-8 -*-
"""CC round-7 P0-3 boundary Stage1 scoring + Stage2 verdict parsing."""
from novel_engine.pipeline.boundary_guard import (
    stage1_score, scene_overlap_scores, parse_stage2_verdict, build_stage2_prompt,
)

PREV = (
    "陈老根提着那盏旧油灯走进终年不散的浓雾里，脚下的泥路被夜露浸得发黑。"
    "他听见一声极轻的啼哭，像是从枯树根部传来。他拨开低垂的枝条，看见一个裹在青白气晕里的婴儿。"
    "那婴儿睁开眼，瞳底有一点幽绿。陈老根怔住良久，最终扯下外袍将婴儿裹住，"
    "替他挡住渗人的雾气，低声说跟我回家。他抱起孩子转身朝村子方向走，身影被浓雾吞没。"
)


def test_stage1_flags_large_replay():
    # current chapter re-stages most of the previous chapter verbatim, then adds a bit
    cur = PREV[:120] + PREV[40:110] + "村口的火把亮了起来。"
    st = stage1_score(cur, PREV)
    assert st["candidate"] is True
    assert st["containment"] > 0.10 or st["cosine"] > 0.60


def test_stage1_passes_distinct_content():
    cur = (
        "次日清晨，沈知微在药庐前晒着新采的草药，竹匾里的叶片还带着露水。"
        "上官烈策马经过，带来边关急报，两人就着茶汤低声商议对策。"
    )
    st = stage1_score(cur, PREV)
    assert st["candidate"] is False


def test_scene_overlap_scores_keys():
    scores = scene_overlap_scores({1: PREV[:90], 2: "完全不同的全新情节在别处展开。"}, PREV)
    assert set(scores) == {1, 2}
    assert scores[1] > scores[2]


def test_parse_verdict_variants():
    v1 = parse_stage2_verdict('```json\n{"replay": true, "confidence": 0.9, "reason": "整段重演"}\n```')
    assert v1["replay"] is True and v1["ok"] is True
    v2 = parse_stage2_verdict('结果是 {"replay": false, "confidence": 0.2, "reason": "仅一句回扣"}')
    assert v2["replay"] is False
    v3 = parse_stage2_verdict("这不是JSON")
    assert v3["ok"] is False and v3["replay"] is False  # fail-safe: no replay


def test_stage2_prompt_contains_criteria_and_context():
    p = build_stage2_prompt(
        {"narrative_position": "停点", "completed_actions": ["捡婴"], "pending_actions": ["进村"]},
        PREV, "新章开头")
    assert "四条" in p and "捡婴" in p and "进村" in p
