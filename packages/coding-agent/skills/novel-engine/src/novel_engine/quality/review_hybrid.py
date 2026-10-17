# -*- coding: utf-8 -*-
"""CC round-24 P0-1：flash 评审高方差去噪（纯确定性，零额外 LLM 判定）。

真机 r25/r27 暴露：同一章已过全部确定性门，agnes-2.5-flash 三票仍剧烈摆动
（72.5/77.5/100，偶发 raw score=0 解析失败）。本模块把“可由确定性指标代理”的两个
主观维（pacing 节奏 / reader_retention 留存）改成 0.6 确定性 + 0.4 LLM 的混合分，
其余 7 维维持纯 LLM（仅注入确定性锚点做 prompt 校准，见 reviewer_agent）。

设计约束（不得违反）：
- 9 维 120 分体系与 88/85 线完全不变，只改 pacing(10)/reader_retention(7) 两维的
  “分数来源”，因此混合分结果仍可直接与 88 线比较，无需重校阈值。
- 确定性分量只用 round-23/24 已落地且零 LLM 的门产物（任务卡推进契约覆盖、单场氛围
  占比、相邻场氛围共享度、硬门是否 clean）。
- 单票异常剔除只针对“技术性错误票”（raw_total<=0 解析失败、raw_total>120 越界、
  硬门已证伪却声称存在该硬门问题——仅限客观可字符判定的拉丁/英文泄漏），
  绝不允许因为“这票分数低”就剔除（那是伪达标）。
"""
from __future__ import annotations

import statistics

from . import scene_progression_gate as spg
from . import density_gate

# 混合权重：pacing / retention 两维确定性主导
DET_WEIGHT = 0.6
LLM_WEIGHT = 0.4

# reviewer 维度键 -> 满分（与 reviewer_agent.DIM_MAX 一致，这里独立常量避免循环导入）
PACING_KEY = "pacing"
RETENTION_KEY = "reader_retention"
PACING_MAX = 10.0
RETENTION_MAX = 7.0

# 疑似评审偏严时，确定性信号“全绿”的门槛
BEAT_COVERAGE_GREEN = 0.70
SCENE_ATMOSPHERE_GREEN = 0.55

# 硬门已 clean、但某票 issues 仍“声称存在拉丁/英文泄漏”时，判为评审幻觉票。
# 仅列客观字符门能确证的措辞，严禁扩到主观差评（防止把低分票当幻觉剔除）。
_HARD_GATE_CONTRADICTION_TERMS = (
    "英文单词", "英文字母", "英文残片", "英语单词", "拉丁字符", "拉丁字母",
)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def build_deterministic_signals(task_card: dict, scenes: list, root,
                                arc: str | None = None,
                                hard_gates_clean: bool = True) -> dict:
    """零 LLM 汇总本章确定性信号。

    scenes: [{scene_id, scene_text}, ...]（authoritative scenes 即可）。
    beat_coverage_ratio：显式推进契约场景中“新状态已落地”的占比（无显式契约时不惩罚，
    返回 1.0，由 reviewer 锚点另行体现）。
    """
    cfg = spg.load_progression_config(root)
    root_s = str(root) if root is not None else ""
    syn = density_gate.load_synonym_map(root_s, arc, {}) if root_s else {}

    bps = (task_card or {}).get("scene_blueprints", []) or []
    bp_by_id = {int(b.get("scene_num", 0) or 0): b for b in bps if isinstance(b, dict)}

    ordered = []
    for sc in scenes or []:
        sid = int(sc.get("scene_id", 0) or 0)
        txt = str(sc.get("scene_text", "") or "")
        ordered.append((sid, txt))
    ordered.sort(key=lambda x: x[0])

    per_scene_cov: dict[int, bool] = {}
    atmosphere: dict[str, float] = {}
    adj_ratios: list[float] = []
    explicit_total = 0
    explicit_covered = 0
    for sid, txt in ordered:
        bp = bp_by_id.get(sid, {})
        ev = spg.evaluate_scene_progression(bp, txt, cfg, root=root_s, arc=arc)
        covered = not (ev["explicit_contract"] and ev["no_new_state"])
        per_scene_cov[sid] = covered
        atmosphere[f"s{sid}"] = float(ev["atmosphere"]["ratio"])
        if ev["explicit_contract"]:
            explicit_total += 1
            explicit_covered += 1 if covered else 0

    for (_s1, t1), (_s2, t2) in zip(ordered, ordered[1:]):
        adj_ratios.append(float(spg.adjacent_atmosphere_overlap(t1, t2, cfg)["ratio"]))

    beat_cov = (explicit_covered / explicit_total) if explicit_total else 1.0
    mean_atm = (sum(atmosphere.values()) / len(atmosphere)) if atmosphere else 0.0
    mean_adj = (sum(adj_ratios) / len(adj_ratios)) if adj_ratios else 0.0

    return {
        "beat_coverage_ratio": round(beat_cov, 3),
        "new_state_coverage": {f"s{sid}": bool(ok) for sid, ok in per_scene_cov.items()},
        "atmosphere_ratio_per_scene": atmosphere,
        "mean_atmosphere_ratio": round(mean_atm, 3),
        "adjacent_atmosphere_overlap_mean": round(mean_adj, 3),
        "hard_gates_all_clean": bool(hard_gates_clean),
    }


