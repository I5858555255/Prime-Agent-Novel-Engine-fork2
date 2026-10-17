# -*- coding: utf-8 -*-
"""CC28/批D：发布分层 + 动态 pro writer + 评审灰带校准 零 LLM 单测。"""
from novel_engine.quality import publish_router as pr
from novel_engine.quality import dynamic_writer_upgrade as dw
from novel_engine.quality import gray_band as gb


# ---- 发布分层 ----
def test_hard_red_blocks_even_high_score():
    d = pr.decide_publish_tier(90.0, hard_gates_clean=False, chapter_num=1)
    assert d["tier"] == pr.TIER_HARD_RED and d["auto_submit"] is False


def test_85_green_review_free():
    d = pr.decide_publish_tier(85.0, True, chapter_num=3)
    assert d["tier"] == pr.TIER_REVIEW_FREE and d["auto_submit"] is True


def test_82_green_sample_audit_auto_submits():
    d = pr.decide_publish_tier(82.0, True, chapter_num=10)
    assert d["tier"] == pr.TIER_SAMPLE_AUDIT and d["auto_submit"] is True
    # 抽检命中与否只影响人验标记，不阻断发布
    assert d["needs_manual_audit"] in (True, False)


def test_below_82_goes_best_of_fix():
    d = pr.decide_publish_tier(81.5, True, chapter_num=11)
    assert d["tier"] == pr.TIER_BEST_OF_FIX and d["auto_submit"] is False


def test_sample_rate_within_expected_ratio_and_stable():
    # rate=1 全命中、rate=0 全不命中
    assert pr.decide_publish_tier(83.0, True, 5, sample_rate=1.0)["needs_manual_audit"] is True
    assert pr.decide_publish_tier(83.0, True, 5, sample_rate=0.0)["needs_manual_audit"] is False
    # 同章同参数结论稳定
    a = pr.decide_publish_tier(83.0, True, 77)
    b = pr.decide_publish_tier(83.0, True, 77)
    assert a["needs_manual_audit"] == b["needs_manual_audit"]
    # 默认 12.5% 在 100 章上比例落在 10%-15%
    hits = sum(pr.deterministic_sample_hit(n) for n in range(1, 1001)) / 1000.0
    assert 0.10 <= hits <= 0.15


# ---- 动态 pro writer ----
def test_consecutive_3_triggers_upgrade():
    d = dw.should_upgrade_writer(consecutive_below=3, pro_used_in_window=0)
    assert d["upgrade"] is True


def test_below_k_no_upgrade():
    assert dw.should_upgrade_writer(consecutive_below=2, pro_used_in_window=0)["upgrade"] is False


def test_budget_exceeded_no_upgrade():
    d = dw.should_upgrade_writer(consecutive_below=5, pro_used_in_window=2)
    assert d["upgrade"] is False and "budget" in d["reason"]


def test_single_chapter_pro_once():
    assert dw.should_upgrade_writer(consecutive_below=9, pro_used_in_window=0,
                                    pro_used_this_chapter=True)["upgrade"] is False


def test_pro_adoption_and_fallback():
    # 提升>=2 且>=82 采纳
    assert dw.adopt_pro_draft(84.0, 80.0)["adopt"] is True
    # 仍<82 回退
    assert dw.adopt_pro_draft(81.0, 79.0)["adopt"] is False
    # 增益不足 回退
    assert dw.adopt_pro_draft(83.0, 82.5)["adopt"] is False


def test_pro_budget_window_evicts_old():
    b = dw.ProWriterBudget(window_size=10, max_pro_per_window=2)
    b.record(1, True); b.record(2, True)
    assert b.can_upgrade(10, 3)["upgrade"] is False   # 窗口内已满2
    b.record(11, False)
    # ch1 滚出窗口后释放预算
    assert b.pro_used_in_window(11) == 1
    assert b.can_upgrade(11, 3)["upgrade"] is True


# ---- 灰带评审调度 ----
def test_high_green_skips_triple():
    assert gb.decide_review_count(86.0, True)["plan"] == gb.REVIEW_SINGLE_SKIP


def test_in_band_triple():
    d = gb.decide_review_count(82.0, True)
    assert d["plan"] == gb.REVIEW_TRIPLE and d["extra_calls"] == 2


def test_low_no_triple():
    assert gb.decide_review_count(75.0, True)["plan"] == gb.REVIEW_SINGLE_LOW


def test_high_but_not_green_still_triple():
    assert gb.decide_review_count(86.0, False)["plan"] == gb.REVIEW_TRIPLE


def test_junk_rate_forces_dual_in_band():
    assert gb.decide_review_count(82.0, True, force_dual=True)["plan"] == gb.REVIEW_DUAL


def test_recalibration_narrow_widen_dual():
    # 首评≈中位 -> 收窄
    st = gb.score_deviation_stats([(84.0, 83.6), (84.1, 84.0), (83.0, 83.4)])
    assert gb.recalibrate_band(st, 0.0)["band"] == gb.NARROW_BAND
    # 偏差大 P95>2 -> 扩回
    st2 = gb.score_deviation_stats([(84.0, 80.0), (84.0, 81.0), (84.0, 88.0)])
    assert gb.recalibrate_band(st2, 0.0)["band"] == gb.WIDE_BAND
    # 废票率>5% -> 强制双评
    assert gb.recalibrate_band(st, 0.10)["force_dual"] is True


def test_junk_vote_rate():
    assert gb.junk_vote_rate(100, 6) > 0.05
    assert gb.junk_vote_rate(100, 3) < 0.05
