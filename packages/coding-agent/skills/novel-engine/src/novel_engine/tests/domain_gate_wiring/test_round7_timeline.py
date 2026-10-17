# -*- coding: utf-8 -*-
"""CC round-7 Q5 deterministic timeline/age jump gate."""
from novel_engine.quality.timeline_gate import detect_timeline_jump, anchor_fix_directive


def test_explicit_forbidden_marker_always_enforced():
    anchor = {"max_time_progression": "数年内",
              "forbidden_markers": ["五岁那年", "成年"]}
    # even with a multi-year allowed span, explicit markers still hard-block
    issue = detect_timeline_jump("日子流转，陆烬五岁那年已经能识字。", anchor)
    assert issue and "五岁那年" in issue


def test_protagonist_age_jump_blocked_in_short_span():
    anchor = {"max_time_progression": "当日"}
    issue = detect_timeline_jump("陆烬五岁那年第一次独自走进祠堂。", anchor, chapter_num=2)
    assert issue and "时间线越界" in issue


def test_generic_growth_phrases_blocked():
    anchor = {"max_time_progression": "数日内"}
    for bad in ["多年以后，村子早已变了模样。", "他长大后成了守雾人。", "三年后再回此地。"]:
        assert detect_timeline_jump(bad, anchor) is not None, bad


def test_backward_reference_and_days_allowed():
    anchor = {"max_time_progression": "数日内"}
    # 三十年前是回溯、三日后是短时段，均不应误杀
    assert detect_timeline_jump("陈老根想起三十年前封雾的往事。", anchor) is None
    assert detect_timeline_jump("三日后，村口的告示贴了出来。", anchor) is None


def test_adult_age_mention_not_flagged():
    # 陈老根七十岁是当下人物描写，不是主角成长跳跃
    anchor = {"max_time_progression": "当日"}
    assert detect_timeline_jump("陈老根已年近七十，背脊却依旧挺直。", anchor) is None


def test_year_span_permits_forward_years():
    anchor = {"max_time_progression": "数年内"}
    # generic growth phrases suppressed when the legal span itself is years
    assert detect_timeline_jump("三年后，他终于练成那门功法。", anchor) is None


def test_fix_directive_mentions_constraint():
    d = anchor_fix_directive("[时间线越界] xxx", {"max_time_progression": "当日"})
    assert "当日" in d and "严禁" in d


# ---------- CC round-11 R3：对白/心理引用 vs 旁白重演 ----------

def test_dialogue_mention_yesterday_not_flagged():
    """引号对白中提及"昨夜"——对白先被 mask，不应命中。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}
    text = '陈老根问："昨夜村里有什么动静吗？"村民摇头说没有。'
    assert detect_timeline_jump(text, anchor) is None


def test_cinematic_reposition_flagged():
    """电影化场景重定位（画面回到/镜头回到/时间倒回等）+ 禁写标记 → 应命中。
    此类旁白把叙事真正拉回过去重演，不同于心理回忆或一句揭示，必须拦截。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}
    for text in [
        "画面回到昨夜，绿光再次笼罩村庄，陆烬在襁褓中啼哭。",
        "镜头回到昨夜，火光照亮了整个村落。",
        "时间倒回昨夜，村民还在熟睡。",
        "时光倒流，回到了昨夜那场大火之前。",
        "一切回到昨夜，婴儿安静地躺在焦土上。",
        "闪回至昨夜，异象再次出现。",
    ]:
        result = detect_timeline_jump(text, anchor)
        assert result is not None and "昨夜" in result, f"Expected hit for: {text}"


def test_character_mention_yesterday_still_ok():
    """角色心理活动提及昨夜——整句被 thought mask，时间仍在当下，不应命中。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}
    text = "陈老根心里想着昨夜那诡异的绿光，脚步加快了。"
    assert detect_timeline_jump(text, anchor) is None


def test_real_ch3_thought_sentence_not_flagged():
    """真机 ch3 原句——角色当下回忆昨夜，整句屏蔽后不应命中。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}
    text = "他想起昨夜在禁区边缘看到的景象：草木枯死的圆圈中心，这个婴儿安静地躺着……那画面此刻在他脑海里反复闪现"
    assert detect_timeline_jump(text, anchor) is None


def test_flashback_marker_returning_to_past_allowed():
    """一句揭示性回述"原来昨夜"——属心理/认知范畴，应放行。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}
    text = "原来昨夜绿光闪过之时，陆烬在襁褓中发出了微光。"
    assert detect_timeline_jump(text, anchor) is None


def test_generic_growth_still_flagged_even_with_reposition():
    """generic growth jump 不受 forbidden_markers 豁免影响。"""
    anchor = {"max_time_progression": "当日"}
    text = "画面回到昨夜，转眼已是三年后，陆烬长大成人。"
    result = detect_timeline_jump(text, anchor)
    assert result is not None and "越界" in result


def test_think_and_recalled_patterns_masked():
    """补充：想起/记起/回忆/浮想 等整句均应屏蔽，不命中禁写标记。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}
    for text in [
        "他想起昨夜那道绿光，心中一凛。",
        "记起昨夜发生在井台边的对话，村民神色紧张。",
        "回忆涌上，昨夜陈老根把襁褓裹紧的身影清晰地浮现。",
        "浮想联翩，昨夜的一幕幕在脑中回放。",
        "他回忆起昨夜迷雾中的脚步声。",
    ]:
        result = detect_timeline_jump(text, anchor)
        assert result is None, f"Expected no hit for thought-masked text: {text}"