def pacing_deterministic(signals: dict) -> float:
    """pacing 确定性分量（0-10）：氛围越淡、beat 覆盖越高 → 节奏越好。"""
    mean_atm = float(signals.get("mean_atmosphere_ratio", 0.0) or 0.0)
    beat = float(signals.get("beat_coverage_ratio", 1.0) or 1.0)
    comp = ((1.0 - _clamp(mean_atm, 0.0, 1.0)) * 0.5
            + _clamp(beat, 0.0, 1.0) * 0.5)
    return round(comp * PACING_MAX, 3)


def retention_deterministic(signals: dict) -> float:
    """reader_retention 确定性分量（0-7）：相邻场意象越不重复 → 越留人。"""
    mean_adj = float(signals.get("adjacent_atmosphere_overlap_mean", 0.0) or 0.0)
    return round((1.0 - _clamp(mean_adj, 0.0, 1.0)) * RETENTION_MAX, 3)


def hybridize_review(review: dict, signals: dict) -> dict:
    """把一条 review 的 pacing/reader_retention 改成混合分，并就地重算总分/归一化分。

    其余 7 维保持 reviewer 原始 LLM 分。调用方应在【确认该票为有效票】后再调用。
    """
    scores = review.get("scores")
    if not isinstance(scores, dict):
        return review
    llm_pacing = float(scores.get(PACING_KEY, 0.0) or 0.0)
    llm_ret = float(scores.get(RETENTION_KEY, 0.0) or 0.0)
    hy_pacing = _clamp(
        pacing_deterministic(signals) * DET_WEIGHT + llm_pacing * LLM_WEIGHT,
        0.0, PACING_MAX)
    hy_ret = _clamp(
        retention_deterministic(signals) * DET_WEIGHT + llm_ret * LLM_WEIGHT,
        0.0, RETENTION_MAX)
    scores[PACING_KEY] = round(hy_pacing, 2)
    scores[RETENTION_KEY] = round(hy_ret, 2)

    total = 0.0
    for name, mx in (("plot_consistency", 25), ("character_consistency", 20),
                     ("foreshadow_execution", 20), ("style_match", 15),
                     ("pacing", 10), ("innovation", 10), ("hook_strength", 8),
                     ("reader_retention", 7), ("cliffhensity", 5)):
        total += _clamp(float(scores.get(name, 0.0) or 0.0), 0.0, float(mx))
    review["scores"] = scores
    review["total_score"] = int(round(total))
    review["normalized_score"] = round(total / 120.0 * 100.0, 1)
    review["hybrid_scored"] = True
    review["deterministic_components"] = {
        "pacing_det": pacing_deterministic(signals),
        "retention_det": retention_deterministic(signals),
    }
    return review


