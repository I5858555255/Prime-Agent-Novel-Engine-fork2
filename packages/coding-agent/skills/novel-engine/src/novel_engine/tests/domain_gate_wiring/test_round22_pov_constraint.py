# -*- coding: utf-8 -*-
"""CC round-22 P0-1/P0-2：婴儿POV独白门 + 任务卡硬约束履约门（纯规则，零 LLM）。"""
from novel_engine.quality import pov_interiority_gate as pig
from novel_engine.quality import constraint_compliance_gate as ccg
from novel_engine.quality import density_gate
from novel_engine.agents.scene_schema import build_scene_prompt


# ---------------- Q1 POV interiority ----------------

def test_non_restricted_agency_skipped():
    text = "他心想" + "光" * 200
    r = pig.check_pov_interiority_health(text, "normal")
    assert r["clean"] is True and r["violations"] == []
    nt, terms, changed = pig.plan_and_excise(text, "normal")
    assert nt == text and terms == [] and changed is False
    # 未知/空 agency 同样跳过
    assert pig.check_pov_interiority_health(text, None)["clean"] is True


def test_long_adult_monologue_is_truncated():
    seg = "他心想" + "光" * 60 + "。"
    r = pig.check_pov_interiority_health(seg, "infant_low")
    assert r["clean"] is False
    assert r["violations"][0]["type"] == "length_exceeded"
    nt, terms, changed = pig.plan_and_excise(seg, "infant_low")
    assert changed is True and terms == []
    # 独白片段被截到 <=50
    frag = nt[nt.find("他心想"):]
    assert len(frag) <= 50


def test_short_abstract_terms_force_regen():
    seg = "他心中涌起恩情与誓约。"
    r = pig.check_pov_interiority_health(seg, "infant_low")
    types = {v["type"] for v in r["violations"]}
    assert "abstract_concept" in types
    nt, terms, changed = pig.plan_and_excise(seg, "infant_low")
    assert changed is False  # 短片段不靠截断
    assert set(["恩情", "誓约"]).issubset(set(terms))


def test_abstract_term_after_cutoff_is_removed_by_truncation():
    # 禁词位于 50 字之后：纯截断即可清除，无需重生
    seg = "他心想" + "光" * 48 + "恩情" + "光" * 10 + "。"
    nt, terms, changed = pig.plan_and_excise(seg, "infant_low")
    assert changed is True and terms == []
    assert "恩情" not in nt[nt.find("他心想"): nt.find("他心想") + 50]


def test_abstract_term_within_cutoff_requires_regen():
    seg = "他心想恩情" + "光" * 60 + "。"
    nt, terms, changed = pig.plan_and_excise(seg, "infant_low")
    assert "恩情" in terms


def test_legitimate_fragment_sensory_is_clean():
    # 无内心引导词的碎片感官描写应放行
    assert pig.check_pov_interiority_health("婴儿无词，只觉得暖，便又睡去。", "infant_low")["clean"] is True


def test_truncation_prefers_punctuation():
    # 逗号在 50 字内 -> 在逗号后收束
    seg = "他心想" + "光" * 40 + "，" + "光" * 40 + "。"
    nt, terms, changed = pig.plan_and_excise(seg, "infant_low", max_chars=50)
    assert changed is True
    frag = nt[nt.find("他心想"):]
    assert frag.endswith("，") and len(frag) <= 50


# ---------------- Q2 constraint compliance ----------------

def test_identity_concealment_detects_leaked_terms():
    cons = [{"constraint_id": "c2", "type": "identity_concealment",
             "forbidden_reveal_terms": ["三个前世", "武者", "修为"]}]
    v = ccg.check_constraint_compliance("老者眼中精光一闪，三个前世的身法与修为尽展。", cons)
    assert len(v) == 1 and v[0]["type"] == "identity_concealment"
    assert set(v[0]["leaked_terms"]) == {"三个前世", "修为"}
    assert ccg.check_constraint_compliance("老者只是个普通的山村老人。", cons) == []


