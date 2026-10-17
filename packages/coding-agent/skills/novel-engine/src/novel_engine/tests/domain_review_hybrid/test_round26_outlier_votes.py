# -*- coding: utf-8 -*-
"""CC round-26：单离群崩票 gap 识别 + 补票收敛 + 算术废票 + 矛盾特征（纯确定性单测）。"""
from novel_engine.quality import review_votes as rv
from novel_engine.quality import review_hybrid as rh


# ---------- 1. classify_vote_pattern：r31 真实案例 + 对称/分裂/一致 ----------

def test_pattern_r31_real_case_single_outlier_low():
    pat = rv.classify_vote_pattern([76.3, 58.3, 75.1])
    assert pat == rv.PATTERN_SINGLE_OUTLIER_LOW
    outlier, cluster = rv.outlier_and_cluster([76.3, 58.3, 75.1])
    assert abs(outlier - 58.3) < 1e-9
    assert sorted(round(c, 1) for c in cluster) == [75.1, 76.3]


def test_pattern_true_split():
    # 两个相邻 gap 都严格 >15（CC 用严格 >）
    assert rv.classify_vote_pattern([60.0, 76.0, 92.0]) == rv.PATTERN_TRUE_SPLIT
    assert rv.outlier_and_cluster([60.0, 76.0, 92.0]) == (None, [])
    # gap 恰好=15 不构成 split（严格大于）
    assert rv.classify_vote_pattern([60.0, 75.0, 90.0]) == rv.PATTERN_CONSISTENT


def test_pattern_consistent():
    assert rv.classify_vote_pattern([80.0, 82.0, 83.0]) == rv.PATTERN_CONSISTENT
    # 合理波动（两 gap 均 <=15 且不构成单离群紧簇）
    assert rv.classify_vote_pattern([70.0, 78.0, 84.0]) == rv.PATTERN_CONSISTENT


def test_pattern_single_outlier_high_symmetric():
    assert rv.classify_vote_pattern([70.0, 71.0, 95.0]) == rv.PATTERN_SINGLE_OUTLIER_HIGH
    outlier, cluster = rv.outlier_and_cluster([70.0, 71.0, 95.0])
    assert abs(outlier - 95.0) < 1e-9 and sorted(cluster) == [70.0, 71.0]


def test_aggregate_carries_pattern_and_r31_still_flags_range():
    agg = rv.aggregate([58.3, 75.1, 76.3])
    assert abs(agg["median"] - 75.1) < 1e-9
    assert agg["range"] == 18.0 and agg["highly_unstable"] is True
    assert agg["pattern"] == rv.PATTERN_SINGLE_OUTLIER_LOW
    # 非三票不做离群判定
    assert rv.classify_vote_pattern([80.0, 90.0]) == rv.PATTERN_CONSISTENT


# ---------- 2. 补票收敛 / 不收敛 ----------

def test_supplementary_convergence_branches():
    cluster = [75.1, 76.3]  # median 75.7
    # 落入紧簇 ±8 -> 收敛（确认崩票）
    assert rv.supplementary_converges(cluster, 80.0) is True
    assert rv.supplementary_converges(cluster, 75.7) is True
    # 边界 75.7-8=67.7
    assert rv.supplementary_converges(cluster, 67.7) is True
    # 支持离群端 -> 不收敛
    assert rv.supplementary_converges(cluster, 59.0) is False
    assert rv.supplementary_converges(cluster, 90.0) is False


def test_converged_three_votes_median_is_cluster_consensus():
    # 丢弃 58.3 崩票，紧簇[75.1,76.3] + 收敛新票 77.0 -> 共识中位 76.3
    final = [75.1, 76.3, 77.0]
    med = rv.aggregate(final)["median"]
    assert abs(med - 76.3) < 1e-9
    # 关键原则：共识中位即便 <85 也【不再 unstable】，应正常进 E-loop，而非 gap
    assert rv.aggregate(final)["highly_unstable"] is False


# ---------- 2b. CC round-27：缓坡形态补票收敛判定（consistent + 极差>15）----------

def test_slope_pattern_is_consistent_with_big_range():
    # 49.2/62.5/69.7：两 gap 均<=15 -> consistent，但 range=20.5>15 -> highly_unstable
    agg = rv.aggregate([49.2, 62.5, 69.7])
    assert agg["pattern"] == rv.PATTERN_CONSISTENT
    assert agg["range"] > 15 and agg["highly_unstable"] is True
    assert agg["outlier"] is None and agg["cluster"] == []


