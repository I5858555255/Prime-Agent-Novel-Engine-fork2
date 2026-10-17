# -*- coding: utf-8 -*-
"""CC round-8 P0-3：评审三评取中位数（纯函数，可离线单测）。

触发：归一化总分落入近阈值敏感区间 [85, 90]，或同稿已有多次评分且极差 > 10。
取值：中位数（对单次异常高/低分比均值更鲁棒）。
极不稳定：三评极差 > 15 → 标记 highly_unstable，无论中位数是否过线都强制人工复核。
"""
from __future__ import annotations

BAND_LOW: float = 85.0
BAND_HIGH: float = 90.0
SWING_TRIGGER: float = 10.0
UNSTABLE_RANGE: float = 15.0

# CC round-26 P0：单离群崩票识别（gap 分析法）。
# 三票排序 x0≤x1≤x2，相邻 gap g1=x1-x0、g2=x2-x1：
#   一个 gap>OUTLIER_MIN 且另一个 gap<=TIGHT_CLUSTER_MAX → 单张离群票 + 两票紧簇；
#   两个 gap 都>OUTLIER_MIN → 真三分布分裂（维持人工/gap）。
# CC33：紧簇阈值由 5.0 放宽至 7.0（9 维 120 分体系下两票差 5-7 分=每维不足 1 分，
#   属正常波动；agnès-3.0-flash temperature=0 实测 ch151 三票 [31.7, 59.1, 64.7]
#   紧簇差 5.6 恰被旧阈值误判为 consistent，导致单离群补票机制未触发）。
TIGHT_CLUSTER_MAX: float = 7.0
OUTLIER_MIN: float = 15.0
# 单离群时补 1 票，新票与紧簇中位数差距<=此值 → 确认离群票为崩票、收敛。
CONVERGENCE_TOLERANCE: float = 8.0
# CC round-26：单章评审 LLM 调用总数硬上限（首评1+三评2+单离群补票1+两轮修复后复评2=6）。
REVIEW_CALL_CAP: int = 6

PATTERN_CONSISTENT = "consistent"
PATTERN_SINGLE_OUTLIER_LOW = "single_outlier_low"
PATTERN_SINGLE_OUTLIER_HIGH = "single_outlier_high"
PATTERN_TRUE_SPLIT = "true_split"


def median(values: list[float]) -> float:
    xs = sorted(float(v) for v in values)
    n = len(xs)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return xs[mid]
    return (xs[mid - 1] + xs[mid]) / 2.0


def classify_vote_pattern(votes: list[float],
                          tight_max: float = TIGHT_CLUSTER_MAX,
                          outlier_min: float = OUTLIER_MIN) -> str:
    """三票 gap 形态分类（纯函数）。

    - single_outlier_low：一张异常低崩票 + 两票紧簇（如 58.3/75.1/76.3）。
    - single_outlier_high：一张异常高拱票 + 两票紧簇（如 70/71/95），与低离群对称处理。
    - true_split：两个相邻 gap 都大，真三分布分裂。
    - consistent：其余（正常一致/合理波动）。
    票数不为 3 时不做离群判定，返回 consistent（交由既有极差/多数逻辑处理）。
    """
    xs = sorted(float(v) for v in (votes or []))
    if len(xs) != 3:
        return PATTERN_CONSISTENT
    x0, x1, x2 = xs
    g1, g2 = x1 - x0, x2 - x1
    if g1 > outlier_min and g2 <= tight_max:
        return PATTERN_SINGLE_OUTLIER_LOW
    if g2 > outlier_min and g1 <= tight_max:
        return PATTERN_SINGLE_OUTLIER_HIGH
    if g1 > outlier_min and g2 > outlier_min:
        return PATTERN_TRUE_SPLIT
    return PATTERN_CONSISTENT


def outlier_and_cluster(votes: list[float]) -> tuple[float, list[float]]:
    """返回 (离群票, 紧簇两票)；非单离群形态返回 (None, [])。"""
    xs = sorted(float(v) for v in (votes or []))
    pat = classify_vote_pattern(xs)
    if pat == PATTERN_SINGLE_OUTLIER_LOW:
        return xs[0], [xs[1], xs[2]]
    if pat == PATTERN_SINGLE_OUTLIER_HIGH:
        return xs[2], [xs[0], xs[1]]
    return None, []


def supplementary_converges(cluster_votes: list[float], supplementary: float,
                            tol: float = CONVERGENCE_TOLERANCE) -> bool:
    """补票是否落入紧簇附近（与紧簇中位数差距<=tol）→ 确认离群票为崩票。"""
    if not cluster_votes:
        return False
    return abs(float(supplementary) - median(cluster_votes)) <= float(tol)


def slope_median_converges(four_votes: list[float],
                           tol: float = CONVERGENCE_TOLERANCE) -> bool:
    """缓坡补票收敛判定（CC round-27，纯函数）。

    缓坡形态 = 三票无单离群/无真分裂（classify==consistent）但极差>15（如 49.2/62.5/69.7）。
    补第 4 票后取排序后中间两票（去掉最高最低）的差 <= tol → 共识成形、评审可信，
    清 highly_unstable 走正常流程（内容不达标≠评审不可信）；
    中间两票仍宽（>tol）→ 多数票仍分散，维持 unstable 交人工复核。
    """
    xs = sorted(float(v) for v in (four_votes or []))
    if len(xs) < 4:
        return False
    return abs(xs[2] - xs[1]) <= float(tol)


def should_run_extra_reviews(existing_scores: list[float],
                             band_low: float = BAND_LOW,
                             band_high: float = BAND_HIGH,
                             swing_trigger: float = SWING_TRIGGER) -> bool:
    """已有至少一次评分后，是否需要再补两次凑成三评。"""
    scores = [float(v) for v in (existing_scores or [])]
    if not scores:
        return False
    first = scores[0]
    if band_low <= first <= band_high:
        return True
    if len(scores) >= 2 and (max(scores) - min(scores)) > swing_trigger:
        return True
    return False


def aggregate(scores: list[float], unstable_range: float = UNSTABLE_RANGE) -> dict:
    """三评聚合：中位数 + 极差不稳定判定 + CC26 gap 形态/紧簇/离群信息。"""
    xs = [float(v) for v in (scores or [])]
    if not xs:
        return {"median": 0.0, "min": 0.0, "max": 0.0, "range": 0.0,
                "highly_unstable": False, "pattern": PATTERN_CONSISTENT,
                "outlier": None, "cluster": [], "scores": []}
    rng = max(xs) - min(xs)
    pattern = classify_vote_pattern(xs)
    outlier, cluster = outlier_and_cluster(xs)
    return {
        "median": median(xs),
        "min": min(xs),
        "max": max(xs),
        "range": rng,
        "highly_unstable": rng > unstable_range,
        "pattern": pattern,
        "outlier": outlier,
        "cluster": cluster,
        "scores": xs,
    }
