# -*- coding: utf-8 -*-
"""CC round-8 离线测试：章内时序/因果一致性门 + 评审三评 + 任务卡时序字段规范化。"""
from types import SimpleNamespace

from novel_engine.quality.continuity_gate import (
    normalize_sequence, build_specs, detect_inversions, continuity_fix_directive,
)
from novel_engine.quality.review_votes import median, should_run_extra_reviews, aggregate
from novel_engine.agents.scene_schema import build_scene_prompt, SceneOutput
from novel_engine.pipeline.pipeline_orchestrator import _normalize_blueprint_beats, _is_coherent_short
from novel_engine.quality.degeneration import is_near_empty


def _scene_out(sid, text, structured=True, beats_cov=0, finish="stop"):
    return SceneOutput(sid, text, "", [str(i) for i in range(beats_cov)],
                       beats_covered=[str(i) for i in range(beats_cov)],
                       structured=structured, finish_reason=finish)


def _bp(sid, seq, goal="", beats=None, states=None, kw=None, narrative=""):
    return {
        "scene_num": sid, "sequence_index": seq, "goal": goal,
        "beats": beats or [], "narrative_time": narrative,
        "do_not_depict_before": states or [], "do_not_depict_keywords": kw or [],
    }


def _scene(sid, text):
    return SimpleNamespace(scene_id=sid, scene_text=text, hook="", beats=[])


# ---------- normalize_sequence ----------

def test_normalize_sequence_unique_and_fallback():
    bps = [_bp(1, 1), _bp(2, 2), _bp(3, 3)]
    order, ok = normalize_sequence(bps)
    assert ok and order == {1: 1, 2: 2, 3: 3}
    # 缺失 sequence_index 时回退 scene_num
    bps2 = [{"scene_num": 1}, {"scene_num": 2}]
    order2, ok2 = normalize_sequence(bps2)
    assert ok2 and order2 == {1: 1, 2: 2}


def test_normalize_sequence_duplicate_is_unorderable():
    bps = [_bp(1, 1), _bp(2, 1), _bp(3, 3)]
    _order, ok = normalize_sequence(bps)
    assert ok is False


# ---------- build_specs ordering ----------

def test_build_specs_sorted_by_sequence():
    bps = {1: _bp(1, 2), 2: _bp(2, 1)}
    scenes = [_scene(1, "甲"), _scene(2, "乙")]
    specs = build_specs(scenes, bps)
    assert [s["scene_id"] for s in specs] == [2, 1]


# ---------- director keyword inversion ----------

def test_director_keyword_inversion_detected():
    bps = {
        1: _bp(1, 1, states=["婴儿已被发现抱回"], kw=[["婴儿", "怀里"]]),
        2: _bp(2, 2, goal="陈老根入雾在焦土发现婴儿并抱回", beats=["捡婴"]),
    }
    scenes = [_scene(1, "村民围着，婴儿被母亲紧紧抱在怀里。"), _scene(2, "他入雾抱起婴儿。")]
    inv = detect_inversions(build_specs(scenes, bps), use_director_keywords=True)
    assert any(i["scene_id"] == 1 and i["source"] == "director" for i in inv)
    # 默认关闭导演关键词，但内置种子规则同样应抓到“被发现前已在母亲怀中”
    inv_seed = detect_inversions(build_specs(scenes, bps))
    assert any(i["scene_id"] == 1 and i["source"] == "seed" for i in inv_seed)


def test_director_keyword_no_false_positive_without_group():
    bps = {1: _bp(1, 1, states=["x"], kw=[["婴儿", "母亲"]]), 2: _bp(2, 2)}
    scenes = [_scene(1, "紫光里一个婴儿坠地，四周无人。"), _scene(2, "天亮了。")]
    inv = detect_inversions(build_specs(scenes, bps), use_director_keywords=True)
    assert inv == []


# ---------- seed baby-carry inversion (the real ch1 case) ----------

