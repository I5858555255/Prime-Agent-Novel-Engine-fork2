# -*- coding: utf-8 -*-
"""CC round-15 scene 韧性：ID泄漏净化 + 初稿分级/组合放行（零LLM）。"""
from novel_engine.quality import scene_resilience as sr


def test_strip_real_c009_leak():
    # 真机案例：句末换行后孤立泄漏字段 "C009"
    txt = "山道上响起三串脚步声，踩在湿泥里啪嗒作响。\n\n\"C009\""
    cleaned, leak = sr.strip_leaked_tokens(txt)
    assert leak is True
    assert "C009" not in cleaned
    assert cleaned.endswith("。")
    assert "脚步声" in cleaned


def test_keep_id_inside_dialogue():
    txt = '他低声说:你的编号是C009，记住了。'
    cleaned, leak = sr.strip_leaked_tokens(txt)
    assert leak is False
    assert "C009" in cleaned


def test_keep_id_with_legit_context_and_inline():
    assert sr.strip_leaked_tokens("样本C009已被转移。")[1] is False
    assert sr.strip_leaked_tokens("代号C009的实验体睁开了眼。")[1] is False


def test_strip_multiple_isolated_leaks():
    txt = "他转身离去。\n\nC009\n\n门外风声呜咽。"
    cleaned, leak = sr.strip_leaked_tokens(txt)
    assert leak is True
    assert "C009" not in cleaned
    assert "门外风声呜咽" in cleaned


def test_proper_ending_and_sentence_count():
    assert sr.has_proper_ending("他走了。")
    assert not sr.has_proper_ending("他走")
    assert sr.count_effective_sentences("他起身。风吹过。灯灭了！远处传来声响……") == 4


def test_beat_coverage_basic():
    beats = ["陆烬睁开眼睛", "陈老根抱起婴儿", "绿雾灼烧"]
    text = "陆烬缓缓睁开眼睛，陈老根伸手把婴儿抱进怀里。"
    cov = sr.beat_coverage(text, beats)
    assert cov >= 0.6
    assert sr.beat_coverage("无关内容。完全没演到。", beats) < 0.5


def test_classify_hard_degenerate():
    assert sr.classify_scene_output("C009", "stop", 2000, ["beat a"]) == "HARD_DEGENERATE"
    assert sr.classify_scene_output("", "stop", 2000, ["beat a"]) == "HARD_DEGENERATE"


def test_classify_malformed_short_no_ending():
    txt = ("他站在门口听见远处的风声一阵接一阵地刮过荒草掠过土坡吹向村外的"
           "篱笆墙根久久不停")  # 约48字
    txt = txt * 3  # >=80, <300, 无句末
    assert 80 <= len(txt) < 300
    assert sr.classify_scene_output(txt, "stop", 2000, ["他站在门口听风"]) == "MALFORMED_SHORT"


def test_classify_coherent_short_goes_expansion():
    # 真机 scene2≈208字：有句末、演到 beat、虽短但有效 → 非 HARD/MALFORMED（L3 放行进扩写）
    txt = ("他睁开眼睛看见一团淡青色的光在头顶缓缓旋转。陈老根粗糙的手掌把他从襁褓里抱了起来，"
           "动作笨拙却格外用力，生怕摔着这风雪里捡来的婴孩。绿雾顺着皮肤灼烧，像无数根细针密密麻麻扎进毛孔。"
           "院门外几个村民举着火把悄悄围拢过来，压低声音议论这荒年里天降的妖星究竟是吉是凶。"
           "他被夜里寒气一激，扯开嗓子啼哭出声，声音又细又弱。")
    assert len(txt) >= 150
    cls = sr.classify_scene_output(txt, "stop", 2000, ["睁眼", "抱起", "灼烧", "围拢", "啼哭"])
    assert cls not in ("HARD_DEGENERATE", "MALFORMED_SHORT", "TRUNCATED")


def test_classify_truncated_by_finish_reason():
    txt = "一段已经写了足够长但被截断的内容" * 6
    assert sr.classify_scene_output(txt, "length", 2000, ["b"]) == "TRUNCATED"


def test_usable_draft_gate():
    good = "他睁开眼睛看见淡青色的光。陈老根粗糙的手把他抱起。绿雾顺着毛孔灼烧。村民举着火把靠近。他啼哭出声。"
    assert sr.usable_draft(good, ["睁眼", "抱起", "灼烧", "靠近"]) is True
    assert sr.usable_draft("太短。", ["睁眼"]) is False  # 句数不足
    assert sr.usable_draft(good, ["完全无关的beat标签xyz"], ) is False  # beat 不覆盖