def llm_raw_total(review: dict):
    """该票在混合分之前的 LLM 原始总分（用于异常票判定）。取不到返回 None。"""
    v = review.get("_llm_raw_total")
    if v is None:
        v = review.get("raw_total")
    try:
        return int(round(float(v)))
    except (TypeError, ValueError):
        return None


def annotate_raw_total(review: dict) -> dict:
    """在混合分之前冻结 LLM 原始总分与原始维度分（_normalize_review 已写入 raw_total）。"""
    try:
        review["_llm_raw_total"] = int(round(float(review.get("raw_total", 0))))
    except (TypeError, ValueError):
        review["_llm_raw_total"] = 0
    # CC round-26：冻结混合分前的原始维度分，供维度分合计 vs raw_total 的算术自洽检测。
    _sc = review.get("scores")
    if isinstance(_sc, dict):
        review["_llm_dim_snapshot"] = {k: float(v or 0.0) for k, v in _sc.items()}
    return review


# CC round-26：维度满分（与 reviewer_agent.DIM_MAX 一致）。
_DIM_MAX26 = {"plot_consistency": 25.0, "character_consistency": 20.0,
              "foreshadow_execution": 20.0, "style_match": 15.0, "pacing": 10.0,
              "innovation": 10.0, "hook_strength": 8.0, "reader_retention": 7.0,
              "cliffhensity": 5.0}
# 章末悬念句常见的【形式】标记（只证明"有钩子形式"，不证明钩子写得好，故仅降权）。
_HOOK_MARKER_CHARS = ("？", "?", "……", "—", "「", "“", "\"", "！", "!")
_SEVERELY_LOW_RATIO = 0.40
_HOOK_SEVERELY_LOW_RATIO = 0.15
_INCONSISTENT_STD = 0.35
_INCONSISTENT_MIN = 0.30


def dim_sum_mismatch(review: dict, tol: float = 1.0) -> bool:
    """CC26 可直接丢弃特征：评审自报维度分之和与 raw_total 不符（算术自相矛盾）。

    必须在混合分【之前】用快照判定（混合分会改 pacing/retention 造成伪 mismatch）；
    缺少快照则 fail-safe 返回 False（宁可漏判，不可误删合法严评）。
    """
    snap = review.get("_llm_dim_snapshot")
    if not isinstance(snap, dict) or not snap:
        return False
    raw = llm_raw_total(review)
    if raw is None or raw <= 0:
        return False
    dim_sum = sum(float(v or 0.0) for v in snap.values())
    return abs(dim_sum - float(raw)) > float(tol)