def test_seed_baby_pre_discovery_in_arms_detected():
    bps = {
        1: _bp(1, 1, goal="紫雾凝婴坠地", beats=["异象降生"]),
        2: _bp(2, 2, goal="次日村民围观", beats=["议论不祥"]),
        3: _bp(3, 3, goal="陈老根当夜入雾，在焦土发现灰布包裹的婴儿并抱回",
               beats=["入雾", "发现婴儿", "抱回茅屋"]),
    }
    scenes = [
        _scene(1, "紫光散去，焦土上一个灰布婴儿啼哭，周围并无一人。"),
        _scene(2, "鸡鸣初响，村民围拢，婴儿被母亲抱在怀里哄着。"),
        _scene(3, "陈老根拄杖入雾，在灰烬中发现并抱起那婴儿。"),
    ]
    inv = detect_inversions(build_specs(scenes, bps))
    assert any(i["scene_id"] == 2 for i in inv)
    assert not any(i["scene_id"] == 1 for i in inv)  # 降生场景无持有人，不误伤
    assert not any(i["scene_id"] == 3 for i in inv)  # 发现场景本身在更晚，不被检


def test_seed_no_inversion_when_discovery_is_earlier():
    # 发现动作就在本场（没有更晚场景去发现），不得报倒置
    bps = {1: _bp(1, 1, goal="陈老根发现婴儿", beats=["发现婴儿"]), 2: _bp(2, 2, goal="抱回")}
    scenes = [_scene(1, "他在焦土发现婴儿。"), _scene(2, "村民后来见到母亲抱着孩子。")]
    inv = detect_inversions(build_specs(scenes, bps))
    assert inv == []


def test_seed_mere_crowd_presence_is_not_holding():
    # 婴儿已降生躺在焦土，村民仅围观、无人持有：不得误报（真正要件是“持有”）
    bps = {
        1: _bp(1, 1, goal="紫雾凝婴坠地", beats=["异象降生"]),
        2: _bp(2, 2, goal="村民远远围观焦土上的婴儿", beats=["围观"]),
        3: _bp(3, 3, goal="陈老根入雾，在焦土发现并抱起婴儿", beats=["发现婴儿", "抱起"]),
    }
    scenes = [
        _scene(1, "紫光散去，焦土上一个灰布婴儿啼哭，四周无人。"),
        _scene(2, "鸡鸣初响，村民围拢围观，人群议论纷纷，那婴儿仍躺在焦土灰烬里，没人敢碰。"),
        _scene(3, "陈老根拄杖入雾，在灰烬中发现并抱起那婴儿。"),
    ]
    assert detect_inversions(build_specs(scenes, bps)) == []


def test_fix_directive_mentions_state():
    d = continuity_fix_directive({"state": "婴儿已被抱回", "matched": ["婴儿", "怀里"]},
                                 {"narrative_time": "当夜"})
    assert "时序因果倒置" in d and "婴儿已被抱回" in d and "当夜" in d


# ---------- review median-of-3 ----------

def test_median_values():
    assert median([83, 90, 86]) == 86
    assert median([58, 92, 84]) == 84


def test_should_run_extra_reviews_band_and_swing():
    assert should_run_extra_reviews([87]) is True
    assert should_run_extra_reviews([92]) is False
    assert should_run_extra_reviews([58, 87]) is True  # 极差 29 > 10
    assert should_run_extra_reviews([70, 72]) is False


def test_aggregate_unstable_range():
    a = aggregate([58, 84, 92])
    assert a["median"] == 84 and a["range"] == 34 and a["highly_unstable"] is True
    b = aggregate([85, 86, 88])
    assert b["highly_unstable"] is False


# ---------- blueprint temporal field normalization ----------

