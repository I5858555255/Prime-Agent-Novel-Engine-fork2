# -*- coding: utf-8 -*-
"""CC round-21：scope_gate 吐纳/调息豁免扩展 + 气感子串 + 反诘降级测试。

覆盖：
- 放行：陈老根深夜/当晚独白吐纳调息（包括"自己"/"他"跨句回溯）
- 降 soft：反问"如何理解气感"、否定"他没有再盘膝调息"
- 不误伤："空气感"不命中
- 仍 hard（红线）：陆烬学练吐纳/调息、真实传授呼吸法给陆烬、
  陆烬肯定气感/内视、非夜场景陈老根吐纳
- 真机回归夹具：ch6 失败草稿 scene4 终版断言 hard 为空（反问句在 soft）
"""
from __future__ import annotations

import json
from pathlib import Path

from novel_engine.quality.scope_gate import (
    detect_scope_violations,
    reset_config_cache,
    _is_air_sensibility_substring,
    _has_teach_violation,
)


# ── 夹具 ───────────────────────────────────────────────────────────────────────

_FIXTURE_ROOT = str(Path(__file__).resolve().parent / "fixtures" / "ch6_scope_tuna")
_SCENE4_PATH = Path(_FIXTURE_ROOT) / "chapter_6_scene4.jsonl"


def _load_scene4() -> str:
    """读取真机 ch6 失败草稿 scene4 终版文本。"""
    with open(_SCENE4_PATH, encoding="utf-8") as f:
        return json.loads(f.readline()).get("scene_text", "")


_NIGHT_ANCHOR = {
    "chapter_start_marker": "当晚",
    "max_time_progression": "数时辰",
}

_ROOT = "novel_engine"  # relative to src/ when running from src dir


# ── 放行用例 ──────────────────────────────────────────────────────────────────


def test_exempt_chen_laogen_night_tuna():
    """陈老根深夜盘膝吐纳 → 豁免（硬门空）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "是夜子时，陈老根独自盘膝坐在炕边吐纳呼吸，气息绵长。陆烬在旁偷看。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" not in [h["term"] for h in r["hard"]]


def test_exempt_chen_laogen_tiaoxi_same_night():
    """陈老根调息（吐纳同义）→ 豁免。"""
    reset_config_cache()
    r = detect_scope_violations(
        "深夜，陈老根在房中调息，气息绵长而规律。陆烬在襁褓里静静看着。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "调息" not in [h["term"] for h in r["hard"]]


def test_exempt_chen_self_reflexive_tuna():
    """陈老根'自己吐纳'（内心独白反身标记）→ 豁免。"""
    reset_config_cache()
    # 使用场景级 night_bounded（通过 timeline_anchor），确保 night_bounded=True
    r = detect_scope_violations(
        "深夜，他独自坐在窗前，想起自己吐纳调息时的情形，心中翻涌。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    # 验证：该句含"深夜"(night_anchor) + "自己"+ 吐纳/调息 → 应豁免
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, f"self-reflexive 应豁免，实际 hard={hard_terms}"
    assert "调息" not in hard_terms


def test_exempt_chen_pronoun_cross_sentence():
    """他（陈老根）回想起昨夜吐纳 → 代词跨句回溯豁免。"""
    reset_config_cache()
    prev = "是夜子时，陈老根独自盘膝坐在炕边。他缓缓吐纳，呼吸绵长。"
    r = detect_scope_violations(prev + "x" * 500, 6, _NIGHT_ANCHOR, _ROOT)
    assert "吐纳" not in [h["term"] for h in r["hard"]]


def test_exempt_observer_lu_jin():
    """陆烬旁观陈老根吐纳（无模仿动词）→ 豁免。"""
    reset_config_cache()
    r = detect_scope_violations(
        "陆烬裹在襁褓中，偷偷看着陈老根盘膝吐纳。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" not in [h["term"] for h in r["hard"]]


def test_exempt_descriptive_reference():
    """吐纳作为描述性名词引用（非施为动作）→ 豁免。"""
    reset_config_cache()
    r = detect_scope_violations(
        "深夜，那呼吸又变回了平常的样子，粗重，带着轻微的鼾意，"
        "和刚才那绵长规律的吐纳判若两人。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, f"descriptive 引用 应豁免，实际 hard={hard_terms}"


# ── 降 soft 用例 ──────────────────────────────────────────────────────────────


def test_rhetorical_qi_gan_soft():
    """反问'如何理解气感' → soft（非 hard）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "话都不会说，路都不会走，如何理解气感，如何引导周天？"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "气感" not in hard_terms
    soft_terms = [(s["kind"], s["term"]) for s in r["soft"]]
    assert ("negated_hard", "气感") in soft_terms


