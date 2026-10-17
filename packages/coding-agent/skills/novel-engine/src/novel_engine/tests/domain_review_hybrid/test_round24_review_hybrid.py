# -*- coding: utf-8 -*-
"""CC round-24 P0-1：pacing/retention 混合分 + 异常票剔除 + 全绿诊断（纯确定性单测）。"""
from novel_engine.quality import review_hybrid as rh


def _full_other_dims():
    return {
        "plot_consistency": 25, "character_consistency": 20,
        "foreshadow_execution": 20, "style_match": 15, "innovation": 10,
        "hook_strength": 8, "cliffhensity": 5,
    }


def test_deterministic_components_values():
    # mean_atm=0.3, beat=0.9 -> ((1-0.3)*0.5 + 0.9*0.5)*10 = 8.0
    sig = {"mean_atmosphere_ratio": 0.3, "beat_coverage_ratio": 0.9,
           "adjacent_atmosphere_overlap_mean": 0.2}
    assert abs(rh.pacing_deterministic(sig) - 8.0) < 1e-6
    # retention: (1-0.2)*7 = 5.6
    assert abs(rh.retention_deterministic(sig) - 5.6) < 1e-6


def test_hybridize_only_pacing_retention_change_and_total_maps_to_100():
    sig = {"mean_atmosphere_ratio": 0.3, "beat_coverage_ratio": 0.9,
           "adjacent_atmosphere_overlap_mean": 0.2}
    review = {"scores": {**_full_other_dims(), "pacing": 2, "reader_retention": 1}}
    rh.hybridize_review(review, sig)
    # pacing: 8.0*0.6 + 2*0.4 = 5.6 ; retention: 5.6*0.6 + 1*0.4 = 3.76
    assert abs(review["scores"]["pacing"] - 5.6) < 1e-6
    assert abs(review["scores"]["reader_retention"] - 3.76) < 1e-6
    # 其它 7 维不动
    assert review["scores"]["plot_consistency"] == 25
    assert review["scores"]["innovation"] == 10
    # 103(其它维满分) + 5.6 + 3.76 = 112.36 -> 93.6 百分位（仍可直接与 88 线比较）
    assert review["normalized_score"] == 93.6
    assert review["hybrid_scored"] is True


def test_valid_vote_technical_filters():
    assert rh.is_valid_vote({"_llm_raw_total": 90})
    assert not rh.is_valid_vote({"_llm_raw_total": 0})      # flash 解析失败 score=0
    assert not rh.is_valid_vote({"raw_total": 121})         # 越界
    assert not rh.is_valid_vote({})                         # 不可解析


def test_hard_gate_contradiction_only_when_clean_and_latin_claim():
    rv = {"issues": [{"dimension": "style_match",
                      "description": "正文混入英文单词 steady，不合古风"}]}
    assert rh.hard_gate_contradicted_vote(rv, hard_gates_all_clean=True)
    # 硬门未全 clean 时不判幻觉（可能确有泄漏）
    assert not rh.hard_gate_contradicted_vote(rv, hard_gates_all_clean=False)
    # 主观差评（不含字符级拉丁/英文断言）即便硬门 clean 也绝不能当幻觉剔除
    rv2 = {"issues": [{"dimension": "pacing", "description": "节奏拖沓，雾景反复"}]}
    assert not rh.hard_gate_contradicted_vote(rv2, hard_gates_all_clean=True)


def test_filter_drops_invalid_and_hallucinated_keeps_low_but_valid():
    pairs = [
        ({"_llm_raw_total": 0, "normalized_score": 0}, 0.0),
        ({"_llm_raw_total": 90, "normalized_score": 75.0,
          "issues": [{"description": "节奏平庸"}]}, 75.0),
        ({"_llm_raw_total": 100, "normalized_score": 83.3}, 83.3),
    ]
    kept = rh.filter_valid_votes(pairs, hard_gates_all_clean=True)
    assert [round(s) for _, s in kept] == [75.0, 83.0]


def test_signals_all_green_and_diagnose():
    green = {"hard_gates_all_clean": True, "beat_coverage_ratio": 0.9,
             "atmosphere_ratio_per_scene": {"s1": 0.3, "s2": 0.4}}
    assert rh.signals_all_green(green)
    assert not rh.signals_all_green({**green, "beat_coverage_ratio": 0.5})
    assert not rh.signals_all_green({**green, "hard_gates_all_clean": False})
    assert not rh.signals_all_green(
        {**green, "atmosphere_ratio_per_scene": {"s1": 0.6}})
    assert rh.diagnose_low_score(90, green) == "publish_track"
    assert rh.diagnose_low_score(70, green) == "suspected_reviewer_bias"
    not_green = {**green, "beat_coverage_ratio": 0.5}
    assert rh.diagnose_low_score(70, not_green) == "genuine_quality_issue"


def test_build_signals_smoke_keys_and_ranges():
    task_card = {"scene_blueprints": [
        {"scene_num": 1, "scene_progression_contract":
         {"new_state_or_entity": ["光点坠地"], "irreversible_change": "被抱起"}},
        {"scene_num": 2, "scene_progression_contract":
         {"new_state_or_entity": ["取名"], "irreversible_change": "赐名"}},
    ]}
    scenes = [
        {"scene_id": 1, "scene_text": "他睁开眼看见了光点。他伸手握住那物。有人迈步走进屋内。"
                                       "那人开口说了一句话。他被人抱起带走。"},
        {"scene_id": 2, "scene_text": "老人赐名陆烬。陈老根点头答应。众人安静站着听。"
                                       "孩子啼了一声。火光在墙上跳动。"},
    ]
    sig = rh.build_deterministic_signals(task_card, scenes, root=None,
                                         hard_gates_clean=True)
    assert 0.0 <= sig["beat_coverage_ratio"] <= 1.0
    assert 0.0 <= sig["mean_atmosphere_ratio"] <= 1.0
    assert 0.0 <= sig["adjacent_atmosphere_overlap_mean"] <= 1.0
    assert set(sig["new_state_coverage"]) == {"s1", "s2"}
    assert sig["hard_gates_all_clean"] is True