def test_normalize_blueprint_temporal_fields():
    card = {"scene_blueprints": [
        {"scene_num": 1, "beats": [{"beat": "x"}, "y"],
         "do_not_depict_before": ["状态A", 123, ""],
         "do_not_depict_keywords": [["婴儿", "母亲", "婴儿"], ["单词"], "坏组"]},
        {"scene_num": 2},  # 全缺，安全回退
    ]}
    _normalize_blueprint_beats(card)
    bp1, bp2 = card["scene_blueprints"]
    assert bp1["beats"] == ["x", "y"]
    assert bp1["sequence_index"] == 1 and bp1["narrative_time"] == ""
    assert bp1["do_not_depict_before"] == ["状态A", "123"]
    assert bp1["do_not_depict_keywords"] == [["婴儿", "母亲"]]  # 去重保序、≥2词
    assert bp2["sequence_index"] == 2 and bp2["do_not_depict_before"] == []
    assert bp2["do_not_depict_keywords"] == []


# ---------- coherent-tiny rescue classification ----------

def test_coherent_tiny_valid_beats_is_coherent_despite_near_empty():
    # flash 真机：合法 JSON、beats 全覆盖，却只写约120字 finish=stop
    txt = "陈老根拄杖立在雾边，听着远处隐约的啼哭，指节因用力而泛白。他想起三十年前那夜的火光，脚步迟疑却终究向前。"
    s = _scene_out(3, txt, structured=True, beats_cov=5)
    assert len(txt) < 300 and is_near_empty(txt, 2000)  # 旧规则会当近空
    assert _is_coherent_short(s, 5) is True


def test_empty_and_garbled_are_not_coherent():
    assert _is_coherent_short(_scene_out(1, "", beats_cov=5), 5) is False
    bad = "的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的的"
    assert _is_coherent_short(_scene_out(1, bad, beats_cov=5), 5) is False
    # beats 未覆盖完整 → 不算连贯
    txt = "陈老根立在雾边，听着啼哭，想起往事，终究向前走去。" * 3
    assert _is_coherent_short(_scene_out(1, txt, beats_cov=2), 5) is False
    # 非结构化（散文回退）不享受该豁免，保持旧语义
    assert _is_coherent_short(_scene_out(1, txt, structured=False, beats_cov=5), 5) is False


# ---------- writer prompt temporal injection ----------

def test_scene_prompt_contains_temporal_lock():
    card = {"chapter_num": 1, "scene_blueprints": [
        {"scene_num": 1, "location": "焦土", "characters": [], "goal": "降生",
         "conflict": "c", "emotion": "e", "beats": ["b1", "b2", "b3"],
         "sequence_index": 1, "narrative_time": "当夜子时",
         "do_not_depict_before": ["婴儿已被村民抱走"]},
        {"scene_num": 2, "location": "雾", "characters": [], "goal": "捡婴",
         "conflict": "c", "emotion": "e", "beats": ["b1", "b2", "b3"],
         "sequence_index": 2, "narrative_time": "当夜丑时", "do_not_depict_before": []},
    ], "chapter_hook": "敲门声"}
    p = build_scene_prompt(card, card["scene_blueprints"][0], card["scene_blueprints"])
    assert "时序因果锁" in p and "当夜子时" in p and "婴儿已被村民抱走" in p


def _specs_with_discovery_later(s1_text):
    # 场景2 更晚，蓝图含 实体(婴儿) + 发现/抱起 动作，触发种子规则的“later discovers”
    bp1 = _bp(1, 1, goal="村民聚集议论", beats=["村民远观荒坡异象"])
    bp2 = _bp(2, 2, goal="陈老根发现并抱起婴儿", beats=["陈老根抱起婴儿", "抱婴儿回村"])
    return build_specs([_scene_out(1, s1_text), _scene_out(2, "后文")], {1: bp1, 2: bp2})


def test_seed_inversion_quoted_debate_not_flagged():
    text = "众人远远围着，有人低声道：“这种时候，谁敢把那婴儿抱在怀里！”说罢纷纷后退，无人上前。"
    inv = detect_inversions(_specs_with_discovery_later(text))
    assert inv == []


def test_seed_inversion_hypothetical_not_flagged():
    text = "他心里一动，想上前把婴儿搂进怀里，却又不敢，脚步只抬起半寸便缩了回去。"
    inv = detect_inversions(_specs_with_discovery_later(text))
    assert inv == []


