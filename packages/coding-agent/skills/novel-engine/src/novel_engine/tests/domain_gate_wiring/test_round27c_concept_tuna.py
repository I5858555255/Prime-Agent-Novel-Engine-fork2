# -*- coding: utf-8 -*-
"""CC round-27c: concept_unlocks first-criterion for tuna terms.

Before this round, 吐纳/调息 release depended on the hard-coded
_TUNA_EXEMPT_CHAPTERS={6,7,8,9} plus night-anchor/character checks. Now the
concept layer decides first: cultivation_system is unlocked at ch6 and
applies_to_character=陈老根, so "陈老根吐纳" without a night anchor is legal
from ch6 onward, while baby-side 吐纳 stays hard and ch1-5 stays locked.
"""
import pathlib

from novel_engine.quality.scope_gate import detect_scope_violations, reset_concept_cache

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _check(text, chapter, expect_hard, label):
    reset_concept_cache()
    r = detect_scope_violations(text, chapter, {}, ROOT)
    got = bool(r["hard"])
    assert got == expect_hard, (
        f"{label}: expected hard={expect_hard}, got hard={got} "
        f"({[(h['term'], h['sentence'][:20]) for h in r['hard']]})"
    )


def test_tuna_concept_unlock_from_ch6_without_night_anchor():
    # 陈老根吐纳、无夜锚 —— ch6 起概念解锁 → 放行（替代写死章节集）
    _check("陈老根在草铺上吐纳，气息平稳。", 6, False, "clg-tuna-ch6-no-anchor")


def test_tuna_baby_side_stays_hard():
    # 婴儿施为吐纳 —— 概念 applies_to=陈老根 不匹配 → 仍 hard
    _check("婴儿竟会吐纳，鼻孔一开一合。", 6, True, "baby-tuna-ch6")


def test_tuna_locked_before_unlock_chapter():
    # ch5 < unlock 6 → 未解锁 → hard
    _check("陈老根在草铺上吐纳，气息平稳。", 5, True, "clg-tuna-ch5")


def test_tuna_diaoxi_unlock_ch7():
    # 调息同组概念，ch7 放行
    _check("陈老根盘膝调息，双目微闭。", 7, False, "clg-diaoxi-ch7")


def test_tuna_night_anchor_legacy_still_works():
    # 原有夜锚豁免路径不受影响
    _check("深夜，陈老根独自吐纳。", 6, False, "night-anchor-ch6")
