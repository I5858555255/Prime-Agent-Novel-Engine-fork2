"""爽点系统 L1-L4 TDD (red first).

四层爽点闭环（爽点 = 压抑的兑现）：
  L1 简写层：高潮章 DRAFT 必含 payoff beat 且前置。
  L2 扩写硬验收：高潮章 payoff 未引爆 -> check_prose 阻断。
  L3 爽点自动返工：爽点 < floor / arc climax 违规 -> 定向重扩，每章 2 轮上限，
     连续 2 轮不达标 -> HARD_BLOCK（防死循环）。
  L4 指标 + drift：每卷爽点达标章占比（>= 60% 目标），跌破报 drift_suspect。

Deterministic logic lives in `novel_engine.condensed.satisfaction` (no LLM).
Fixtures are synthetic (superpowers test rule: no real generated artifacts).
"""
import json
from pathlib import Path

import pytest

from novel_engine.condensed import satisfaction
from novel_engine.condensed.satisfaction import (
    climax_chapters,
    draft_has_payoff,
    is_climax_chapter,
    payoff_hard_block,
    should_retry_satisfaction,
    satisfaction_retry_decision,
    volume_satisfaction_ratio,
    drift_suspect,
    is_suppression_exempt,
    satisfaction_floor_for,
    volume_satisfaction_ratio_dual,
)


# ---------------------------------------------------------------------------
# L1: payoff beat design in DRAFT (condensed) chapters
# ---------------------------------------------------------------------------

def _chapters():
    """Synthetic arc: climax_at = [5, 10, 15, 20], min = 4."""
    arc = {"climax_at": {"chapters": [5, 10, 15, 20], "min": 4}}
    return arc, [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20]


def test_draft_beat_payoff_high_climax_chapter():
    """A climax chapter whose DRAFT has a payoff beat -> recognized as a
    satisfaction-design chapter; a non-climax chapter without one -> not."""
    arc, chapters = _chapters()
    climax_ids = set(climax_chapters(arc, chapters))
    assert {5, 10, 15, 20} <= climax_ids

    # climax chapter 5 with a payoff beat -> draft_has_payoff True
    ch5 = {"scenes": [{"id": "S1", "beats": [
        {"id": "S1-B1", "event_ids": ["E3"], "action": "陆烬当场亮出契约，反将一军",
         "payoff": {"type": "打脸", "release_of": "F2", "trigger": "契约摊开", "intensity": 5}}]}]}
    assert draft_has_payoff(ch5) is True
    assert is_climax_chapter(5, arc) is True

    # non-climax chapter with no payoff beat -> not a designed satisfaction chapter
    ch7 = {"scenes": [{"id": "S1", "beats": [
        {"id": "S1-B1", "event_ids": ["E7"], "action": "陈老根锁抽屉", "payoff": None}]}]}
    assert draft_has_payoff(ch7) is False
    assert is_climax_chapter(7, arc) is False


def test_climax_chapter_must_carry_payoff_and_place_it_front():
    """L1 rule: a climax chapter that LACKS any payoff beat is an authoring
    defect -> draft_has_payoff flags it so the DRAFT repair loop can add one."""
    arc, _ = _chapters()
    ch10 = {"scenes": [{"id": "S1", "beats": [
        {"id": "S1-B1", "event_ids": ["E10"], "action": "陆烬沉默观察", "payoff": None}]}]}
    assert is_climax_chapter(10, arc) is True
    # no payoff beat on a climax chapter -> authoring defect
    assert draft_has_payoff(ch10) is False
    # a climax chapter WITH a payoff beat -> front-loaded design satisfied
    ch10b = {"scenes": [{"id": "S1", "beats": [
        {"id": "S1-B1", "event_ids": ["E10"], "action": "陆烬当众反制",
         "payoff": {"type": "反转", "release_of": "压抑", "trigger": "翻牌", "intensity": 4}}]}]}
    assert draft_has_payoff(ch10b) is True


