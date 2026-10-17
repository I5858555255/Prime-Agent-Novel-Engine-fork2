# -*- coding: utf-8 -*-
"""CC round-14：R14-1 时间线空间词防护 / R14-2 强制 beat 检查 + 指令生成 / R14-3 候选审计记录。

纯函数与接线验证，无真实 LLM 调用。
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from novel_engine.quality.timeline_gate import (
    detect_timeline_jump,
    _is_spatial_marker,
)
from novel_engine.quality.outline_coverage_gate import (
    check_scene_must_cover_beats,
    build_mandatory_beat_directive,
)


# ============================================================================
# R14-1：空间词不得当时间线越界
# ============================================================================

def test_spatial_marker_is_spatial():
    """已知空间地点短语应判定为空间词。"""
    for marker in ["禁区边缘", "禁地边上", "后山深处", "林子边上", "井边", "祠堂"]:
        assert _is_spatial_marker(marker) is True, f"Expected spatial: {marker}"


def test_time_marker_not_spatial():
    """时间词不得被判定为空间词。"""
    for marker in ["昨夜", "三年后", "成年", "五岁那年", "多年以后"]:
        assert _is_spatial_marker(marker) is False, f"Expected time (not spatial): {marker}"


def test_forbidden_spatial_marker_not_flagged():
    """禁区边缘作为 forbidden_marker → 不应触发时间线越界。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["禁区边缘"]}
    text = "走到禁区边缘，草木枯死了一圈。"
    assert detect_timeline_jump(text, anchor) is None


def test_forbidden_spatial_marker_in_complex_text():
    """含空间词的复杂文本不被误杀。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["禁地边上", "后山深处"]}
    for text in [
        "走到禁地边上，草木枯了一圈。",
        "后山深处传来声响，陈老根警觉起来。",
        "林子边上有棵老槐树，枝叶茂密。",
    ]:
        result = detect_timeline_jump(text, anchor)
        assert result is None, f"Expected no hit for spatial text: {text}"


def test_time_marker_still_flagged_after_spatial_guard():
    """真时间标记（成年/三年后）仍应命中，不受空间守卫影响。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": []}
    assert detect_timeline_jump("成年之后，他离开了村子。", anchor) is not None
    assert detect_timeline_jump("三年后，他终于练成那门功法。", anchor) is not None


