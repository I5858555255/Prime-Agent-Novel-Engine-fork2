# -*- coding: utf-8 -*-
"""CC round-30：任务卡章内场景时间顺序/跨日校验（ch141 时间倒置回归）。

纯函数测试，无 LLM。
"""
from __future__ import annotations

from novel_engine.agents.chapter_director import (
    detect_task_card_scene_time_inversion,
    _scene_time_order,
)


def test_time_order_basic():
    assert _scene_time_order("清晨") == 1
    assert _scene_time_order("午后") == 4
    assert _scene_time_order("深夜") == 8
    assert _scene_time_order("未知时段") == 0


def test_inversion_detected_afternoon_then_morning():
    """ch141 场景倒置样本：1午后→2上午/傍晚→3午后→4黄昏 必须报错。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "午后"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "上午"},
        {"scene_num": 3, "sequence_index": 3, "narrative_time": "午后"},
        {"scene_num": 4, "sequence_index": 4, "narrative_time": "黄昏"},
    ]
    errors = detect_task_card_scene_time_inversion(scenes)
    assert errors, "下午→上午 应判倒置"
    assert "场景2" in errors[0]


def test_monotonic_times_no_error():
    """清晨→上午→午后→黄昏 顺序正常，无报错。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "清晨"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "上午"},
        {"scene_num": 3, "sequence_index": 3, "narrative_time": "午后"},
        {"scene_num": 4, "sequence_index": 4, "narrative_time": "黄昏"},
    ]
    assert detect_task_card_scene_time_inversion(scenes) == []


def test_backref_scene_skipped():
    """含回溯词（回忆/昨夜）的场景不参与倒置判定。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "午后"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "回忆昨夜"},
        {"scene_num": 3, "sequence_index": 3, "narrative_time": "黄昏"},
    ]
    assert detect_task_card_scene_time_inversion(scenes) == []


def test_unknown_time_keeps_chain():
    """未知时间词不断链：午后→(月上柳梢)→清晨 仍应判倒置。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "午后"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "月上柳梢"},
        {"scene_num": 3, "sequence_index": 3, "narrative_time": "清晨"},
    ]
    errors = detect_task_card_scene_time_inversion(scenes)
    assert errors, "跨未知时间词的倒置应被检出"
    assert "场景3" in errors[0]


def test_validate_task_card_catches_inversion():
    """validate_task_card 应包含时间倒置错误（接入点回归）。"""
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)  # 仅测校验，不构造完整实例
    director._bible_cache = {}
    card = {
        "core_goal": "g", "conflicts": [], "emotion_curve": [], "chapter_hook": "h",
        "scene_blueprints": [
            {"scene_num": 1, "narrative_time": "午后", "location": "a", "characters": ["x"],
             "goal": "g1", "conflict": "c1", "emotion": "e1"},
            {"scene_num": 2, "narrative_time": "上午", "location": "b", "characters": ["x"],
             "goal": "g2", "conflict": "c2", "emotion": "e2"},
            {"scene_num": 3, "narrative_time": "黄昏", "location": "c", "characters": ["x"],
             "goal": "g3", "conflict": "c3", "emotion": "e3"},
        ],
    }
    errors = director.validate_task_card(card, 141)
    assert any("倒置" in e for e in errors), f"expect time inversion error, got {errors}"


def test_overnight_progression_not_inversion():
    """CC round-31 回归：深夜→次日清晨 是合法跨日推进，不得判倒置（ch151）。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "深夜"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "次日清晨"},
        {"scene_num": 3, "sequence_index": 3, "narrative_time": "次日午后"},
        {"scene_num": 4, "sequence_index": 4, "narrative_time": "次日黄昏"},
    ]
    assert detect_task_card_scene_time_inversion(scenes) == [], "深夜→次日清晨 不应判倒置"


def test_next_day_then_bare_slot_same_day_not_inversion():
    """次日清晨 → 午后：裸时段词承接次日日期档（同指次日），不判倒置。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "次日清晨"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "午后"},
    ]
    assert detect_task_card_scene_time_inversion(scenes) == [], "次日清晨→午后 同指次日，不应判倒置"


def test_bare_morning_after_next_day_overnight_not_inversion():
    """深夜 → 次日清晨 → 上午：跨日推进 + 裸时段承接次日，全程不判倒置（ch151 核心）。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "深夜"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "次日清晨"},
        {"scene_num": 3, "sequence_index": 3, "narrative_time": "上午"},
        {"scene_num": 4, "sequence_index": 4, "narrative_time": "黄昏"},
    ]
    assert detect_task_card_scene_time_inversion(scenes) == [], "深夜→次日清晨→上午 不应判倒置"


def test_deep_night_to_morning_implicit_overnight_not_inversion():
    """深夜 → 拂晓/上午：无显式跨日词，但深夜后接清晨类是隐含跨日，不得判倒置。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "深夜"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "拂晓"},
        {"scene_num": 3, "sequence_index": 3, "narrative_time": "上午"},
    ]
    assert detect_task_card_scene_time_inversion(scenes) == [], "深夜→拂晓→上午 是隐含跨日，不应判倒置"


