# -*- coding: utf-8 -*-
"""CC round-23 P0-1/P0-2 离线测试：场景差异化推进 + 氛围占比确定性门 + 任务卡清洗。"""
from novel_engine.quality import scene_progression_gate as spg

CFG = spg.load_progression_config(None)
TH_SCENE = float(CFG["scene_atmosphere_ratio"])
TH_ADJ = float(CFG["adjacent_atmosphere_ratio"])


def _scenes(*pairs):
    return [{"scene_id": sid, "scene_text": txt} for sid, txt in pairs]


def test_pure_atmosphere_sentence_classification():
    assert spg.is_pure_atmosphere_sentence("雾气在死寂里缓缓翻涌，寒意刺骨。", CFG)
    assert spg.is_pure_atmosphere_sentence("浓重的夜色压下来，磷火幽幽亮着。", CFG)
    # 对话句 / 含具体动作动词的句子不算纯氛围
    assert not spg.is_pure_atmosphere_sentence('他开口说：“先别过去。”', CFG)
    assert not spg.is_pure_atmosphere_sentence("陈老根抱起婴儿，转身往村里走。", CFG)
    assert not spg.is_pure_atmosphere_sentence("普通陈述句没有任何氛围词。", CFG)


def test_scene_atmosphere_ratio_saturated():
    atmos = "雾气在死寂里翻涌。寒意贴着脊背游走。磷火在浓雾深处明灭。夜色压得人喘不过气。幽暗的目光悬在头顶。"
    action = '老赵攥住阿福的胳膊。他低声说：“别去。”'
    r = spg.scene_atmosphere_ratio(atmos + action, CFG)
    assert r["total_sentences"] >= 6
    assert r["saturated"] is True
    assert r["ratio"] > TH_SCENE


def test_scene_atmosphere_ratio_healthy_not_saturated():
    txt = (
        "陈老根踢开柴门，大步走到床前。他伸出粗糙的手，把襁褓抱了起来。"
        '村民围在门口，有人低声问：“这孩子还活着吗？”老人点头：“在哭，命硬。”'
        "他转身对众人说，今夜谁也不许把这事说出去。"
    )
    r = spg.scene_atmosphere_ratio(txt, CFG)
    assert r["saturated"] is False


def test_adjacent_atmosphere_overlap_flags_homogeneity():
    a = "雾气翻涌，寒意刺骨，磷火在暗夜里明灭，冷光幽幽。"
    b = "浓雾压着村子，寒气逼人，那点幽光在夜色里忽明忽暗。"
    ov = spg.adjacent_atmosphere_overlap(a, b, CFG)
    assert ov["saturated"] is True
    assert ov["ratio"] > TH_ADJ
    assert "雾" in ov["shared"]


def test_adjacent_distinct_scenes_not_flagged():
    a = "陈老根抱起婴儿，推门进了屋，把孩子放在炕上。"
    b = "第二天清晨，沈知微在县衙翻开一卷文书，提笔写下名字。"
    ov = spg.adjacent_atmosphere_overlap(a, b, CFG)
    assert ov["saturated"] is False


def test_progression_contract_parse():
    bp = {"scene_progression_contract": {
        "new_state_or_entity": ["村口第一次亮起火把", "陈老根做出留下婴儿的决定"],
        "irreversible_change": "陈老根正式抱起婴儿离开原地"}}
    c = spg.progression_contract(bp)
    assert len(c["new_state_or_entity"]) == 2 and c["irreversible_change"]
    assert spg.blueprint_has_progression_contract(bp)
    assert not spg.blueprint_has_progression_contract({})


def test_new_state_covered_hit_and_miss():
    bp_hit = {"scene_num": 1, "scene_progression_contract": {
        "new_state_or_entity": ["村口第一次亮起成排火把"]}}
    text_hit = "村道尽头，一长串火把次第点亮，陈老根领着众人朝雾边走。"
    ev_hit = spg.evaluate_scene_progression(bp_hit, text_hit, CFG)
    # “火把”是该状态落地时必然出现的具体意象
    assert ev_hit["no_new_state"] is False

    bp_miss = {"scene_num": 1, "scene_progression_contract": {
        "new_state_or_entity": ["山庙里铜钟第一次被敲响"]}}
    ev_miss = spg.evaluate_scene_progression(bp_miss, "四周只有雾气和寒意，什么也没有发生。", CFG)
    assert ev_miss["no_new_state"] is True
    assert ev_miss["missing_new_state"]