def test_slope_supplementary_converges_when_midpair_tight():
    # 缓坡补票：补票落入共识区 -> 4 票中间两票差<=8 -> 收敛（清 unstable）
    assert rv.slope_median_converges([49.2, 62.5, 65.0, 69.7]) is True
    # 补票落在高簇附近（62.5/69.7 本就近）-> 收敛
    assert rv.slope_median_converges([49.2, 62.5, 69.7, 100.0]) is True
    # 边界：中间两票差恰好=8 -> 收敛
    assert rv.slope_median_converges([49.2, 57.0, 65.0, 69.7]) is True


def test_slope_supplementary_not_converged_when_midpair_wide():
    # 补票落在低端 -> 中间两票仍宽（62.5-49.2=13.3>8）-> 不收敛（多数票仍分散）
    assert rv.slope_median_converges([49.2, 62.5, 69.7, 20.0]) is False
    assert rv.slope_median_converges([49.2, 62.5, 69.7, 40.0]) is False
    # 不足 4 票 -> 不收敛（补票未发生）
    assert rv.slope_median_converges([49.2, 62.5, 69.7]) is False


def test_slope_supplementary_equiv_routing():
    # 编排层等价逻辑：缓坡补票收敛 -> 清 unstable（即使新中位<软线也进 E-loop）；
    # 不收敛 -> 维持 unstable 交人工复核。
    def route(four_votes, soft5):
        if rv.slope_median_converges(four_votes):
            return "cleared"
        return "keep_unstable"
    assert route([49.2, 62.5, 65.0, 69.7], 85) == "cleared"
    assert route([49.2, 62.5, 69.7, 40.0], 85) == "keep_unstable"


# ---------- 4. dim_sum_mismatch 算术自相矛盾直接丢弃 ----------

def _review_with_scores(scores, raw_total):
    review = {"scores": dict(scores), "raw_total": raw_total}
    return rh.annotate_raw_total(review)


def test_dim_sum_mismatch_detect_and_drop():
    full = {"plot_consistency": 20, "character_consistency": 15,
            "foreshadow_execution": 15, "style_match": 10, "pacing": 8,
            "innovation": 8, "hook_strength": 6, "reader_retention": 5,
            "cliffhensity": 4}
    s_total = sum(full.values())  # 91
    bad = _review_with_scores(full, s_total + 10)   # 自报总分 101，与维度和不符
    assert rh.dim_sum_mismatch(bad) is True
    good = _review_with_scores(full, s_total)
    assert rh.dim_sum_mismatch(good) is False
    # filter_valid_votes 必须丢弃算术矛盾票，保留低分但自洽票
    pairs = [(bad, 84.0), (good, float(round(s_total / 120 * 100, 1)))]
    kept = rh.filter_valid_votes(pairs, hard_gates_all_clean=True)
    assert len(kept) == 1 and kept[0][0] is good
    # 缺少快照 fail-safe：不判矛盾，绝不误删
    assert rh.dim_sum_mismatch({"raw_total": 50}) is False


# ---------- 5. 三类矛盾特征：只降权，不单独丢票 ----------

def test_plot_foreshadow_severely_low_only_when_hard_gates_clean():
    # plot=8/25=0.32 <0.4；硬门全 clean 才触发
    rv_low = _review_with_scores(
        {"plot_consistency": 8, "character_consistency": 15,
         "foreshadow_execution": 15, "style_match": 10, "pacing": 8,
         "innovation": 8, "hook_strength": 6, "reader_retention": 5,
         "cliffhensity": 4}, 79)
    sig = {"hard_gates_all_clean": True}
    assert rh.vote_anomaly_features(rv_low, sig)["plot_foreshadow_severely_low"] is True
    # 硬门未全 clean：不把客观核验维低分当矛盾
    assert rh.vote_anomaly_features(
        rv_low, {"hard_gates_all_clean": False})["plot_foreshadow_severely_low"] is False