def test_seed_inversion_affirmative_holding_flagged():
    text = "他不再犹豫，快步上前，把那婴儿稳稳抱在怀里，转身便走。"
    inv = detect_inversions(_specs_with_discovery_later(text))
    assert inv and inv[0]["scene_id"] == 1 and inv[0]["source"] == "seed"


# ---------- CC round-11 R1：prior_acquired_entities 跨章豁免 ----------

def test_prior_acquired_skips_seed_rule():
    """ch2末已收养婴儿，ch3任何场景抱着婴儿均不判倒置（R1）。"""
    bp1 = _bp(1, 1, goal="村口老槐树下村长召集议事", beats=["村长敲鼓"])
    bp2 = _bp(2, 2, goal="祠堂表决收留弃婴", beats=["举手表决"])
    bp3 = _bp(3, 3, goal="陈老根向村民解释婴儿来历并抱回", beats=["解释来历", "抱回"])
    scenes = [
        _scene(1, "陈老根怀里抱着襁褓，走在老槐树下。"),
        _scene(2, "祠堂里众人讨论是否收留。"),
        _scene(3, "村民问起这孩子哪来的。"),
    ]
    specs = build_specs(scenes, {1: bp1, 2: bp2, 3: bp3})
    # 无 prior_acquired → 仍应报 scene1 倒置
    inv_no_prior = detect_inversions(specs)
    assert any(i["scene_id"] == 1 for i in inv_no_prior)
    # 有 prior_acquired（ch2末已抱回）→ 不报倒置
    inv_with_prior = detect_inversions(specs, prior_acquired_entities={"婴儿", "襁褓", "孩子"})
    assert not any(i["scene_id"] == 1 for i in inv_with_prior)


def test_prior_acquired_empty_set_no_skip():
    """prior_acquired_entities={} 不应跳过任何规则。"""
    inv = detect_inversions(_specs_with_discovery_later("他抱着婴儿走入夜色。"), prior_acquired_entities=set())
    assert inv and inv[0]["source"] == "seed"


def test_extract_prior_acquired_entities_basic():
    from novel_engine.quality.continuity_gate import extract_prior_acquired_entities
    es = {"completed_actions": ["陈老根把婴儿抱回茅屋收养", "给孩子取名陆烬"], "narrative_position": "陈老根抱着襁褓入睡"}
    result = extract_prior_acquired_entities(es)
    assert "婴儿" in result
    assert "襁褓" in result


def test_extract_prior_acquired_entities_no_match():
    from novel_engine.quality.continuity_gate import extract_prior_acquired_entities
    es = {"completed_actions": ["村民散去", "夜幕降临"], "narrative_position": "村庄恢复平静"}
    assert extract_prior_acquired_entities(es) == set()


def test_extract_prior_acquired_entities_none():
    from novel_engine.quality.continuity_gate import extract_prior_acquired_entities
    assert extract_prior_acquired_entities(None) == set()
    assert extract_prior_acquired_entities({}) == set()


# ---------- CC round-11 R1b：真实 ch1/ch2 end_state 回归 ----------

def test_real_ch2_endstate_yields_acquired_entities():
    """真实 ch2 end_state（completed_actions 含'抱起带回'）应提取出 {'婴儿'}。"""
    from novel_engine.quality.continuity_gate import extract_prior_acquired_entities
    ch2_es = {
        "chapter_id": 2,
        "narrative_position": "陈老根坐在炕边，看着被包裹在简陋襁褓中、呼吸微弱的婴儿陆烬，"
                               "低声念出他的名字后陷入沉默。屋内油灯光线昏暗，火盆炭火将熄未熄，"
                               "屋外寒风呜咽。",
        "location": "雾隐村陈老根家中",
        "completed_actions": [
            "陈老根决定出门探查",
            "陈老根发现枯荣痕迹与中心浅坑",
            "陈老根确认婴儿存活并将其抱起带回",
            "陈老根为婴儿擦洗、包裹、取名'陆烬'",
        ],
        "pending_actions": [
            "村中天亮后对昨夜异象的反应与流言",
            "陈老根如何向村民解释婴儿来历",
            "婴儿陆烬的初步存活与成长挑战",
        ],
        "time_marker": "夜（丑时末）",
    }
    result = extract_prior_acquired_entities(ch2_es)
    # 动词"抱起带回"应命中，且实体词"婴儿"也在 narrative_position 中出现
    assert "婴儿" in result or "襁褓" in result, f"Expected entity extraction from real ch2; got {result}"