def test_old_card_without_contract_never_hard_flags_no_new_state():
    # 老卡/模板卡无 scene_progression_contract：即使正文为空也不产生 no_new_state 硬违规
    card = {"scene_blueprints": [{"scene_num": 1, "beats": ["x"]}]}
    out = spg.check_chapter_progression(card, _scenes((1, "雾气寒意夜色")), CFG)
    assert all("no_new_state" not in v["types"] for v in out["scene_violations"])


def test_check_chapter_flags_missing_state_and_adjacent():
    card = {"scene_blueprints": [
        {"scene_num": 1, "scene_progression_contract": {
            "new_state_or_entity": ["山庙里铜钟第一次被敲响"], "irreversible_change": "钟响"}},
        {"scene_num": 2, "scene_progression_contract": {
            "new_state_or_entity": ["铜钟被敲后祠堂门打开"], "irreversible_change": "门开"}},
    ]}
    t1 = "雾气翻涌，寒意刺骨，磷火在暗夜里明灭，冷光幽幽，长夜死寂。"
    t2 = "浓雾压着，寒气逼人，那幽光在夜色里忽明忽暗，死寂无人。山门外没有任何动静。"
    out = spg.check_chapter_progression(card, _scenes((1, t1), (2, t2)), CFG)
    # 两场都没演契约里的新状态
    sids_state = {v["scene_id"] for v in out["scene_violations"] if "no_new_state" in v["types"]}
    assert 1 in sids_state and 2 in sids_state
    # 相邻氛围同质，后现场=2
    assert any(a["later_scene"] == 2 for a in out["adjacent"])


def test_sanitize_coerces_dirty_contract():
    bp = {"scene_progression_contract": "随便写的自由文本：本场要冷要有雾"}
    spg.sanitize_scene_progression(bp)
    assert "scene_progression_contract" not in bp  # 非 dict 直接删

    bp2 = {"scene_progression_contract": {
        "new_state_or_entity": "村口第一次亮起火把",  # 误写成单字符串
        "irreversible_change": 123}}  # 非字符串
    spg.sanitize_scene_progression(bp2)
    c = bp2["scene_progression_contract"]
    assert c["new_state_or_entity"] == ["村口第一次亮起火把"]
    assert c["irreversible_change"] == ""

    bp3 = {"scene_progression_contract": {"new_state_or_entity": ["a", "a", "", "b"]}}
    spg.sanitize_scene_progression(bp3)
    assert bp3["scene_progression_contract"]["new_state_or_entity"] == ["a", "b"]


def test_sanitize_task_card_progression_all_blueprints():
    card = {"scene_blueprints": [
        {"scene_num": 1, "scene_progression_contract": "脏字符串"},
        {"scene_num": 2, "scene_progression_contract": {
            "new_state_or_entity": ["新实体X"], "irreversible_change": "决定Y"}}]}
    spg.sanitize_task_card_progression(card)
    assert "scene_progression_contract" not in card["scene_blueprints"][0]
    assert card["scene_blueprints"][1]["scene_progression_contract"]["new_state_or_entity"] == ["新实体X"]


def test_new_state_synonym_and_segmentation_coverage_fix():
    # CC23 Fix A：实义锚点“发光/坠落”即使被正文换写成“光点/沉落”也算落地，避免误判 no_new_state
    bp = {"scene_num": 1, "scene_progression_contract": {
        "new_state_or_entity": ["意识体压缩后呈发光点状态坠落"]}}
    text = "那点幽光骤然亮起，光点拖着尾迹朝无尽黑暗沉落，四野只剩风声。"
    ev = spg.evaluate_scene_progression(bp, text, CFG)
    assert ev["no_new_state"] is False
    assert ev["missing_new_state"] == []


def test_progression_terms_drop_meta_and_keep_salient():
    terms = spg.extract_progression_terms("意识体完成压缩，呈发光点状态坠落", CFG)
    assert "发光" in terms and "坠落" in terms
    for meta in ("状态", "首次", "呈现", "出现", "完成"):
        assert meta not in terms


def test_directives_are_event_focused_not_polish():
    d1 = spg.new_state_directive({"detail": {"missing_new_state": [{"desc": "铜钟第一次敲响"}]}})
    assert "新增" in d1 and "铜钟第一次敲响" in d1 and "严禁只改写措辞" in d1
    d2 = spg.atmosphere_ratio_directive(0.72, 0.55)
    assert "72%" in d2 and "具体动作" in d2
    d3 = spg.adjacent_motif_directive({"shared": ["雾", "寒", "磷光"]})
    assert "雾" in d3 and "新事件" in d3