# ---------------------------------------------------------------------------
# L2: hard acceptance — climax chapter payoff must be ignited in the prose
# ---------------------------------------------------------------------------

def test_expand_hard_accept_rejects_low_payoff():
    """A climax chapter whose payoff beat did NOT ignite in the prose ->
    payoff_hard_block returns a blocking entry (the 11th hard check)."""
    arc, _ = _chapters()
    ch5_card = {"chapter": 5, "scenes": [{"id": "S1", "beats": [
        {"id": "S1-B1", "event_ids": ["E3"], "action": "陆烬当场亮出契约反将一军",
         "payoff": {"type": "打脸", "trigger": "契约摊开", "intensity": 5}}]}]}
    # prose that does NOT echo the payoff trigger/action
    weak_prose = "那天村里很安静。陈老根坐在门口，抽了很久的旱烟。"
    blocked = payoff_hard_block(weak_prose, ch5_card, arc)
    assert blocked is not None
    assert blocked["check"] == "payoff_not_ignited"
    assert blocked["waivable"] is False  # hard (non-waivable) on climax chapters

    # prose that DOES ignite the payoff (echoes trigger + action CJK)
    strong_prose = "陆烬把契约往桌上一摊，众人脸色骤变——他反将一军，契约摊开的那一刻，" \
                   "原先轻视他的人哑口无言。那天的寂静被彻底打破。"
    assert payoff_hard_block(strong_prose, ch5_card, arc) is None


def test_non_climax_payoff_missing_is_note_not_block():
    """On non-climax chapters a missing payoff is recorded (note), not a block."""
    ch7_card = {"chapter": 7, "scenes": [{"id": "S1", "beats": [
        {"id": "S1-B1", "event_ids": ["E7"], "action": "陈老根锁抽屉", "payoff": None}]}]}
    arc, _ = _chapters()
    result = payoff_hard_block("陈老根锁了抽屉。", ch7_card, arc)
    # no payoff beat designed + not a climax chapter -> nothing to hard-block
    assert result is None


# ---------------------------------------------------------------------------
# L3: satisfaction auto-retry (with cap + HARD_BLOCK)
# ---------------------------------------------------------------------------

def _verdict(sat, hook=4, supp=3):
    return {"爽点": sat, "钩子": hook, "压抑": supp}


def test_expand_retry_until_satisfy_climax():
    """爽点 2 < floor 4 on a climax chapter -> retry; on reaching floor -> PASS.
    Beyond the cap (3) -> HARD_BLOCK (no infinite loop)."""
    floor = {"爽点": 4}
    cap = 3
    # rounds 1-3: still sat=2 < 4 -> retry each
    for r in (1, 2, 3):
        d = satisfaction_retry_decision(_verdict(2), floor, arc_violation=True, round_no=r, cap=cap)
        assert d["action"] == "retry_satisfaction"
    # round 4 (after cap): sat still low -> HARD_BLOCK
    d4 = satisfaction_retry_decision(_verdict(2), floor, arc_violation=True, round_no=4, cap=cap)
    assert d4["action"] == "hard_block"
    assert d4["reason"].startswith("satisfaction")

    # reaching the floor -> no retry, PASS
    dpass = satisfaction_retry_decision(_verdict(4), floor, arc_violation=False, round_no=1, cap=cap)
    assert dpass["action"] == "pass"


def test_should_retry_satisfaction_only_when_below_floor_or_arc_violation():
    """Retry fires only when 爽点 < floor OR arc climax violated; below the cap."""
    floor = {"爽点": 4}
    cap = 3
    assert should_retry_satisfaction(_verdict(3), floor, arc_violation=False, round_no=1, cap=cap) is True
    assert should_retry_satisfaction(_verdict(4), floor, arc_violation=False, round_no=1, cap=cap) is False
    # arc violation alone triggers retry even at floor
    assert should_retry_satisfaction(_verdict(4), floor, arc_violation=True, round_no=1, cap=cap) is True


# ---------------------------------------------------------------------------
# L4: volume satisfaction ratio + drift
# ---------------------------------------------------------------------------

