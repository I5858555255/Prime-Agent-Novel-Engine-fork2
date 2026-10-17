# -*- coding: utf-8 -*-
"""CC28 批B 3b/3c：相邻场近重复重演门 + 同章别名裸奇门（零 LLM）单测。"""
from novel_engine.quality.cross_scene_reprise import (
    score_pair, detect_adjacent_reprise, REPRISE_JACCARD)
from novel_engine.quality.alias_consistency_gate import (
    detect_bare_alias_switch, has_identity_anchor, strip_dialogue,
    load_alias_groups)

# ───────────────────────── 3b 相邻场重演 ─────────────────────────

_A = (
    "老松树的根须在浓雾里若隐若现，襁褓搁在隆起的树根上，婴孩的啼哭一声紧似一声。"
    "陈老根拨开荆棘走近，粗糙的大手把那襁褓抱起，贴着胸口，铜锣声从村子方向隐隐传来，"
    "子时的山风掠过林梢，夜鸟扑棱棱惊起，他低头看着婴孩皱巴巴的脸，长长叹了一口气。"
    "襁褓里塞着半块刻了纹路的青铜长命锁，雾水打湿了婴孩稀疏的胎发，哭声在空山里久久回荡。")


def test_near_verbatim_same_scene_flagged():
    # 后场把同一片现场几乎原样再演（仅少量措辞改动）
    b = _A.replace("若隐若现", "依稀可见").replace("隐隐传来", "断断续续传来")
    b = b.replace("长长叹了一口气", "沉默着没有说话")
    r = score_pair(_A, b)
    assert r["similarity"] >= REPRISE_JACCARD
    assert r["is_reprise"] is True
    assert detect_adjacent_reprise([_A, b])


def test_same_place_but_time_advances_with_new_event_not_flagged():
    # 同地点，但子时→卯时、新增追兵与奔逃的足量新内容
    b = (
        "卯时天色微亮，雾散了大半，老松树根边只剩一只绣鞋。远处忽然传来追兵的犬吠与人喊，"
        "陈老根把襁褓往怀里一紧，转身抄小道往山脊奔去，脚下枯枝被踩得噼啪作响，"
        "三支冷箭擦着耳边钉进树干，他不敢回头，沿着溪涧连翻过两道山梁才甩掉火把的光。")
    r = score_pair(_A, b)
    assert r["time_advance"] is True
    assert r["is_reprise"] is False


def test_explicit_flashback_not_flagged():
    # 后场是人物显式回忆前事（合法闪回），即便措辞相近也不报
    b = "他回忆起那夜老松树根的情形，" + _A
    r = score_pair(_A, b)
    assert r["flashback"] is True
    assert r["is_reprise"] is False


def test_normal_adjacent_progression_not_flagged():
    b = (
        "祠堂里烛火通明，族老们分两列站定，陈老根抱着婴孩跨进门槛，众人的目光齐刷刷投来。"
        "沈知微立在廊柱阴影里，指尖捏着一枚传讯玉简，不动声色地看着这一幕，等着看养父如何开口。")
    r = score_pair(_A, b)
    assert r["is_reprise"] is False


def test_short_transition_scene_not_flagged():
    assert score_pair(_A, "山路蜿蜒，他一路下山。")["is_reprise"] is False


# ───────────────────────── 3c 别名裸切 ─────────────────────────

def test_bare_switch_li_chun_to_lu_jin_flagged():
    # 前几场叙述用“李淳”，后面无锚点直接用“陆烬”
    text = (
        "李淳睁开眼，只觉得浑身发冷，他不明白自己为何会躺在荒野里。李淳撑起身子，"
        "摸了摸这具陌生的身体，心中警铃大作。"
        "三日后，陆烬跟着养父走进村子，陆烬看着低矮的茅屋，神色平静。")
    out = detect_bare_alias_switch(text)
    assert out["has_issue"] is True
    iss = out["issues"][0]
    assert set(iss["used"]) == {"李淳", "陆烬"}
    assert iss["dominant"] in {"李淳", "陆烬"}


def test_identity_anchor_sentence_exempts():
    text = (
        "他本名李淳，神魂转生到大乾之后名陆烬。李淳睁开眼时，陆烬这个名字尚未被人叫起，"
        "后来陈老根便唤他陆烬。陆烬渐渐习惯了这个新名字。")
    assert has_identity_anchor(text, ["陆烬", "李淳"]) is True
    assert detect_bare_alias_switch(text)["has_issue"] is False


def test_dialogue_address_exempt_when_narration_single_name():
    # 叙述只用“李淳”，“陆烬”仅出现在他人对话称呼中 → 不报
    text = (
        "李淳低头喝粥，不理会旁人议论。那汉子指着他嚷：“陆烬！你给我站住！”"
        "李淳依旧不抬头，把碗里的粥一口口喝完，转身出门。")
    assert "陆烬" in strip_dialogue(text) or True  # strip 后叙述无“陆烬”
    assert "陆烬" not in strip_dialogue(text)
    assert detect_bare_alias_switch(text)["has_issue"] is False


def test_single_name_no_issue():
    text = "陆烬走进密林，陆烬屏住呼吸，陆烬听见前方有兵刃交击之声。"
    assert detect_bare_alias_switch(text)["has_issue"] is False


def test_groups_include_builtin_rebirth_alias():
    groups = load_alias_groups(None)
    assert any(set(["陆烬", "李淳"]).issubset(set(g)) for g in groups)