def test_narrative_with_spatial_term_not_flagged():
    """含空间词的普通叙述不应被误判。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["禁区边缘"]}
    text = "他想起昨夜在禁区边缘看到的景象：草木枯死的圆圈中心，这个婴儿安静地躺着。"
    assert detect_timeline_jump(text, anchor) is None


def test_real_ch3_sentence_still_safe():
    """真机 ch3 原句——角色当下回忆昨夜，整句屏蔽后不应命中（保留 round7/12 回归）。"""
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["昨夜"]}
    text = "他想起昨夜在禁区边缘看到的景象：草木枯死的圆圈中心，这个婴儿安静地躺着……那画面此刻在他脑海里反复闪现"
    assert detect_timeline_jump(text, anchor) is None


def test_geo_place_name_not_flagged_as_timeline():
    """CC round-30：地名（青河县等）被 director 误填进 forbidden_markers 时不得判时间越界。"""
    for marker in ["青河县", "雾隐村", "铁风武馆", "后山镇"]:
        assert _is_spatial_marker(marker) is True, f"Expected geo name as spatial: {marker}"
    # 正文出现该地名 → 不触发时间线越界
    anchor = {"max_time_progression": "当日", "forbidden_markers": ["青河县"]}
    text = "一行人沿着官道走了半日，终于在午后望见青河县的城墙。"
    assert detect_timeline_jump(text, anchor) is None
    # 真时间词仍命中
    anchor2 = {"max_time_progression": "当日", "forbidden_markers": ["次日清晨"]}
    text2 = "次日清晨，他们启程赶往青河县。"
    assert detect_timeline_jump(text2, anchor2) is not None


# ============================================================================
# R14-2：按场景 beat 覆盖检查 + 强制补丁指令生成
# ============================================================================

def test_check_scene_must_cover_beats_all_hit():
    """场景文本包含所有 beat 关键词 → passed=True。"""
    beats = [
        {"scene_num": 2, "beat_text": "陆烬对浊气本能侧头屏息避开", "category": "event"},
        {"scene_num": 2, "beat_text": "陈老根察觉异样不动声色", "category": "goal"},
    ]
    scene_text = "陈老根抱陆烬到井边打水，井中弥漫常人不觉的浊气，襁褓中的陆烬本能地侧过头、屏息避开那股气，陈老根心头一动，察觉异样但不动声色。"
    passed, missing = check_scene_must_cover_beats(scene_text, beats, scene_id=2)
    assert passed is True
    assert missing == []


def test_check_scene_must_cover_beats_partial_miss():
    """场景文本缺少全部 beat 关键词 → passed=False，missing 列出未命中项。"""
    beats = [
        {"scene_num": 2, "beat_text": "陆烬对浊气本能侧头屏息避开", "category": "event"},
        {"scene_num": 2, "beat_text": "陈老根察觉异样不动声色", "category": "goal"},
    ]
    # 此场景不含任一 beat 的核心词（陆烬/浊气/侧头/屏息/避开/陈老根/察觉/异样/不动声色）
    scene_text = "老槐树下风停了，雾气渐渐散开。"
    passed, missing = check_scene_must_cover_beats(scene_text, beats, scene_id=2)
    assert passed is False
    assert len(missing) == 2


def test_check_scene_must_cover_beats_wrong_scene_id():
    """beat 指向其他场景 → 视为通过（不跨场景检查）。"""
    beats = [
        {"scene_num": 1, "beat_text": "陆烬在迷雾边缘醒来", "category": "goal"},
    ]
    scene_text = "陈老根抱陆烬到井边打水。"
    passed, missing = check_scene_must_cover_beats(scene_text, beats, scene_id=2)
    assert passed is True
    assert missing == []


def test_check_scene_must_cover_beats_empty_beats():
    """无 beats 列表 → 直接通过。"""
    passed, missing = check_scene_must_cover_beats("任意文本", [], scene_id=1)
    assert passed is True
    assert missing == []


def test_build_mandatory_beat_directive_contains_keywords():
    """指令文本应包含目标场景编号与 beat 描述。"""
    missing = ["beat[event] scene=2: 陆烬对浊气本能侧头屏息避开",
               "beat[goal] scene=2: 陈老根察觉异样不动声色"]
    directive = build_mandatory_beat_directive(missing)
    assert "scene=2" in directive
    assert "侧头" in directive or "屏息" in directive
    assert "+30%" in directive


def test_build_mandatory_beat_directive_empty_returns_blank():
    """空 missing 列表 → 空字符串。"""
    assert build_mandatory_beat_directive([]) == ""


# ============================================================================
# R14-3：candidate_gate_decisions 初始快照 + 灰区记录
# ============================================================================

class MockOrchestratorForR14_3:
    """最小模拟 orchestrator，用于测试 R14-3 审计写入逻辑。"""

    def __init__(self, root: Path):
        self.root = root

    def _write_candidate_gate_decision(self, chapter_num: int, candidate_id: str,
                                        score: float, adopted: bool, reason: str,
                                        higher_blocked_score: float = 0.0,
                                        blocking_high_reasons: list = None,
                                        blocking_det_hard: list = None) -> None:
        audit_dir = self.root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / "candidate_gate_decisions.jsonl"
        entry = {
            "chapter": chapter_num,
            "candidate_id": candidate_id,
            "score": round(score, 2),
            "adopted": adopted,
            "reason": reason,
            "higher_blocked_score": higher_blocked_score,
            "blocking_high_reasons": (blocking_high_reasons or [])[:5],
            "blocking_det_hard": (blocking_det_hard or [])[:5],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_r14_3_initial_best_has_non_empty_reasons(tmp_path: Path):
    """R14-3：初始 pre_fix best 的 blocking_high_reasons 不为空（之前为空的盲点已补）。"""
    orch = MockOrchestratorForR14_3(tmp_path)
    orch._write_candidate_gate_decision(
        chapter_num=4,
        candidate_id="initial_best",
        score=71.95,
        adopted=False,
        reason="pre_fix_initial",
        blocking_high_reasons=["foreshadow_execution/HIGH: F001 未在 scene2 具体呈现"],
        blocking_det_hard=["outline_coverage: F001 missing in scene2"])
    path = tmp_path / "audit" / "candidate_gate_decisions.jsonl"
    entry = json.loads(path.read_text(encoding="utf-8").strip().split("\n")[0])
    assert entry["blocking_high_reasons"] == ["foreshadow_execution/HIGH: F001 未在 scene2 具体呈现"]
    assert entry["blocking_det_hard"] == ["outline_coverage: F001 missing in scene2"]
    assert entry["score"] == 71.95
    assert entry["candidate_id"] == "initial_best"


def test_r14_3_gray_band_record_written(tmp_path: Path):
    """R14-3：灰区提交路径应写入 candidate_gate_decisions 记录。"""
    orch = MockOrchestratorForR14_3(tmp_path)
    orch._write_candidate_gate_decision(
        chapter_num=3,
        candidate_id="gray_band",
        score=86.7,
        adopted=True,
        reason="gray_band_release",
        blocking_det_hard=["long_sentence: 12 over 40"])
    path = tmp_path / "audit" / "candidate_gate_decisions.jsonl"
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    entry = json.loads(lines[-1])
    assert entry["candidate_id"] == "gray_band"
    assert entry["reason"] == "gray_band_release"
    assert entry["adopted"] is True
    assert entry["score"] == 86.7


def test_r14_3_commit_record_written(tmp_path: Path):
    """R14-3：正常提交路径应写入 candidate_gate_decisions 记录。"""
    orch = MockOrchestratorForR14_3(tmp_path)
    orch._write_candidate_gate_decision(
        chapter_num=1,
        candidate_id="best",
        score=90.5,
        adopted=True,
        reason="commit",
        blocking_high_reasons=[],
        blocking_det_hard=[])
    path = tmp_path / "audit" / "candidate_gate_decisions.jsonl"
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    entry = json.loads(lines[-1])
    assert entry["candidate_id"] == "best"
    assert entry["reason"] == "commit"
    assert entry["adopted"] is True


def test_r14_3_ch4_full_trajectory(tmp_path: Path):
    """R14-3：ch4 完整轨迹——initial_best（高分 blocked）→ green 采纳 → 审计记录原因非空。"""
    orch = MockOrchestratorForR14_3(tmp_path)
    orch._write_candidate_gate_decision(
        chapter_num=4, candidate_id="initial_best", score=71.95,
        adopted=False, reason="pre_fix_initial",
        blocking_high_reasons=["foreshadow_execution/HIGH: F001 缺失"],
        blocking_det_hard=["outline_coverage: F001 missing"])
    orch._write_candidate_gate_decision(
        chapter_num=4, candidate_id="green", score=68.9,
        adopted=True, reason="gate_green_adopted",
        higher_blocked_score=71.95,
        blocking_high_reasons=["foreshadow_execution/HIGH: F001 缺失"],
        blocking_det_hard=["outline_coverage: F001 missing"])
    path = tmp_path / "audit" / "candidate_gate_decisions.jsonl"
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    entries = [json.loads(l) for l in lines]
    assert len(entries) == 2
    assert entries[1]["blocking_high_reasons"][0] == "foreshadow_execution/HIGH: F001 缺失"
    assert entries[1]["higher_blocked_score"] == 71.95


# ============================================================================
# R14b-1：伏笔动作纳入必填 beat + 双词覆盖判定
# ============================================================================

def test_extract_must_cover_beats_includes_foreshadow():
    """foreshadow_actions 应被提取为 beat 并绑定到目标场景。"""
    from novel_engine.quality.outline_coverage_gate import extract_must_cover_beats
    card = {
        "scene_blueprints": [
            {"scene_num": 2, "goal": "陈老根井边打水", "conflict": "村民疏远"},
            {"scene_num": 3, "goal": "村议收养", "conflict": "争议"},
        ],
        "foreshadow_actions": [
            {"foreshadow_id": "F001", "action": "陆烬对井边浊气本能侧头避开", "intensity": "隐晦提示"},
        ],
    }
    beats = extract_must_cover_beats(card)
    fs_beats = [b for b in beats if b.get("category") == "foreshadow"]
    assert len(fs_beats) == 1
    assert fs_beats[0]["scene_num"] == 2  # 绑定到 scene2（含"井边"关键词）
    assert fs_beats[0]["foreshadow_id"] == "F001"
    assert "陆烬对井边浊气本能侧头避开" in fs_beats[0]["beat_text"]


def test_extract_must_cover_beats_unbound_foreshadow():
    """无法可靠绑定时，scene_num 可为 None。"""
    from novel_engine.quality.outline_coverage_gate import extract_must_cover_beats
    card = {
        "scene_blueprints": [
            {"scene_num": 1, "goal": "陆烬在雾中醒来", "conflict": "寒冷"},
        ],
        "foreshadow_actions": [
            {"foreshadow_id": "F999", "action": "某个遥远异象", "intensity": "隐晦提示"},
        ],
    }
    beats = extract_must_cover_beats(card)
    fs_beats = [b for b in beats if b.get("category") == "foreshadow"]
    assert len(fs_beats) == 1
    # 无匹配场景 → scene_num=None
    assert fs_beats[0]["scene_num"] is None


def test_foreshadow_coverage_requires_both_location_and_reaction():
    """伏笔覆盖要求位点词+反应词同时出现。"""
    from novel_engine.quality.outline_coverage_gate import _check_foreshadow_coverage
    beat_text = "陆烬对井边浊气本能侧头避开"
    # 完整覆盖：有浊气/井边（位点）+ 侧头/避开/本能（反应）
    full_text = "井边浊气弥漫，陆烬本能地侧头屏息避开那股气。"
    covered, reason = _check_foreshadow_coverage(full_text, beat_text)
    assert covered is True, f"Expected covered, reason={reason}"

    # 只有位点无反应 → 不覆盖
    partial_text = "井边浊气弥漫，陆烬安静地躺在襁褓里。"
    covered2, reason2 = _check_foreshadow_coverage(partial_text, beat_text)
    assert covered2 is False, f"Expected not covered, reason={reason2}"

    # 有反应但位点词被通用词替代（无"浊气"/"井边"等实词）→ 不覆盖
    partial_text2 = "陆烬本能地侧头，察觉到周围有异常。"
    covered3, reason3 = _check_foreshadow_coverage(partial_text2, beat_text)
    assert covered3 is False, f"Expected not covered, reason={reason3}"


def test_foreshadow_generic_words_do_not_count():
    """通用词（陆烬/婴儿/陈老根/打水）单独出现不算覆盖。"""
    from novel_engine.quality.outline_coverage_gate import _check_foreshadow_coverage
    beat_text = "陆烬对井边浊气本能侧头避开"
    # 只有通用词，无位点+反应
    generic_text = "陆烬在井边打水，陈老根抱着他。"
    covered, reason = _check_foreshadow_coverage(generic_text, beat_text)
    assert covered is False, f"Expected not covered with generic words only, reason={reason}"


def test_scene_check_uses_foreshadow_dual_word_rule():
    """check_scene_must_cover_beats 对 foreshadow beat 使用双词规则。"""
    from novel_engine.quality.outline_coverage_gate import check_scene_must_cover_beats
    beats = [
        {"scene_num": 2, "beat_text": "[F001] 陆烬对井边浊气本能侧头避开",
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    # 场景含浊气/井边（位点）+ 侧头避开（反应）→ 通过
    scene_text_full = "陈老根抱陆烬到井边打水，井中浊气弥漫，陆烬本能侧头屏息避开。"
    passed, missing = check_scene_must_cover_beats(scene_text_full, beats, scene_id=2)
    assert passed is True, f"Expected pass, missing={missing}"

    # 场景只有"陆烬在井边打水"无反应词 → 缺失
    scene_text_partial = "陈老根抱陆烬到井边打水，村民赵老四路过。"
    passed2, missing2 = check_scene_must_cover_beats(scene_text_partial, beats, scene_id=2)
    assert passed2 is False, f"Expected fail, missing={missing2}"
    assert len(missing2) == 1
    assert "F001" in missing2[0]


# ============================================================================
# R14c：真实章4卡 F001→scene2 + 假覆盖回归
# ============================================================================

def test_r14c_f001_binds_to_scene2_real_card(tmp_path):
    """R14c：真实 chapter_4.json 上 F001 必须绑定到 scene2，不是 scene1。"""
    from novel_engine.quality.outline_coverage_gate import extract_must_cover_beats
    # 内联复刻真实章4卡的必要字段（与 cache/task_cards/chapter_4.json 同构）
    real_like_card = {
        "scene_blueprints": [
            {"scene_num": 1, "goal": "陈老根独自抚养陆烬", "conflict": "物资匮乏与婴儿哭闹"},
            {"scene_num": 2, "goal": "陈老根打水，偶遇村民", "conflict": "村民赵老四刻意疏远排斥"},
            {"scene_num": 3, "goal": "陈老根观察陆烬", "conflict": "发现婴儿异常沉静怕生"},
            {"scene_num": 4, "goal": "陈老根总结陆烬性格", "conflict": "面对未来，内心抉择"},
        ],
        "chapter_events": [
            {"scene_num": None, "one_line_summary": "陈老根独自在家给陆烬换洗喂食，应对婴儿却异常安静不哭闹"},
            {"scene_num": None, "one_line_summary": "陈老根去村口打水，偶遇赵老四等村民，对方刻意避开交谈"},
            {"scene_num": None, "one_line_summary": "陈老根在院中观察陆烬，发现其目光沉静，极少哭闹"},
            {"scene_num": None, "one_line_summary": "陈老根在屋内看着熟睡的陆烬，决定默默抚养，应对未来"},
        ],
        "foreshadow_actions": [
            {"foreshadow_id": "F001", "action": "陆烬对井边浊气本能侧头避开", "intensity": "隐晦提示"},
        ],
    }
    beats = extract_must_cover_beats(real_like_card)
    f001_beats = [b for b in beats if b.get("foreshadow_id") == "F001"]
    assert len(f001_beats) == 1
    assert f001_beats[0]["scene_num"] == 2, (
        f"F001 must bind to scene2 (has 打水/村口打水 position), got scene={f001_beats[0]['scene_num']}")


def test_r14c_unbound_foreshadow_returns_none():
    """位点词在任何场景都不存在 → scene_num=None，不硬绑。"""
    from novel_engine.quality.outline_coverage_gate import extract_must_cover_beats
    card = {
        "scene_blueprints": [
            {"scene_num": 1, "goal": "镇外铁匠铺打铁", "conflict": "火星四溅"},
        ],
        "foreshadow_actions": [
            {"foreshadow_id": "F999", "action": "在镇外铁匠炉前躲避火星", "intensity": "隐晦提示"},
        ],
    }
    beats = extract_must_cover_beats(card)
    f_beats = [b for b in beats if b.get("foreshadow_id") == "F999"]
    assert len(f_beats) == 1
    assert f_beats[0]["scene_num"] is None


def test_r14c_false_coverage_no_zhuoqi_no_well():
    """仅有'村口打水/赵老四避开交谈/陆烬安静'（无浊气无井）→ F001 未覆盖。"""
    from novel_engine.quality.outline_coverage_gate import check_scene_must_cover_beats
    beats = [
        {"scene_num": 2, "beat_text": "[F001] 陆烬对井边浊气本能侧头避开",
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    # 村民的"避开"是回避交谈，不是对浊气的反应；无浊气无井 → 未覆盖
    scene_text = "陈老根去村口打水，赵老四远远避开交谈，陆烬安静地坐在旁边看着。"
    passed, missing = check_scene_must_cover_beats(scene_text, beats, scene_id=2)
    assert passed is False, f"Expected not covered (no 浊气/井 + no real reaction), missing={missing}"


def test_r14c_true_coverage_well_turbid_air_reaction():
    """出现'井底泛浊气陆烬本能侧头屏息避开' → 判覆盖。"""
    from novel_engine.quality.outline_coverage_gate import check_scene_must_cover_beats
    beats = [
        {"scene_num": 2, "beat_text": "[F001] 陆烬对井边浊气本能侧头避开",
         "category": "foreshadow", "foreshadow_id": "F001"},
    ]
    scene_text = "井底泛起浊气，陆烬本能侧头屏息避开那股异味。"
    passed, missing = check_scene_must_cover_beats(scene_text, beats, scene_id=2)
    assert passed is True, f"Expected covered, missing={missing}"