def test_satisfaction_ratio_metric():
    """Volume ratio = chapters with 爽点 >= floor / all scored chapters."""
    # 10 scored chapters; 6 reach floor 4, 4 do not -> ratio 0.6
    verdicts = [_verdict(4) for _ in range(6)] + [_verdict(2) for _ in range(4)]
    ratio = volume_satisfaction_ratio(verdicts, floor_satisfaction=4)
    assert abs(ratio - 0.6) < 1e-9

    # no scored chapters -> None (denominator 0)
    assert volume_satisfaction_ratio([], floor_satisfaction=4) is None


def test_drift_suspect_when_ratio_drops_below_target():
    """Ratio below the 60% target -> drift_suspect True; at/above -> False."""
    verdicts_low = [_verdict(2)] * 10            # 0.0 < 0.6
    verdicts_ok = [_verdict(4)] * 10             # 1.0 >= 0.6
    assert drift_suspect(verdicts_low, floor_satisfaction=4, target=0.6) is True
    assert drift_suspect(verdicts_ok, floor_satisfaction=4, target=0.6) is False
    # all verdicts None (no scores yet) -> not drift (no signal)
    assert drift_suspect([None, None], floor_satisfaction=4, target=0.6) is False


def _arc_full():
    """arc with climax_at [5,10] + a suppression_release window ch7-9."""
    return {"climax_at": {"chapters": [5, 10], "min": 4},
            "suppression_release": {"chapter": 7, "min_压抑": 4, "release_by_chapter": 9}}


def test_suppression_exempt_chapter_gets_lower_floor():
    """A chapter inside the suppression_release window (ch8) is exempt from the
    climax hard bar -> its floor drops to NON_CLIMAX_SAT_FLOOR."""
    arc = _arc_full()
    assert is_suppression_exempt(8, arc) is True
    assert is_suppression_exempt(5, arc) is False  # a climax target, not suppression
    assert satisfaction_floor_for(8, arc, floor_satisfaction=4) == 2  # lower floor
    # climax chapter keeps the high floor
    assert satisfaction_floor_for(5, arc, floor_satisfaction=4) == 4
    # a plain non-climax, non-suppression chapter gets the lower floor too
    assert satisfaction_floor_for(3, arc, floor_satisfaction=4) == 2


def test_volume_ratio_dual_uses_per_chapter_floor():
    """Dual-target ratio: a climax ch judged at 4, non-climax judged at 2.
    ch5(爽4) reaches its 4 -> count; ch8(爽1, suppression floor 2) misses -> not.
    ch3(爽2, non-climax floor 2) reaches -> count."""
    arc = _arc_full()
    verdicts = [
        {"chapter": 5, "爽点": 4, "钩子": 4, "压抑": 3},   # climax, reaches 4
        {"chapter": 8, "爽点": 1, "钩子": 2, "压抑": 4},   # suppression, floor 2 -> 1<2 miss
        {"chapter": 3, "爽点": 2, "钩子": 4, "压抑": 3},   # non-climax, floor 2 -> 2>=2 reach
    ]
    out = volume_satisfaction_ratio_dual(verdicts, arc, climax_floor=4, target=0.6)
    assert out["scored"] == 3
    assert out["reaching"] == 2            # ch5 + ch3 reach their floors
    assert abs(out["ratio"] - 2 / 3) < 1e-9
    # 2/3 = 0.667 >= 0.6 target -> NOT drift.
    assert out["drift_suspect"] is False
    # tighten: all three miss -> drift
    all_miss = [{"chapter": 5, "爽点": 0}, {"chapter": 3, "爽点": 0}, {"chapter": 8, "爽点": 0}]
    assert volume_satisfaction_ratio_dual(all_miss, arc, climax_floor=4)["drift_suspect"] is True
    # no scored chapters -> no signal
    empty = volume_satisfaction_ratio_dual([], arc, climax_floor=4)
    assert empty["ratio"] is None and empty["drift_suspect"] is False
