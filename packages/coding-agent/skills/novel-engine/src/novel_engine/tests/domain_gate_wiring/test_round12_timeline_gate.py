# -*- coding: utf-8 -*-
"""CC round-12 R1：时间线门——回溯指称不得当硬伤，除非真在倒叙重演。

覆盖需求：
- 无引号旁白字面提及"昨夜"/"昨晚"等回溯词（无重演cue）→ None
- 电影化重演 cue（画面回到/镜头回到/闪回…）+ 回溯词 → 命中硬 issue
- 顺向跳跃（成年/三年后/长大）→ 仍命中
- 对白提及与心理/想起提及 → None
- 现有 round7/round11 用例保持通过
"""
from novel_engine.quality.timeline_gate import detect_timeline_jump


def _anchor():
    return {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}


# ---------- 无引号旁白回溯词（无重演cue）→ 不命中 ----------

def test_narrative_yesterday_no_reposition_not_flagged():
    """无引号旁白字面提及"昨夜"——无重演cue，不判硬伤。"""
    anchor = _anchor()
    assert detect_timeline_jump("经过昨夜，他仍心有余悸。", anchor) is None
    assert detect_timeline_jump("昨夜的绿光像根刺扎在心里，他推开门。", anchor) is None
    assert detect_timeline_jump("那段昨日经历已随风而去。", anchor) is None


def test_narrative_last_night_phrase_not_flagged():
    """旁白里包含"昨晚"名词性指称——无重演cue，放行。"""
    anchor = _anchor()
    assert detect_timeline_jump("回想起昨晚的对话，她笑了笑。", anchor) is None
    assert detect_timeline_jump("昨晚的风很大，吹散了雾气。", anchor) is None


# ---------- 电影化重演 cue + 回溯词 → 命中 ----------

def test_cinematic_reposition_with_yesterday_flagged():
    """画面回到/镜头回到/闪回等重演cue + 回溯词 → 硬 issue。"""
    anchor = _anchor()
    for text in [
        "画面回到昨夜，绿光再次笼罩村庄。",
        "镜头回到昨夜，火光照亮了整个村落。",
        "视线回到昨夜，村民还在熟睡。",
        "一切回到昨夜，婴儿安静地躺在焦土上。",
        "时间倒回昨夜，异象再次出现。",
        "时光倒流回到了昨夜那场大火之前。",
        "闪回昨夜，绿光闪过之时陆烬在襁褓中发出了微光。",
        "记忆闪回至昨夜，草木枯死的圆圈中心。",
    ]:
        result = detect_timeline_jump(text, anchor)
        assert result is not None and "昨夜" in result, f"Expected hit for: {text}"


# ---------- 顺向跳跃（成年/三年后）→ 仍命中 ----------

def test_forward_growth_jump_still_flagged():
    """顺向成长跳跃不受回溯词豁免影响。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": []}
    assert detect_timeline_jump("成年之后，他离开了村子。", anchor) is not None
    assert detect_timeline_jump("三年后，他终于练成那门功法。", anchor) is not None
    assert detect_timeline_jump("多年以后，村子早已变了模样。", anchor) is not None
    assert detect_timeline_jump("他长大后成了守雾人。", anchor) is not None


# ---------- 对白与心理/想起提及 → 不命中 ----------

def test_dialogue_mention_not_flagged():
    """引号对白中提及"昨夜"——对白先被 mask，不应命中。"""
    anchor = _anchor()
    assert detect_timeline_jump('陈老根问："昨夜村里有什么动静吗？"村民摇头。', anchor) is None
    assert detect_timeline_jump('"昨晚那场雨下得真大。"他说。', anchor) is None


def test_thought_mention_not_flagged():
    """心理/想起整句被屏蔽——时间仍在当下，不命中。"""
    anchor = _anchor()
    assert detect_timeline_jump("他想起昨夜在禁区边缘看到的景象。", anchor) is None
    assert detect_timeline_jump("记起昨夜发生在井台边的对话，村民神色紧张。", anchor) is None
    assert detect_timeline_jump("回忆起昨夜迷雾中的脚步声。", anchor) is None
    assert detect_timeline_jump("原来昨夜绿光闪过之时陆烬在襁褓中发出了微光。", anchor) is None
    assert detect_timeline_jump("心里想着昨夜那诡异的绿光，脚步加快了。", anchor) is None


# ── CC round-26e：寿命/命运表述（活到成年/活不到成年）非时间跳跃 ──
def test_lifespan_expression_exempt():
    """担心婴儿夭折的寿命表述（活不到成年）不判时间越界。"""
    from novel_engine.quality.timeline_gate import detect_timeline_jump
    anchor = {"max_time_progression": "从深夜到次日傍晚", "forbidden_markers": ["成年", "五岁那年"]}
    assert detect_timeline_jump("难道就眼睁睁看着这孩子在浊气侵蚀下，病痛缠身，或许活不到成年。", anchor, 6) is None
    assert detect_timeline_jump("把这孩子养大，让他像个普通村童一样活到成年，已是难事。", anchor, 6) is None
    assert detect_timeline_jump("活到成年，已经是莫大的奢望。", anchor, 6) is None


def test_adult_jump_still_hard():
    """真实顺向成长跳跃（成年之后）仍硬拦。"""
    from novel_engine.quality.timeline_gate import detect_timeline_jump
    anchor = {"max_time_progression": "从深夜到次日傍晚", "forbidden_markers": ["成年", "五岁那年"]}
    assert detect_timeline_jump("成年之后，他离开了村子。", anchor, 6) is not None
    assert detect_timeline_jump("五岁那年，他拜入宗门。", anchor, 6) is not None