def test_deep_night_to_afternoon_is_inversion():
    """深夜 → 当日午后（无跨日词）：非清晨类回落，仍判倒置（督促写'次日午后'）。"""
    scenes = [
        {"scene_num": 1, "sequence_index": 1, "narrative_time": "深夜"},
        {"scene_num": 2, "sequence_index": 2, "narrative_time": "午后"},
    ]
    errors = detect_task_card_scene_time_inversion(scenes)
    assert errors, "深夜 → 午后 应判倒置"


def test_day_shift_order():
    """跨日偏移：次日清晨=11 > 深夜=8；第三日=31、三天后午后=34 按天数递增。"""
    assert _scene_time_order("次日清晨") == 11
    assert _scene_time_order("翌日黄昏") == 15
    assert _scene_time_order("第三日清晨") == 31
    assert _scene_time_order("三天后午后") == 34
    assert _scene_time_order("深夜") == 8


def test_validate_task_card_span_allows_overnight():
    """CC round-31 回归：span 自身允许跨日时，场景含'次日'不报章级矛盾（ch151）。"""
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    director._bible_cache = {}
    card = {
        "core_goal": "g", "conflicts": [], "emotion_curve": [], "chapter_hook": "h",
        "timeline_anchor": {"max_time_progression": "约24小时（深夜至次日黄昏）"},
        "scene_blueprints": [
            {"scene_num": 1, "narrative_time": "深夜", "location": "a", "characters": ["x"],
             "goal": "g1", "conflict": "c1", "emotion": "e1"},
            {"scene_num": 2, "narrative_time": "次日清晨", "location": "b", "characters": ["x"],
             "goal": "g2", "conflict": "c2", "emotion": "e2"},
            {"scene_num": 3, "narrative_time": "次日午后", "location": "c", "characters": ["x"],
             "goal": "g3", "conflict": "c3", "emotion": "e3"},
        ],
    }
    errors = director.validate_task_card(card, 151)
    assert not any("跨日" in e for e in errors), f"span 允许跨日时不应报章级矛盾, got {errors}"


def test_validate_task_card_span_same_day_blocks_next_day():
    """span 为纯当日时，场景含'次日'仍应报章级矛盾（合法拦截）。"""
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    director._bible_cache = {}
    card = {
        "core_goal": "g", "conflicts": [], "emotion_curve": [], "chapter_hook": "h",
        "timeline_anchor": {"max_time_progression": "当日清晨至黄昏"},
        "scene_blueprints": [
            {"scene_num": 1, "narrative_time": "清晨", "location": "a", "characters": ["x"],
             "goal": "g1", "conflict": "c1", "emotion": "e1"},
            {"scene_num": 2, "narrative_time": "次日清晨", "location": "b", "characters": ["x"],
             "goal": "g2", "conflict": "c2", "emotion": "e2"},
        ],
    }
    errors = director.validate_task_card(card, 151)
    assert any("跨日" in e for e in errors), f"当日 span 应拦'次日', got {errors}"


def test_first_scene_next_day_allowed():
    """CC round-31 修正：章以'次日清晨'开头（首场景即跨日）放行——span 起点模糊。"""
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    director._bible_cache = {}
    card = {
        "core_goal": "g", "conflicts": [], "emotion_curve": [], "chapter_hook": "h",
        "timeline_anchor": {"max_time_progression": "从清晨到傍晚，一日之内"},
        "scene_blueprints": [
            {"scene_num": 1, "narrative_time": "次日清晨", "location": "a", "characters": ["x"],
             "goal": "g1", "conflict": "c1", "emotion": "e1"},
            {"scene_num": 2, "narrative_time": "上午", "location": "b", "characters": ["x"],
             "goal": "g2", "conflict": "c2", "emotion": "e2"},
            {"scene_num": 3, "narrative_time": "午后", "location": "c", "characters": ["x"],
             "goal": "g3", "conflict": "c3", "emotion": "e3"},
            {"scene_num": 4, "narrative_time": "黄昏", "location": "d", "characters": ["x"],
             "goal": "g4", "conflict": "c4", "emotion": "e4"},
        ],
    }
    errors = director.validate_task_card(card, 151)
    assert not any("跨日" in e for e in errors), f"首场景跨日应放行, got {errors}"


def test_deep_night_then_next_day_allowed():
    """深夜 → 次日清晨：隐含跨日推进，跨日检查放行。"""
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    director._bible_cache = {}
    card = {
        "core_goal": "g", "conflicts": [], "emotion_curve": [], "chapter_hook": "h",
        "timeline_anchor": {"max_time_progression": "深夜至次日黄昏"},
        "scene_blueprints": [
            {"scene_num": 1, "narrative_time": "深夜", "location": "a", "characters": ["x"],
             "goal": "g1", "conflict": "c1", "emotion": "e1"},
            {"scene_num": 2, "narrative_time": "次日清晨", "location": "b", "characters": ["x"],
             "goal": "g2", "conflict": "c2", "emotion": "e2"},
        ],
    }
    errors = director.validate_task_card(card, 151)
    assert not any("跨日" in e for e in errors), f"深夜后跨日应放行, got {errors}"
