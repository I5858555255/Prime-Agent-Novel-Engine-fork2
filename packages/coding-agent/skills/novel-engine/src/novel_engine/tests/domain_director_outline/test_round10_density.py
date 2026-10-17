# -*- coding: utf-8 -*-
"""CC round-10 P0-1 内容密度门离线测试。"""
from novel_engine.quality import density_gate as dg

CFG = {
    "coverage_threshold": 0.8,
    "event_bigram_ratio": 0.2,
    "event_min_bigrams": 2,
    "function_chars": "了的是在他她它一有不和人与又就都很像被把让给而却但也还着过个上中下里",
    "arc_overrides": {"infant": {"protagonist_interiority": 1}},
    "interiority_markers": ["他意识到", "他想", "前世"],
}

BP = {
    "scene_num": 1,
    "characters": ["老赵头", "张三嫂"],
    "beats": ["老赵头发现异象敲响铜锣"],
    "concrete_events": [
        {"event": "老赵头发现异象敲响铜锣警示全村", "observable_action": "老赵头用力敲响铜锣三声"},
        {"event": "张三嫂冲出家门试图靠近荒坡上的婴儿", "observable_action": "张三嫂往坡上冲"},
    ],
    "named_interactions": [
        {"characters": ["老赵头", "张三嫂"], "interaction_type": "劝阻", "brief": "老赵头厉声拦住想靠近婴儿的张三嫂"}
    ],
    "info_reveal_points": [],
}

FULL = (
    "老赵头望见昆仑方向裂开一线幽蓝，他意识到事情不对，立刻用力敲响铜锣三声，警示全村起身。"
    "张三嫂披衣冲出家门，抬脚往坡上冲，试图靠近荒坡上的婴儿。老赵头一个箭步上前，厉声拦住张三嫂，死活不让她过去。"
)
THIN = "夜雾越来越浓，四野寂静，只有风吹过荒草的声音，空气里透着一股说不清的凉意，黑暗层层叠叠地压下来。"


def test_normalize_fills_from_beats_and_characters():
    out = dg.normalize_scene_density({"characters": ["甲", "乙"], "beats": ["第一件事", "第二件事"]})
    assert len(out["concrete_events"]) == 2
    assert out["named_interactions"] and out["named_interactions"][0]["characters"] == ["甲", "乙"]


def test_full_scene_covers_density():
    res = dg.evaluate_scene(BP, FULL, CFG)
    assert res["ratio"] >= 0.8, res
    assert res["missing"] == []
    assert res["interiority"] is True


def test_thin_scene_fails_density():
    res = dg.evaluate_scene(BP, THIN, CFG)
    assert res["ratio"] < 0.8
    assert len(res["missing"]) == 3  # 2 events + 1 interaction all unperformed
    assert res["interiority"] is False


def test_directive_lists_missing_and_interiority():
    res = dg.evaluate_scene(BP, THIN, CFG)
    d = dg.density_directive(res, protagonist_interiority=1)
    assert "可观察动作" in d
    assert "评判性内在观察" in d


def test_requirements_arc_override():
    req = dg.requirements_for(CFG, "infant")
    assert int(req.get("protagonist_interiority", 0)) == 1
    req2 = dg.requirements_for(CFG, "adult")
    assert int(req2.get("protagonist_interiority", 0)) == 0