def test_hook_low_despite_markers_is_auxiliary_only():
    # hook=1/8=0.125 <0.15，章末含问号悬念形式
    r = _review_with_scores(
        {"plot_consistency": 20, "character_consistency": 15,
         "foreshadow_execution": 15, "style_match": 10, "pacing": 8,
         "innovation": 8, "hook_strength": 1, "reader_retention": 5,
         "cliffhensity": 4}, 86)
    ending = "他猛地回头，黑暗里那东西，究竟是什么？"
    feats = rh.vote_anomaly_features(r, {"hard_gates_all_clean": True}, ending_text=ending)
    assert feats["hook_severely_low_despite_markers"] is True
    # 无形式标记则不触发（形式不在、不能反推评审错）
    assert rh.vote_anomaly_features(
        r, {"hard_gates_all_clean": True}, ending_text="天色渐渐亮了。")["hook_severely_low_despite_markers"] is False
    # 特征绝不构成丢票依据：is_valid_vote 仍为真
    assert rh.is_valid_vote(r) is True


def test_internally_inconsistent_feature():
    # 多个维度近满分、四个维度极低 -> 得分率 pstdev>0.35 且 min<0.3
    r = _review_with_scores(
        {"plot_consistency": 24, "character_consistency": 19,
         "foreshadow_execution": 19, "style_match": 14, "pacing": 1,
         "innovation": 9, "hook_strength": 1, "reader_retention": 1,
         "cliffhensity": 1}, 89)
    feats = rh.vote_anomaly_features(r, {"hard_gates_all_clean": True})
    assert feats["internally_inconsistent"] is True
    # 普遍平稳的正常严评（都在合理区间）不应误报
    r2 = _review_with_scores(
        {"plot_consistency": 18, "character_consistency": 14,
         "foreshadow_execution": 14, "style_match": 11, "pacing": 7,
         "innovation": 7, "hook_strength": 6, "reader_retention": 5,
         "cliffhensity": 4}, 86)
    assert rh.vote_anomaly_features(
        r2, {"hard_gates_all_clean": True})["internally_inconsistent"] is False


# ---------- 7. 调用硬上限常量 ----------

def test_review_call_cap_constant():
    # 首评1+三评2+单离群补票1+两轮修复后复评2 = 6
    assert rv.REVIEW_CALL_CAP == 6
    assert rv.TIGHT_CLUSTER_MAX == 7.0
    assert rv.OUTLIER_MIN == 15.0
    assert rv.CONVERGENCE_TOLERANCE == 8.0


# ---------- 8. reviewer 顶层 list 容错（r33 真机 'list' object has no attribute 'get'）----------

def test_reviewer_coerce_wrapped_list_and_bare_list():
    from novel_engine.agents.reviewer_agent import _coerce_review_object, _normalize_review
    inner = {"scores": {"plot_consistency": 20}, "issues": []}
    # flash 把评审对象包进 list -> 捞出该 dict，normalize 后是有效票（raw>0）
    got = _normalize_review(_coerce_review_object([inner]))
    assert got["raw_total"] == 20 and rh.is_valid_vote(got)
    # 裸 list（无 dict）-> {} -> raw=0 技术废票，被 filter 剔除而非抛异常
    empty = _normalize_review(_coerce_review_object(["一条裸字符串", "又一条"]))
    assert not rh.is_valid_vote(empty)
    pairs = [(empty, 0.0)]
    assert rh.filter_valid_votes(pairs, hard_gates_all_clean=True) == []
    # 原样 dict 不受影响
    assert _coerce_review_object(inner) is inner
    # issues 内混入非 dict 不应崩 normalize
    mixed = _normalize_review(_coerce_review_object(
        {"scores": {"plot_consistency": 10}, "issues": ["裸字符串问题", {"dimension": "pacing"}]}))
    assert mixed["raw_total"] == 10


# ---------- 6. 门修复后最终全绿 -> 免强制三评（按最终状态而非历史轨迹）----------

def test_triple_exemption_based_on_final_signals():
    # 等价编排层 `force_triple = gate_fired and not final_all_green`：
    # 门曾经触发，但修复后最终确定性信号全绿 -> 不强制三评（视同未触发）。
    def force_triple(gate_fired, final_all_green, first_in_band):
        if first_in_band:           # 灰带[85,90]仍三评
            return True
        return bool(gate_fired and not final_all_green)
    assert force_triple(gate_fired=True, final_all_green=True, first_in_band=False) is False
    assert force_triple(gate_fired=True, final_all_green=False, first_in_band=False) is True
    assert force_triple(gate_fired=False, final_all_green=True, first_in_band=False) is False
    assert force_triple(gate_fired=True, final_all_green=True, first_in_band=True) is True