def test_real_ch1_endstate_returns_empty():
    """真实 ch1 end_state（仅发现/窥见/目击，无获得动词）应返回空集，
    保证 ch2 真正的获得章仍会被正常检测，不被 R1 抑制。"""
    from novel_engine.quality.continuity_gate import extract_prior_acquired_entities
    ch1_es = {
        "chapter_id": 1,
        "narrative_position": "镜头从冻土坡上孤零零的婴儿（身陷枯荣痕迹中央）拉远，"
                               "扫过死寂的荒原，最后定格在雾隐村村西头一扇黑暗的窗户后，"
                               "陈老根静止不动的剪影上。寒风呜咽。",
        "location": "雾隐村外冻土坡 / 雾隐村村西陈老根家窗外（双焦点）",
        "completed_actions": [
            "李淳神魂跨界完成并降生为婴儿",
            "婴儿引发系列可见物理异象（荧光、雾涡、光柱、光环、草木枯荣）",
            "村民目击异象并产生集体恐慌",
            "陈老根窥见异象并产生异常反应",
        ],
        "pending_actions": [
            "陈老根是否会出门查看",
            "婴儿能否存活至被发现",
            "村民天亮后的反应",
        ],
        "time_marker": "深夜（丑时初）",
    }
    result = extract_prior_acquired_entities(ch1_es)
    # ch1 只有发现/窥见/目击等认知动词，无获取动词，必须返回空
    assert result == set(), f"ch1 end_state should yield empty prior set; got {result}"


def test_ch3_with_real_ch2_prior_no_inversion():
    """用真实 ch2 end_state 提取 prior，再对 ch3 场景（怀抱婴儿+后景蓝图含'捡来'）
    调用 detect_inversions，应返回 []（R1 生效）。"""
    from novel_engine.quality.continuity_gate import (
        extract_prior_acquired_entities, build_specs, detect_inversions,
    )
    from types import SimpleNamespace as S

    ch2_es = {
        "completed_actions": [
            "陈老根确认婴儿存活并将其抱起带回",
            "陈老根为婴儿擦洗、包裹、取名'陆烬'",
        ],
        "narrative_position": "陈老根坐在炕边，看着被包裹在简陋襁褓中、呼吸微弱的婴儿陆烬",
    }
    prior = extract_prior_acquired_entities(ch2_es)
    assert prior, f"prior should not be empty for real ch2: got {prior}"

    # ch3 场景：scene1 怀抱婴儿，scene3 蓝图中出现"捡来/发现"
    bp1 = {"scene_num": 1, "sequence_index": 1, "goal": "村口老槐树下村长召集议事",
           "beats": ["村长敲鼓"]}
    bp3 = {"scene_num": 3, "sequence_index": 3, "goal": "陈老根向村民解释弃婴来历并抱回",
           "beats": ["解释来历", "抱回婴儿"]}
    scenes = [
        S(scene_id=1, scene_text="陈老根怀里抱着襁褓，走在老槐树下。"),
        S(scene_id=2, scene_text="祠堂里众人讨论是否收留。"),
        S(scene_id=3, scene_text="村民问起这孩子哪来的。"),
    ]
    specs = build_specs(scenes, {1: bp1, 3: bp3})
    inv = detect_inversions(specs, prior_acquired_entities=prior)
    # 不应再有倒置：ch2 已抱回婴儿，ch3 怀抱是延续而非提前
    assert inv == [], f"Expected no inversions with real ch2 prior; got {inv}"