def test_action_forbidden_until_logic():
    cons = [{"constraint_id": "c1", "type": "action_forbidden_until",
             "forbidden_action_markers": ["抱走", "带回"],
             "condition_markers": ["留下", "系上标记"]}]
    # 做了禁止动作、未满足条件 -> 违规
    v = ccg.check_constraint_compliance("他俯身把婴儿抱走，快步离去。", cons)
    assert len(v) == 1 and v[0]["type"] == "action_forbidden_until"
    # 同时出现条件标记 -> 视为条件已满足，放行
    assert ccg.check_constraint_compliance("他先在襁褓系上标记，随后才把婴儿抱走。", cons) == []
    # 没有禁止动作 -> 放行
    assert ccg.check_constraint_compliance("他只看了一眼便转身离开。", cons) == []


def test_unsupported_and_malformed_constraints_ignored():
    cons = [
        {"type": "emotional_restraint"},  # 不支持类型
        "not-a-dict",
        {"type": "identity_concealment"},  # 缺 terms
    ]
    assert ccg.check_constraint_compliance("任意正文", cons) == []
    assert ccg.check_constraint_compliance("正文", None) == []


def test_constraints_for_blueprint_and_negative_example():
    bp = {"scene_constraints": [{"type": "identity_concealment",
                                 "forbidden_reveal_terms": ["武者"]}, "x"]}
    cs = ccg.constraints_for_blueprint(bp)
    assert len(cs) == 1 and cs[0]["type"] == "identity_concealment"
    assert ccg.constraints_for_blueprint({}) == []
    neg = ccg.violation_negative_example(
        {"type": "identity_concealment", "constraint_id": "c2", "leaked_terms": ["武者"]})
    assert "武者" in neg


# ---------------- prompt / density 条件化 ----------------

def _minimal_prompt(agency):
    bp = {"scene_num": 1, "beats": ["beat一", "beat二"], "word_count_target": 2000,
          "concrete_events": [{"event": "x", "observable_action": "y"}],
          "named_interactions": [{"characters": ["甲"], "interaction_type": "对话", "brief": "b"}],
          "info_reveal_points": [{"type": "伏笔", "content": "c", "anchor_terms": ["雾"]}]}
    tc = {"chapter_num": 0, "protagonist_agency_level": agency,
          "scene_blueprints": [bp], "chapter_hook": ""}
    return build_scene_prompt(tc, bp, [bp])


def test_writer_prompt_agency_conditional():
    low = _minimal_prompt("infant_low")
    assert "主角内心独白硬限制" in low
    assert "不超过50字" in low and "恩情" in low
    normal = _minimal_prompt("normal")
    assert "主角内心独白硬限制" not in normal


def test_sanitize_scene_constraints_drops_invalid():
    from novel_engine.quality.constraint_compliance_gate import (
        sanitize_scene_constraints, sanitize_task_card_constraints)
    raw = [
        "action_forbidden_until: 陈老根武者身份不得点明（自由文本，必须丢弃）",
        {"type": "emotional_restraint", "forbidden_reveal_terms": ["x"]},  # 不支持类型
        {"type": "identity_concealment", "forbidden_reveal_terms": ["武者", "武者", " ", "修为"]},
        {"constraint_id": "c1", "type": "action_forbidden_until",
         "forbidden_action_markers": ["抱走"], "condition_markers": []},   # 缺条件，丢弃
        {"type": "action_forbidden_until",
         "forbidden_action_markers": ["抱走", "带回"], "condition_markers": ["留下标记"]},
        "not-a-dict",
    ]
    kept = sanitize_scene_constraints(raw)
    types = [c["type"] for c in kept]
    assert types == ["identity_concealment", "action_forbidden_until"]
    assert kept[0]["forbidden_reveal_terms"] == ["武者", "修为"]  # 去空白/去重保序
    assert kept[1]["constraint_id"] and kept[1]["condition_markers"] == ["留下标记"]
    assert sanitize_scene_constraints(None) == []
    # 端到端：任务卡清洗后字符串项不残留
    card = {"scene_blueprints": [{"scene_num": 1, "scene_constraints": raw}]}
    sanitize_task_card_constraints(card)
    assert all(isinstance(c, dict) for c in card["scene_blueprints"][0]["scene_constraints"])
    assert len(card["scene_blueprints"][0]["scene_constraints"]) == 2


def test_density_directive_agency_conditional():
    res = {"missing": ["x"], "interiority": False}
    low = density_gate.density_directive(res, protagonist_interiority=1, agency_level="infant_low")
    assert "碎片内心印象" in low and "评判性内在观察" not in low
    norm = density_gate.density_directive(res, protagonist_interiority=1, agency_level="normal")
    assert "评判性内在观察" in norm
