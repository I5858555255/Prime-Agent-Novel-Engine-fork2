# -*- coding: utf-8 -*-
"""CC30 SceneFailoverController 桥接调度单测（预算+退化窗口组合行为）。"""
from novel_engine.quality.scene_model_router import ProSceneBudget
from novel_engine.quality.degrade_window import DegradeWindow
from novel_engine.quality.scene_failover_controller import (
    SceneFailoverController, classify_scene_attempt)


def test_classifier_signals():
    long_cn = "陆" * 1200
    ok = classify_scene_attempt(scene_text=long_cn, beats_covered_count=3, short_floor=1000)
    assert ok["valid"] and not ok["short"] and not ok["reasoning_only"]
    sh = classify_scene_attempt(scene_text="陆" * 900, beats_covered_count=2, short_floor=1000)
    assert sh["short"] and not sh["valid"]
    ro = classify_scene_attempt(scene_text="", beats_covered_count=0, finish_reason="stop")
    assert ro["reasoning_only"] and ro["beats_zero"]
    # length 截断的近空不算 REASONING_ONLY
    tr = classify_scene_attempt(scene_text="", beats_covered_count=0, finish_reason="length")
    assert not tr["reasoning_only"]
    lat = classify_scene_attempt(scene_text="fusion food omakase " * 10,
                                 beats_covered_count=0, short_floor=1000, latin_wordstream=True)
    assert lat["latin"] and not lat["valid"] and not lat["short"]


def test_client_reasoning_events_trigger_global_cooldown():
    import time
    from novel_engine.agents.writer_agent import WriterAgent
    w = WriterAgent(llm_client=object())  # 普通对象：观察者挂载遍历安全跳过
    w.set_pro_failover(True, client=object())
    scenes = [{"scene_num": i} for i in range(1, 5)]
    w.begin_chapter_failover({"chapter_num": 1}, scenes)
    for _ in range(8):
        w._on_client_degenerate("REASONING_ONLY")
    assert w._fb_pause_until > 0
    assert w._fb_pause_until - time.monotonic() > 150   # P0 冷却 180s
    assert w._fb.concurrency(4) == 1                   # P0 并发->1


def test_writer_routes_degraded_scene_to_pro():
    from novel_engine.agents.writer_agent import WriterAgent
    from novel_engine.agents.scene_schema import SceneOutput
    flash, pro = object(), object()
    w = WriterAgent(llm_client=flash)
    w.set_pro_failover(True, client=pro)
    card = {"chapter_num": 1}
    scenes = [{"scene_num": 1}, {"scene_num": 2}, {"scene_num": 3}, {"scene_num": 4}]
    w.begin_chapter_failover(card, scenes)

    def out(text, beats=2, finish="stop"):
        return SceneOutput(0, text, "", ["b"] * beats, beats_covered=["c"] * beats)

    # 初始未退化：全部 flash
    assert w._fb_pick_client(1) is flash
    # 同场连续 2 次短稿 -> 该场切 pro
    w._fb_report(1, out("陆" * 900), {"word_count_target": 2000})
    assert w._fb_pick_client(1) is flash  # 第1次短稿仍 flash
    w._fb_report(1, out("陆" * 900), {"word_count_target": 2000})
    assert w._fb_pick_client(1) is pro    # 第2次短稿后切 pro
    # 其它场不受影响
    assert w._fb_pick_client(2) is flash
    # latin 词流一次即切 pro
    w._fb_report(3, out("fusion food omakase sushi ramen " * 8, beats=0),
                 {"word_count_target": 2000})
    assert w._fb_pick_client(3) is pro


def test_writer_failover_disabled_always_flash():
    from novel_engine.agents.writer_agent import WriterAgent
    from novel_engine.agents.scene_schema import SceneOutput
    flash, pro = object(), object()
    w = WriterAgent(llm_client=flash)
    w.set_pro_failover(False, client=pro)
    w.begin_chapter_failover({"chapter_num": 1}, [{"scene_num": 1}])
    for _ in range(3):
        w._fb_report(1, SceneOutput(0, "短" * 5, "", [], beats_covered=[]),
                     {"word_count_target": 2000})
    assert w._fb_pick_client(1) is flash


def test_disabled_controller_never_pro():
    c = SceneFailoverController(1, ProSceneBudget(), DegradeWindow(), n_scenes=4, enabled=False)
    c.mark_scene_signal(0, latin_stream_hit=True)
    assert c.use_pro_for_scene(0) is False
    assert c.concurrency(4) == 4


def test_force_whole_all_scenes_pro():
    c = SceneFailoverController(1, ProSceneBudget(), n_scenes=4, force_whole_chapter=True)
    assert all(c.use_pro_for_scene(i) for i in range(4))
    assert c.plan["whole_chapter_pro"] is True and c.plan["pro_scene_count"] == 4


def test_degraded_scenes_capped_per_chapter():
    c = SceneFailoverController(1, ProSceneBudget(), n_scenes=4)
    for i in range(4):
        c.mark_scene_signal(i, short_retry_count=2)  # 4 场全触发
    # 退化场>=3 应整章 pro（计4），不是按场2
    assert c.plan["whole_chapter_pro"] is True
    # 非整章情形：2 场触发只 pro 2 场
    c2 = SceneFailoverController(2, ProSceneBudget(), n_scenes=4)
    c2.mark_scene_signal(0, short_retry_count=2)
    c2.mark_scene_signal(2, reasoning_only_count=2)
    assert c2.use_pro_for_scene(0) and c2.use_pro_for_scene(2)
    assert not c2.use_pro_for_scene(1) and not c2.use_pro_for_scene(3)


def test_chapter_reasoning_three_forces_unfinished():
    c = SceneFailoverController(1, ProSceneBudget(), n_scenes=4)
    c.mark_scene_signal(0, completed=True)
    c.note_chapter_reasoning(3)
    assert c.use_pro_for_scene(0) is False  # 已完成不重出
    assert c.use_pro_for_scene(1) is True   # 未完成场 pro


def test_p1_reduces_concurrency():
    w = DegradeWindow()
    c = SceneFailoverController(1, ProSceneBudget(), degrade=w, n_scenes=4)
    for _ in range(6):
        c.feed_result(short=True)
    assert c.concurrency(4) == 2


def test_p0_concurrency_one_and_queues_cooldown():
    w = DegradeWindow()
    c = SceneFailoverController(1, ProSceneBudget(), degrade=w, n_scenes=4)
    fired = None
    for _ in range(12):
        a = c.feed_result(waste=True)
        if a and a["level"] == "P0":
            fired = a
    assert fired and c.concurrency(4) == 1 and c.pending_cooldown
    assert c.take_cooldown() == 180


def test_p3_all_scenes_pro():
    w = DegradeWindow()
    c = SceneFailoverController(1, ProSceneBudget(), degrade=w, n_scenes=4)
    # 推到 P0（level2），再喂 10 次高信号 -> P3
    for _ in range(12):
        c.feed_result(waste=True)
    w._elevated_obs = 9
    a = c.feed_result(waste=True)
    assert a and a["level"] == "P3"
    assert all(c.use_pro_for_scene(i) for i in range(4))
