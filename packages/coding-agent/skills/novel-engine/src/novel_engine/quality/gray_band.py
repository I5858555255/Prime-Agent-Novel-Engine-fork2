# -*- coding: utf-8 -*-
"""CC28/批D（DS 裁决第6点）：评审次数灰带调度 + 10-15 章校准（纯规则，零 LLM）。

目标：把评审 LLM 调用从固定三评降到 1-2，修复轮<=2。
- 首评 >= 灰带上沿(85.5) 且确定性硬门绿：单评+确定性，免第三评。
- 首评 <= 灰带下沿(81.5)：单评+确定性，不三评，直接修复/难章队列。
- 落入 (81.5, 85.5)：进三评取中位（近阈值敏感区）。

校准（每 10-15 章）：记录首评 vs 三评中位，算 MAE / P95 / 偏差方向 / 废票率：
- |中位偏差|<=1.2 且 P95<=2.0 -> 收窄到 [82.5, 84.5]；
- P95>2.0 -> 扩回 [80, 86]；
- 废票率>5% -> 恢复双评（不再单评免第三）。
本模块只产出决策/统计，供 orchestrator 真机评审回路 real-only 采用，不改动 mock 旧行为。
"""
from __future__ import annotations

INITIAL_BAND = (81.5, 85.5)
NARROW_BAND = (82.5, 84.5)
WIDE_BAND = (80.0, 86.0)

NARROW_MAE_MAX = 1.2
NARROW_P95_MAX = 2.0
WIDE_P95_MIN = 2.0
JUNK_RATE_MAX = 0.05

REVIEW_SINGLE_SKIP = "single_high_green"   # 高分硬门绿：单评免三
REVIEW_SINGLE_LOW = "single_low_fix"       # 低分：单评直接修复
REVIEW_TRIPLE = "triple_band"              # 灰带：三评取中位
REVIEW_DUAL = "dual"                        # 废票率高时恢复双评


def decide_review_count(first_score, hard_gates_clean: bool,
                        band: tuple[float, float] = INITIAL_BAND,
                        force_dual: bool = False) -> dict:
    """根据首评与硬门决定评审次数策略。force_dual=True（废票率高）时灰带走双评。"""
    try:
        s = float(first_score)
    except (TypeError, ValueError):
        return {"plan": REVIEW_TRIPLE, "extra_calls": 2, "reason": "score_unparseable"}
    lo, hi = float(band[0]), float(band[1])
    if s >= hi and bool(hard_gates_clean):
        return {"plan": REVIEW_SINGLE_SKIP, "extra_calls": 0, "reason": "high_green_skip"}
    if s <= lo:
        return {"plan": REVIEW_SINGLE_LOW, "extra_calls": 0, "reason": "low_go_fix"}
    if force_dual:
        return {"plan": REVIEW_DUAL, "extra_calls": 1, "reason": "high_junk_rate_dual"}
    return {"plan": REVIEW_TRIPLE, "extra_calls": 2, "reason": "gray_band_triple"}


def _percentile(abs_devs: list[float], q: float) -> float:
    if not abs_devs:
        return 0.0
    xs = sorted(abs_devs)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * min(max(q, 0.0), 1.0)
    lo_i = int(pos)
    frac = pos - lo_i
    hi_i = min(lo_i + 1, len(xs) - 1)
    return xs[lo_i] + (xs[hi_i] - xs[lo_i]) * frac


def score_deviation_stats(samples: list[tuple[float, float]]) -> dict:
    """samples=[(首评, 三评中位), ...]，返回 mae/p95/bias(首评-中位的均值,有符号)/n。"""
    devs = []
    bias_sum = 0.0
    for a, b in samples or []:
        d = float(a) - float(b)
        devs.append(abs(d))
        bias_sum += d
    n = len(devs)
    if n == 0:
        return {"mae": 0.0, "p95": 0.0, "bias": 0.0, "n": 0}
    return {"mae": sum(devs) / n, "p95": _percentile(devs, 0.95),
            "bias": bias_sum / n, "n": n}


def junk_vote_rate(total_votes: int, junk_votes: int) -> float:
    if int(total_votes) <= 0:
        return 0.0
    return min(max(int(junk_votes) / int(total_votes), 0.0), 1.0)


def recalibrate_band(stats: dict, junk_rate: float,
                     current_band: tuple[float, float] = INITIAL_BAND) -> dict:
    """按 MAE/P95/废票率决定下一周期灰带与是否强制双评。"""
    if int(stats.get("n", 0)) <= 0:
        return {"band": current_band, "force_dual": False, "reason": "insufficient_samples"}
    if float(junk_rate) > JUNK_RATE_MAX:
        return {"band": current_band, "force_dual": True, "reason": "junk_rate_high_restore_dual"}
    mae, p95 = float(stats.get("mae", 9.9)), float(stats.get("p95", 9.9))
    if p95 > WIDE_P95_MIN:
        return {"band": WIDE_BAND, "force_dual": False, "reason": "p95_high_widen"}
    if mae <= NARROW_MAE_MAX and p95 <= NARROW_P95_MAX:
        return {"band": NARROW_BAND, "force_dual": False, "reason": "accurate_narrow"}
    return {"band": current_band, "force_dual": False, "reason": "keep_band"}