def vote_anomaly_features(review: dict, signals: dict | None,
                          ending_text: str | None = None) -> dict:
    """CC26 三类【仅降权/辅助】矛盾特征，绝不单独丢票（防误杀合法严评）。

    这些特征只用于提高/降低对 gap 分析法所识别离群票的置信度，最终是否丢弃仍以
    "补 1 票是否收敛" 为唯一裁决（见 review_votes 单离群补票机制）。
    """
    feats = {
        "plot_foreshadow_severely_low": False,
        "hook_severely_low_despite_markers": False,
        "internally_inconsistent": False,
    }
    snap = review.get("_llm_dim_snapshot")
    if not isinstance(snap, dict) or not snap:
        # 没有混合分前快照时，用当前 scores 兜底（此时通常是未混合的纯 LLM 票）。
        sc = review.get("scores")
        snap = {k: float(v or 0.0) for k, v in sc.items()} if isinstance(sc, dict) else {}
    if not snap:
        return feats
    hg_clean = bool((signals or {}).get("hard_gates_all_clean", False))

    # 特征2：硬门全 clean，却在可客观核验的 plot/foreshadow 维打 <40%。
    if hg_clean:
        pr = float(snap.get("plot_consistency", 0.0) or 0.0) / _DIM_MAX26["plot_consistency"]
        fr = float(snap.get("foreshadow_execution", 0.0) or 0.0) / _DIM_MAX26["foreshadow_execution"]
        feats["plot_foreshadow_severely_low"] = (pr < _SEVERELY_LOW_RATIO
                                                 or fr < _SEVERELY_LOW_RATIO)

    # 特征3：章末句有悬念形式标记，hook 维却打 <15%（形式在、近乎零分的矛盾）。
    if ending_text:
        tail = str(ending_text)[-120:]
        has_marker = any(m in tail for m in _HOOK_MARKER_CHARS)
        if has_marker:
            hr = float(snap.get("hook_strength", 0.0) or 0.0) / _DIM_MAX26["hook_strength"]
            feats["hook_severely_low_despite_markers"] = hr < _HOOK_SEVERELY_LOW_RATIO

    # 特征4：维度得分率内部极不一致（std>0.35 且最低<0.3）。
    ratios = [float(snap.get(k, 0.0) or 0.0) / mx for k, mx in _DIM_MAX26.items()]
    if len(ratios) >= 2:
        try:
            feats["internally_inconsistent"] = (
                statistics.pstdev(ratios) > _INCONSISTENT_STD
                and min(ratios) < _INCONSISTENT_MIN)
        except statistics.StatisticsError:
            pass
    return feats


def is_valid_vote(review: dict, max_total: int = 120) -> bool:
    """技术性有效票：raw_total 可解析、>0、未越界。"""
    raw = llm_raw_total(review)
    if raw is None or raw <= 0 or raw > max_total:
        return False
    return True


def hard_gate_contradicted_vote(review: dict, hard_gates_all_clean: bool) -> bool:
    """硬门已全部 clean，而该票 issues 却声称存在字符级拉丁/英文泄漏 → 评审幻觉票。

    仅限拉丁/英文这一类确定性字符扫描能 100% 证伪的问题；scope/continuity 等需要
    语境判断的不在此列（避免误删合法低分票）。
    """
    if not hard_gates_all_clean:
        return False
    for it in review.get("issues", []) or []:
        if not isinstance(it, dict):
            continue
        blob = f"{it.get('description','')} {it.get('suggested_fix','')}"
        if any(term in blob for term in _HARD_GATE_CONTRADICTION_TERMS):
            return True
    return False


def filter_valid_votes(pairs, hard_gates_all_clean: bool):
    """pairs: list[(review, score)]。剔除技术性错误票、硬门证伪幻觉票、算术自相矛盾票，保序返回。"""
    out = []
    for rv, sc in pairs:
        if not is_valid_vote(rv):
            continue
        if hard_gate_contradicted_vote(rv, hard_gates_all_clean):
            continue
        # CC26：维度分合计与自报 raw_total 不符（>1）= 评审自身算术错误，等同技术性废票。
        if dim_sum_mismatch(rv):
            continue
        out.append((rv, float(sc)))
    return out


def signals_all_green(signals: dict | None) -> bool:
    """确定性信号是否全部健康（用于 Q5①免三评 与 suspected_reviewer_bias 判定）。"""
    if not signals:
        return False
    if not signals.get("hard_gates_all_clean", False):
        return False
    if float(signals.get("beat_coverage_ratio", 0.0) or 0.0) < BEAT_COVERAGE_GREEN:
        return False
    atm = signals.get("atmosphere_ratio_per_scene", {}) or {}
    if any(float(v) > SCENE_ATMOSPHERE_GREEN for v in atm.values()):
        return False
    return True


def diagnose_low_score(median: float, signals: dict | None,
                       soft_line: float = 85.0) -> str:
    """中位 < 软线时区分“章确实不行”与“疑似评审偏严”。"""
    if median >= soft_line:
        return "publish_track"
    if not signals_all_green(signals):
        return "genuine_quality_issue"
    return "suspected_reviewer_bias"