def test_negated_tuna_soft():
    """否定句'他没有再盘膝调息' → soft（非 hard）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "他并没有再盘膝调息，只是普通地坐着。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "调息" not in hard_terms


def test_hesitant_teach_soft():
    """犹豫/权衡'权衡是否传授' → soft（非 hard）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "他权衡是否传授呼吸法，终究没有开口。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms


# ── 不误伤 ────────────────────────────────────────────────────────────────────


def test_air_sensibility_not_hit():
    """'空气感'等普通构词不命中'气感'硬门。"""
    reset_config_cache()
    r = detect_scope_violations(
        "那种粘稠的空气感让他感到不适。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "气感" not in hard_terms


def test_air_sensibility_substring_detection():
    """_is_air_sensibility_substring 直接单元测试。"""
    s = "那种粘稠的空气感"
    idx = s.find("气感")  # idx=6, prev char is '空'
    assert idx == 6
    # "空气感"含"气感" → 前一字为"空" → True（放过）
    assert _is_air_sensibility_substring(idx, s) is True
    # "有气感" → 前字为"有"不在白名单 → False（命中）
    assert _is_air_sensibility_substring(2, "感到体内有气感") is False
    # 句首"气感" → 直接命中
    assert _is_air_sensibility_substring(0, "气感涌动") is False


# ── 红线反例（必须仍 hard）───────────────────────────────────────────────────


def test_red_line_lu_jin_mimics_tuna():
    """陆烬学陈老根自己吐纳 → hard（红线）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "深夜，陆烬学陈老根的样子，自己在床上吐纳。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" in [h["term"] for h in r["hard"]]


def test_red_line_actual_teach_tuna():
    """真实传授（肯定实施）'教陆烬吐纳' → hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "陈老根教陆烬吐纳呼吸法门。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" in [h["term"] for h in r["hard"]]


def test_red_line_lu_jin_positive_qi_gan():
    """陆烬肯定具备气感（非反问语境）→ hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "陆烬感到体内有气感涌动。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "气感" in [h["term"] for h in r["hard"]]


def test_red_line_lu_jin_positive_neishi():
    """陆烬肯定内视 → hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "陆烬以内视看见自己脏腑。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "内视" in [h["term"] for h in r["hard"]]


def test_red_line_daytime_tuna():
    """非夜场景（次日）陈老根吐纳 → 概念解锁后放行（round-27c）。"""
    reset_config_cache()
    day_anchor = {"chapter_start_marker": "次日", "max_time_progression": "白昼"}
    r = detect_scope_violations(
        "白天陈老根在院中吐纳呼吸。"
        + "x" * 500,
        6, day_anchor, _ROOT,
    )
    assert "吐纳" not in [h["term"] for h in r["hard"]]  # cultivation_system ch6 解锁


def test_red_line_ch5_tuna():
    """ch5 吐纳不受 ch6 豁免 → hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "陈老根夜里吐纳呼吸。"
        + "x" * 500,
        5, None, _ROOT,
    )
    assert "吐纳" in [h["term"] for h in r["hard"]]


# ── 真机回归夹具（ch6 失败草稿 scene4）──────────────────────────────────────


def test_real_ch6_scene4_no_hard():
    """真机 ch6 失败草稿 scene4 终版 → hard 为空。

    scene4 内容：陈老根独坐回想昨夜，"自己吐纳调息""每次吐纳/调息"均合法；
    "如何理解气感"为反问应降 soft；绝无 hard 阻断。
    """
    reset_config_cache()
    scene4_text = _load_scene4()
    r = detect_scope_violations(scene4_text, 6, _NIGHT_ANCHOR, _ROOT)
    hard_terms = [h["term"] for h in r["hard"]]
    assert hard_terms == [], f"scene4 hard 应为空，实际硬伤：{hard_terms}"
    # 反问气感应降 soft
    soft_terms = [s["term"] for s in r["soft"]]
    assert "气感" in soft_terms, f"反问气感应降为 soft，实际：{r['soft']}"
    # 吐纳/调息应豁免
    assert "吐纳" not in hard_terms
    assert "调息" not in hard_terms


def test_real_ch6_scene4_tuna_exempt_sentences():
    """scene4 中'自己吐纳调息'等句应标记为 tuna_exempt soft。"""
    reset_config_cache()
    scene4_text = _load_scene4()
    r = detect_scope_violations(scene4_text, 6, _NIGHT_ANCHOR, _ROOT)
    soft_kinds = {s["kind"] for s in r["soft"]}
    assert "tuna_exempt" in soft_kinds, f"应有 tuna_exempt soft，实际：{r['soft']}"


# ── Gap A 补测：婴儿反身漏放修复 ─────────────────────────────────────────────


def test_gap_a_lu_jin_self_tuna_hard():
    """GapA：陆烬自己吐纳调息（无字学）→ hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "陆烬自己吐纳调息。" + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" in [h["term"] for h in r["hard"]]
    assert "调息" in [h["term"] for h in r["hard"]]


def test_gap_a_na_lu_jin_zixing_hard():
    """GapA：那陆烬便自行吐纳 → hard（'那'不豁免婴儿主语）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "那陆烬便自行吐纳。" + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" in [h["term"] for h in r["hard"]]


def test_scene4_legit_still_exempt():
    """真机scene4合法句（无婴儿词，含自己+吐纳调息）→ 仍豁免，防误伤。"""
    reset_config_cache()
    scene4_text = _load_scene4()
    r = detect_scope_violations(scene4_text, 6, _NIGHT_ANCHOR, _ROOT)
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms
    assert "调息" not in hard_terms


# ── Gap B 补测：既成传授被犹豫词放掉修复 ─────────────────────────────────────


def test_gap_b_definite_teach_with_hesitant_hard():
    """GapB：犹豫但仍传给陆烬（既成事实）→ hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "他犹豫再三，还是把呼吸法传给了陆烬。" + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    # CC 2026-10-06：归因显示实际命中词（句含"呼吸法"→"呼吸"），非硬编码"吐纳"
    assert hard_terms, f"既成传授应 hard，实际：{hard_terms}"
    assert "呼吸" in hard_terms, f"归因应显示实际命中词，实际：{hard_terms}"


def test_gap_b_hesitant_no_teach_soft():
    """GapB：权衡是否传授（未实施）→ soft，不回归。"""
    reset_config_cache()
    r = detect_scope_violations(
        "他权衡是否传授呼吸法，终究没有开口。" + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms


# ── Gap C 补测：prev_sents 跨禁词污染修复 ─────────────────────────────────────


def test_gap_c_isolated_single_sentence_exempt():
    """GapC：孤立单句（无前文）含"他+自己+吐纳调息"不应被 prev_sents 污染误判 hard。

    根因：_scan_terms 中 prev_sents 在 for term 循环外初始化，扫到吐纳/调息之前
    的不命中硬词时就把当前句 append 进 prev_sents，导致跨禁词上下文泄漏。
    修法：每个禁词独立维护 prev_sents。
    """
    reset_config_cache()
    # 单句，无任何前文，ch6，night_bounded=True（通过 timeline_anchor 传入）
    txt = "他看得真切，那目光的落点，正是自己吐纳调息时，气息流转最隐晦、也最易暴露异常的所在。"
    r = detect_scope_violations(txt, 6, _NIGHT_ANCHOR, _ROOT)
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, f"孤立单句应豁免，实际 hard={hard_terms}"
    assert "调息" not in hard_terms, f"孤立单句应豁免，实际 hard={hard_terms}"
def test_ch6_fangcai_descriptive_marker_exempt(tmp_path):
    """Ch6 根因回归：含"方才"的描述性引用句不应被 hard block。

    场景原文（修复前触发 score=85 快速失败）：
        "方才吐纳被打断，他心中一凛。"
    "方才"在修复前不在 _descriptive_markers 集合中，导致该句无法匹配
    描述性引用豁免路径（has_descriptive_context=False），进而硬词"吐纳"
    被误报为 hard_leak。修复后"方才"已加入 _descriptive_markers，
    应豁免。
    """
    # 创建临时配置，包含吐纳/调息等禁词
    cfg = {
        'arc': 'infant', 'chapters': [1, 9],
        'hard_block': ['魂魄印记', '跨界', '守护者', '吐纳', '调息', '气感', '内视', '行气探查', '气机探查'],
        'soft_warn': ['修炼', '境界'],
        'night_anchor_markers': ['子时', '当夜', '数时辰'],
        'negation_markers': ['不是', '并非'],
        'idiom_whitelist': ['魂飞魄散'],
        'dawn_markers': ['天亮', '鱼肚白'],
        'future_markers': ['等', '等到', '再说'],
    }
    (tmp_path / 'config' / 'leak_terms').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'leak_terms' / 'infant.json').write_text(
        json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    reset_config_cache()
    
    txt = "方才吐纳被打断，他心中一凛。"
    r = detect_scope_violations(txt, 6, _NIGHT_ANCHOR, tmp_path)
    hard_terms = [h["term"] for h in r["hard"]]
    soft_kinds = [h["kind"] for h in r["soft"]]
    # "方才"触发描述性引用豁免 → 不应硬阻断
    assert "吐纳" not in hard_terms, (
        f"方才描述性引用应豁免，实际 hard={hard_terms}, soft={soft_kinds}"
    )
    # 豁免句应记录为 tuna_exempt soft（而非漏检）
    assert "tuna_exempt" in soft_kinds, (
        f"豁免应记录 tuna_exempt soft，实际 soft={soft_kinds}"
    )
# ── CC round-23：根因回归测试（章节范围 + 描述性标记 + 比喻豁免）─────────────────


def test_ch7_tuna_exempt_night():
    """ch7 夜间陈老根吐纳 → 豁免（_TUNA_EXEMPT_CHAPTERS 已扩至 {6,7,8,9}）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "是夜子时，陈老根独自盘膝坐在炕边吐纳呼吸，气息绵长。陆烬在旁偷看。",
        7, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" not in [h["term"] for h in r["hard"]]


def test_ch8_tuna_exempt_night():
    """ch8 夜间陈老根调息 → 豁免。"""
    reset_config_cache()
    r = detect_scope_violations(
        "深夜，陈老根在房中调息，气息绵长而规律。陆烬在襁褓里静静看着。",
        8, _NIGHT_ANCHOR, _ROOT,
    )
    assert "调息" not in [h["term"] for h in r["hard"]]


def test_ch9_tuna_exempt_night():
    """ch9 夜间陈老根吐纳 → 豁免。"""
    reset_config_cache()
    r = detect_scope_violations(
        "是夜，陈老根自己吐纳调息，想起往事。",
        9, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" not in [h["term"] for h in r["hard"]]
    assert "调息" not in [h["term"] for h in r["hard"]]


def test_ch5_tuna_still_hard():
    """ch5 吐纳仍 hard（豁免仅 ch6-ch9）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "陈老根夜里吐纳呼吸。",
        5, _NIGHT_ANCHOR, _ROOT,
    )
    assert "吐纳" in [h["term"] for h in r["hard"]]


def test_descriptive_marker_zhe_secret():
    """根因2 回归："这秘密…深夜的吐纳" 描述性引用不应 hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "这秘密，如今不再只藏于深夜的吐纳和孩童的安静里，而是在这白日的村中，在他眼前，赤裸裸地摊开了",
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, f"描述性引用应豁免，实际 hard={hard_terms}"


def test_metaphor_another_world_sound():
    """根因3 回归："像另一个世界的声音" 比喻用法不应 hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "那些热闹隔着一段距离，嗡嗡的，像另一个世界的声音",
        7, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "另一个世界" not in hard_terms, f"比喻用法应豁免，实际 hard={hard_terms}"


def test_literal_another_world_still_hard():
    """根因3 不误放："他来自另一个世界" 真实设定泄漏仍 hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "他来自另一个世界，带着穿越者的记忆。",
        7, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "另一个世界" in hard_terms, f"真实设定泄漏应 hard，实际 hard={hard_terms}"


def test_metaphor_simile_markers_all_variants():
    """比喻豁免覆盖所有喻词变体。"""
    reset_config_cache()
    simile_words = ["像", "如同", "仿佛", "好似", "宛如", "似乎", "犹如", "好比"]
    for marker in simile_words:
        txt = f"那声音{marker}来自另一个世界，很远。"
        r = detect_scope_violations(txt, 7, _NIGHT_ANCHOR, _ROOT)
        hard_terms = [h["term"] for h in r["hard"]]
        assert "另一个世界" not in hard_terms, (
            f"喻词'{marker}'应触发比喻豁免，实际 hard={hard_terms}"
        )


def test_metaphor_no_simile_still_hard():
    """无喻词时"另一个世界"仍 hard（不漏放）。"""
    reset_config_cache()
    r = detect_scope_violations(
        "他来自另一个世界，穿越了时空。",
        7, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "另一个世界" in hard_terms, f"无喻词应硬阻断，实际 hard={hard_terms}"


def test_descriptive_marker_this_present():
    """描述性标记含"这"时吐纳豁免生效。"""
    reset_config_cache()
    r = detect_scope_violations(
        "那夜的吐纳至此已成往事，再也回不去了。",
        7, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, f"含'这'的描述性引用应豁免，实际 hard={hard_terms}"
# ── CC round-23b：真实运行第 3 轮发现的语境降级缺口 ────────────────────────────
def test_ch9_recollection_teach_soft():
    """回忆语境（教导已发生、此刻回想浮现）的传授判定应降为 soft。"""
    reset_config_cache()
    assert not _has_teach_violation(
        "白天那棵古树下的教导，那些缓慢悠长的呼吸节奏，此刻在陆烬脑中清晰浮现。"
    ), "回忆中的教导（非实时传授）不应 hard"


def test_ch6_contemplation_teach_soft():
    """思虑语境（意味着/并非——内心权衡而非实授）的传授判定应降为 soft。"""
    reset_config_cache()
    assert not _has_teach_violation(
        "传授法门，意味着他不再仅仅是一个沉默的养父，意味着他将自己过往的一角秘密，分享给了这个捡来的孩子。"
    ), "思虑中的传授不应 hard"
    assert not _has_teach_violation(
        "呼吸法的传授，并非口头讲述那般简单。"
    ), "判断句中的传授不应 hard"


def test_ch6_warning_qiyin_ru_ti_soft():
    """劝阻/警告语境（若要强行…稍有不慎…）的硬词命中应降为 soft。"""
    reset_config_cache()
    r = detect_scope_violations(
        "若要强行引气入体，以婴儿脆弱的经脉，稍有不慎，便是摧残而非帮助。",
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "引气入体" not in hard_terms, f"劝阻语境的引气入体应降 soft，实际 hard={hard_terms}"


def test_teach_give_far_span_not_hard():
    """跨字'传…给'距离超限（给=分享对象非受事）不得判既成传授。"""
    reset_config_cache()
    assert not _has_teach_violation(
        "传授法门，意味着他将自己过往的一角秘密，分享给了这个捡来的孩子。"
    ), "远距'传…给'（给为分享对象）不应 hard"


def test_teach_give_near_span_still_hard():
    """跨字'传…给'近距（受事）仍 hard——不误放。"""
    reset_config_cache()
    assert _has_teach_violation(
        "他传呼吸之法给陆烬，教他吐纳。"
    ), "近距既成传授必须 hard"
    assert _has_teach_violation(
        "他并非传授呼吸法给陆烬——已经传了。"
    ), "含'并非'但既成传授短语仍 hard"

# ── CC round-24：ch6 四连败根因回归测试（夜锚缺口 + 功法描述句豁免）─────────────────


def test_ch6_fangfa_desc_exempt_night_anchor_gap():
    """根因1 回归：功法/法门描述句不应要求夜锚。

    场景原文（第四轮草稿卡句）：
        "那法门没什么移山填海的神通，最大的用处，就是能在吐纳间，
         将吸入体内的驳杂之气稍作梳理，化去其中最为伤身的部分，勉强温养内腑。"

    该句主语是"那法门"，无人物施为，纯属功法评价，语义上不需要夜锚。
    修复前：因无夜锚标记被 hard block。
    修复后：含"法门"等功法名词且无婴儿施为 → 直接豁免。
    """
    reset_config_cache()
    # 不传 night_bounded，模拟 runtime 中 in_world_datetime 为空的场景
    r = detect_scope_violations(
        "那法门没什么移山填海的神通，最大的用处，就是能在吐纳间，"
        "将吸入体内的驳杂之气稍作梳理，化去其中最为伤身的部分，勉强温养内腑。",
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    soft_kinds = [s["kind"] for s in r["soft"]]
    assert "吐纳" not in hard_terms, (
        f"功法描述句应豁免，实际 hard={hard_terms}"
    )
    assert "tuna_exempt" in soft_kinds, (
        f"功法描述句应记录 tuna_exempt soft，实际 soft={soft_kinds}"
    )


def test_ch6_yueguang_tiaoxi_exempt_night_anchor_expanded():
    """根因2 回归：含"月光"等自然夜间描写的句子应通过夜锚检测。

    场景原文（第四轮草稿卡句）：
        "那时他坐在几乎相同的位置，正借着朦胧月光做着某种极其隐秘的调息。"

    该句含"月光"，属于自然夜间语境描写，语义上明确为夜间。
    修复前：infant.json 的 night_anchor_markers 不含"月光"，导致 has_night=False → hard。
    修复后：night_anchor_markers 已扩充包含"月光"等词 → has_night=True → 豁免。
    """
    reset_config_cache()
    r = detect_scope_violations(
        "那时他坐在几乎相同的位置，正借着朦胧月光做着某种极其隐秘的调息。",
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    soft_kinds = [s["kind"] for s in r["soft"]]
    assert "调息" not in hard_terms, (
        f"月光夜锚句应豁免，实际 hard={hard_terms}"
    )
    assert "tuna_exempt" in soft_kinds, (
        f"月光夜锚句应记录 tuna_exempt soft，实际 soft={soft_kinds}"
    )


def test_red_line_fangfa_desc_with_baby_still_hard():
    """不误放：功法描述句中若含婴儿施为信号，仍应 hard。"""
    reset_config_cache()
    r = detect_scope_violations(
        "那法门没什么大用，但陆烬在吐纳间强行修炼，气息紊乱。"
        + "x" * 500,
        6, _NIGHT_ANCHOR, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    # 含婴儿词"陆烬"+ 吐纳，即使有"法门"也不应豁免
    assert "吐纳" in hard_terms, (
        f"婴儿施为应 hard，实际 hard={hard_terms}"
    )

# ── CC round-25：ch6 第五轮真实卡句——传授设想/放弃/功法跨句 + polarity 未然 ────
def test_ch6_teach_hypothetical_soft():
    """未然设想（若是…或许）的传授判定应降为 soft。"""
    reset_config_cache()
    assert not _has_teach_violation(
        "若是传授给他，让他学着调整呼吸，学着在吸入那无处不在的浊气时，多少过滤掉一丝最令人难受的杂质，或许……就能让他好过一些。"
    ), "未然设想中的传授不应 hard"


def test_ch6_teach_abandoned_soft():
    """放弃传授（念头被按了下去）应降为 soft。"""
    reset_config_cache()
    assert not _has_teach_violation(
        "传授呼吸法的念头，被他自己亲手按了下去，沉入心底某个角落。"
    ), "放弃的传授念头不应 hard"


def test_ch6_teach_definite_with_hypothetical_still_hard():
    """含假设词但既成传授仍 hard——不误放。"""
    reset_config_cache()
    assert _has_teach_violation(
        "他权衡再三，最终将呼吸之法传给了陆烬——尽管心中或许仍有犹豫。"
    ), "既成传授即使含或许也必须 hard"


def test_ch6_fangfa_cross_sentence_exempt():
    """功法评价跨句指代（前句含法门）应豁免夜锚要求。"""
    reset_config_cache()
    r = detect_scope_violations(
        "那法门没什么移山填海的神通。最大的用处，就是能在吐纳间，将吸入体内的驳杂之气稍作梳理，化去其中最为伤身的部分，勉强温养内腑。",
        6, None, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, f"跨句功法评价应豁免，实际 hard={hard_terms}"


def test_ch6_teach_give_hypothetical_soft():
    """跨字传…给+未然标记（若是传授给他）不得走既成第一优先。"""
    reset_config_cache()
    assert not _has_teach_violation(
        "若是传授给他，让他学着调整呼吸，学着在吸入那无处不在的浊气时，多少过滤掉一丝最令人难受的杂质，或许……就能让他好过一些。"
    ), "未然传…给不应走既成第一优先"


# ── CC round-26：ch6 第六轮真实卡句——「昨夜」夜锚缺口（夜间描写词表不完整）──
def test_ch6_yesterday_night_anchor_exempt():
    """昨夜盘坐调息属夜锚语境，应豁免夜锚要求。"""
    reset_config_cache()
    r = detect_scope_violations(
        "昨夜盘坐调息的人，此刻只是个寻常农人。",
        6, None, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "调息" not in hard_terms, f"昨夜应豁免，实际 hard={hard_terms}"


def test_ch6_nightfall_anchor_exempt():
    """夜锚句（含上下文回溯陈老根）应豁免。"""
    reset_config_cache()
    cases = [
        "陈老根回到院里，四下寂静。夜幕垂下，他在院里盘坐调息。",
        "暮色四合，院中吐纳声隐约可闻，是陈老根照例的晚课。",
    ]
    for s in cases:
        r = detect_scope_violations(s, 6, None, _ROOT)
        hard_terms = [h["term"] for h in r["hard"]]
        assert not hard_terms, f"夜锚句不应 hard: {s} -> {hard_terms}"


def test_ch6_yesterday_night_descriptive_exempt():
    """昨夜盘坐调息的人是叙述性引用（主语泛称'人'），应豁免。"""
    reset_config_cache()
    r = detect_scope_violations(
        "昨夜盘坐调息的人，此刻只是个寻常农人。",
        6, None, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert not hard_terms, f"昨夜叙述句不应 hard: {hard_terms}"


# ── CC round-26b：ch6 第七轮真实卡句——「彼时…被刻意遗忘多年」回忆语境豁免 ──
def test_ch6_recall_flashback_exempt():
    """回忆/追忆叙述（彼时/被遗忘多年）无夜间约束，应豁免。"""
    reset_config_cache()
    r = detect_scope_violations(
        "彼时，他正沉浸于某种被刻意遗忘多年的吐纳节奏之中，胸膛微微起伏，吸纳着常人难以察觉的气息。",
        6, None, _ROOT,
    )
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, f"回忆句应豁免，实际 hard={hard_terms}"


def test_ch6_daytime_tuna_still_hard():
    """白天无夜锚的当下吐纳 → concept 解锁后放行（round-27c 替代夜锚要求）。"""
    reset_config_cache()
    r = detect_scope_violations("陈老根白天在院子里吐纳。", 6, None, _ROOT)
    hard_terms = [h["term"] for h in r["hard"]]
    assert "吐纳" not in hard_terms, "ch6 起陈老根吐纳由 concept_unlocks 放行"


def test_ch6_recall_with_baby_still_hard():
    """回忆中婴儿吐纳仍 hard（回忆豁免不覆盖婴儿红线）。"""
    reset_config_cache()
    r = detect_scope_violations("彼时，陆烬也曾学着吐纳起来。", 6, None, _ROOT)
    hard_terms = [h["term"] for h in r["hard"]]
    assert hard_terms, "婴儿施为的回忆句必须 hard"


# ── CC round-26c：ch6 第八轮真实卡句——「传授呼吸法的心思…反复翻腾」心理活动降级 ──
def test_ch6_teach_intention_mental_soft():
    """传授心思/念头翻腾=未实施心理活动，应 soft。"""
    reset_config_cache()
    r = detect_scope_violations("传授呼吸法的心思，在胸腔里反复翻腾。", 6, None, _ROOT)
    hard_terms = [h["term"] for h in r["hard"]]
    assert not hard_terms, f"心思翻腾应 soft，实际 hard={hard_terms}"


def test_ch6_teach_intention_enacted_still_hard():
    """心思已付诸行动（教了）→ 仍 hard，防误放。"""
    reset_config_cache()
    r = detect_scope_violations("他把传授呼吸法的心思付诸行动，教了陆烬吐纳。", 6, None, _ROOT)
    hard_terms = [h["term"] for h in r["hard"]]
    assert hard_terms, "已实施的传授必须 hard"


# ── CC round-26d：ch6 第九轮真实卡句——经脉/灵根/仙门合法语境（议论/担心/假设）+ 传功受事 ──
def test_ch6_contextual_hardword_exempt():
    """担心/假设/议论语境中的经脉/灵根/仙门应降 soft。"""
    reset_config_cache()
    cases = [
        "这种人，要么早早被纳入仙门道观，要么……便在这污浊世道里无声无息地夭折。",
        "他从未想过传授他人，一来是怕所托非人，二来是深知这东西的凶险——若无正确引导，贸然尝试引气，极易被浊气反噬，轻则经脉受损，重则神智癫狂。",
        "若是寻常父母，得知自家孩儿有此禀赋，怕是欣喜若狂，千方百计也要寻个门路，送孩子去测灵根，拜仙门，求个鲤鱼跃龙门的前程。",
    ]
    for s in cases:
        r = detect_scope_violations(s, 6, None, _ROOT)
        hard_terms = [h["term"] for h in r["hard"]]
        assert not hard_terms, f"合法语境不应 hard: {s[:30]} -> {hard_terms}"


def test_ch6_teach_verb_baby_recipient_hard():
    """传授动词+婴儿受事（传功给陆烬/修士趁夜传功）仍 hard。"""
    reset_config_cache()
    for s in ["修士传功给陆烬。", "婴儿经脉受损，修士趁夜传功。"]:
        r = detect_scope_violations(s, 6, None, _ROOT)
        hard_terms = [h["term"] for h in r["hard"]]
        assert hard_terms, f"婴儿受事的传授必须 hard: {s}"


def test_ch6_teach_negated_soft():
    """否定传授（并未/从未传功）应 soft。"""
    reset_config_cache()
    for s in ["婴儿在旁，陈老根并未传功。", "他从未想过传授他人。"]:
        r = detect_scope_violations(s, 6, None, _ROOT)
        hard_terms = [h["term"] for h in r["hard"]]
        assert not hard_terms, f"否定传授不应 hard: {s} -> {hard_terms}"
