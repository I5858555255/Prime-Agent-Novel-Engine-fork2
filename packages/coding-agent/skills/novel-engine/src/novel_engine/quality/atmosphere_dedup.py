# -*- coding: utf-8 -*-
"""CC round-18 R5 + P8E E2：相邻场氛围母题堆叠检测（软 note，零硬阻断）。

指标：按母题族统计每场氛围词频率浓度（次/千汉字），相邻两场同一族都超过
族级 min 且至少一场超过族级 extreme → 命中。剔除通用氛围词（光/风/呼吸/目光/影等），
只保留具有区分度的母题族。

阈值由真实 ch1-5 分场数据反推校准：ch1-4 零命中、ch5 命中已知堆叠场。
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_SEP = "\n\n※\n\n"

# D3 P8D: 与 cross_scene_crisis_gate._split_text_into_scenes 同口径——按裸※或★切分
_SPLIT_RE = re.compile(r"[※★]")


# ── 氛围母题族定义（剔除通用词，保留具有区分度的堆叠信号）───────────────────

_THEME_FAMILIES: dict[str, list[str]] = {
    "雾浊": ["雾", "雾气", "雾丝", "雾墙", "雾霭", "浓雾", "迷雾",
             "浊", "浊气", "浑浊", "瘴", "雾气弥漫"],
    "黑暗夜色": ["暗", "黑暗", "昏", "昏暗", "漆黑", "夜色", "黑夜",
                 "夜风", "夜露", "阴沉", "阴霾"],
    "寂静死寂": ["寂", "寂静", "死寂", "沉寂", "静谧", "肃杀"],
    "寒冷": ["寒", "寒意", "寒冷", "冰", "霜", "刺骨", "冷意"],
    "腥腐": ["腥", "腥气", "腐", "腐朽", "霉", "霉湿", "霉味", "腐臭"],
    "压抑沉": ["压", "压抑", "沉闷", "沉重", "滞", "窒", "窒息"],
}

# ── 氛围母题族定义（剔除通用词，保留具有区分度的堆叠信号）───────────────────
# 族级阈值由真实 ch1-5 partial 最终场浓度反推锁定（2026-09-22 主指挥 P8F 返修）。
# 口径：次/千汉字；命中规则＝相邻两场都 ≥min_both 且 max ≥extreme_one。
# 数据来源：ch1-5 partial authoritative 最终场，ch1-4 零命中，ch5 黑暗 {场2-3,场3-4}+雾浊{场2-3} 命中。
_FAMILY_THRESHOLDS: dict[str, tuple[float, float]] = {
    "雾浊":       (4.5, 5.0),   # ch5 场2-3=5.0/5.4；ch1-4 最高 3.3 → 间隙干净
    "黑暗夜色":   (5.5, 7.0),   # ch5 场2-3=6.5/7.4、场3-4=7.4/10.2；ch1-4 最高 5.3 → 间隙干净
    "寂静死寂":   (4.5, 5.5),   # ch1-5 均不触发，留作未来真正死寂堆叠章
    "寒冷":       (4.5, 6.0),   # ch1 自然高至 3.7，与 ch5 重叠无区分度 → 暂不触发
    "腥腐":       (3.0, 4.0),   # ch1-5 观测 ≤2.2 → 暂不触发
    "压抑沉":     (3.5, 4.5),   # ch1-5 观测 ≤3.1 → 暂不触发
}

# 替代感官方向
_SENSORY_DIRECTIONS: dict[str, list[str]] = {
    "雾浊": ["触觉温度对比", "气味描写", "光线变化"],
    "黑暗夜色": ["动作细节", "对话推进", "心理刻画"],
    "寂静死寂": ["声音突转", "心跳节奏", "远处声响"],
    "寒冷": ["身体反应", "环境动态", "触觉温度对比"],
    "腥腐": ["视觉细节", "动作捕捉", "时间流逝感"],
    "压抑沉": ["心理刻画", "对话推进", "动作细节"],
}


def _split_scenes(text: str) -> list[str]:
    """按 ※（U+203B）或 ★（U+2605）分场符切出各场文本，忽略空段。
    与 cross_scene_crisis_gate 裸※/★正则切分口径一致。"""
    if not text:
        return []
    parts = _SPLIT_RE.split(text)
    return [p.strip() for p in parts if p.strip()]


def _family_freq(scene_text: str, families: dict[str, list[str]]) -> dict[str, float]:
    """计算单场各母题族的频率浓度（次/千汉字）。"""
    cn = cn_chars(scene_text)
    if cn == 0:
        return {k: 0.0 for k in families}
    return {
        k: sum(scene_text.count(w) for w in words) / (cn / 1000.0)
        for k, words in families.items()
    }


def _pair_concentration(
    f1: dict[str, float],
    f2: dict[str, float],
    thresholds: dict[str, tuple[float, float]],
) -> list[tuple[str, float, float]]:
    """返回命中母题族列表：(family, conc_scene_a, conc_scene_b)。"""
    hits = []
    for fam, (min_v, ext_v) in thresholds.items():
        v1 = f1.get(fam, 0.0)
        v2 = f2.get(fam, 0.0)
        if v1 >= min_v and v2 >= min_v and max(v1, v2) >= ext_v:
            hits.append((fam, v1, v2))
    return hits


def _find_sensory_direction(family: str) -> str:
    return _SENSORY_DIRECTIONS.get(family, ["动作细节", "对话推进"])[0]


# ── 公共 API（保持与旧版兼容）───────────────────────────────────────────────


def extract_atmosphere_terms(text: str, terms: list[str] | None = None) -> list[str]:
    """保留旧接口：从文本中提取匹配的氛围词列表。"""
    if terms is None:
        terms = _DEFAULT_ATMOSPHERE_TERMS_GLOBAL
    hits: list[str] = []
    for term in terms:
        cnt = text.count(term)
        if cnt:
            hits.extend([term] * cnt)
    return hits


# 全局备用词表（供旧接口兼容）
_DEFAULT_ATMOSPHERE_TERMS_GLOBAL = list({w for ws in _THEME_FAMILIES.values() for w in ws})


def scene_atmosphere_freq(scene_text: str, terms: list[str] | None = None) -> dict[str, int]:
    """统计单场文本中各氛围词的出现频次。兼容旧 Counter 接口。"""
    from collections import Counter
    if terms is None:
        terms = _DEFAULT_ATMOSPHERE_TERMS_GLOBAL
    return Counter(extract_atmosphere_terms(scene_text, terms))


def _jaccard_set(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度 = |A∩B| / |A∪B|。空集返回 0.0。"""
    if not a and not b:
        return 0.0
    return len(a & b) / len(a | b)


def adjacent_jaccards(scene_freqs: list[dict[str, int]]) -> list[dict]:
    """保留旧接口：计算相邻场氛围词集合的 Jaccard 相似度列表。"""
    results = []
    for i in range(len(scene_freqs) - 1):
        set_a = set(scene_freqs[i])
        set_b = set(scene_freqs[i + 1])
        shared = set_a & set_b
        results.append({
            "scene_a": i + 1,
            "scene_b": i + 2,
            "jaccard": _jaccard_set(set_a, set_b),
            "shared": len(shared),
            "shared_terms": sorted(shared),
        })
    return results


def detect_atmosphere_dedup(
    scene_texts: list[str],
    jaccard_threshold: float = 0.60,
    min_shared: int = 8,
    terms: list[str] | None = None,
) -> list[dict]:
    """P8E E2：基于母题族浓度共振的检测（替代旧 Jaccard 逻辑）。

    返回命中列表，每项含：
      - scene_num: 命中场序号（1-based，取左场）
      - family: 命中母题族
      - conc_scene_a: 左场浓度（次/千汉字）
      - conc_scene_b: 右场浓度（次/千汉字）
      - high_freq_terms: 本场该族高频词
      - suggested_direction: 推荐替代感官方向
    """
    freqs = [_family_freq(txt, _THEME_FAMILIES) for txt in scene_texts]
    hits: list[dict] = []
    for i in range(len(freqs) - 1):
        pair = _pair_concentration(freqs[i], freqs[i + 1], _FAMILY_THRESHOLDS)
        for fam, v1, v2 in pair:
            words_in_scene = [w for w in _THEME_FAMILIES[fam] if w in scene_texts[i]]
            hits.append({
                "scene_num": i + 1,
                "family": fam,
                "conc_scene_a": round(v1, 2),
                "conc_scene_b": round(v2, 2),
                "high_freq_terms": words_in_scene[:8],
                "suggested_direction": _find_sensory_direction(fam),
            })
    return hits


def generate_repolish_directive(hit: dict) -> str:
    """将单次命中转化为定向重抛光指令文本（供 LLM 消费）。

    使用新 E2 字段：family/conc_scene_a/b/high_freq_terms/suggested_direction。
    不出现 Jaccard/shared 字样。
    """
    sn = hit["scene_num"]
    family = hit.get("family", "?")
    terms = ", ".join(hit.get("high_freq_terms", [])[:5]) or "（该族氛围词）"
    v_a = hit.get("conc_scene_a", 0)
    v_b = hit.get("conc_scene_b", 0)
    direction = hit.get("suggested_direction", "动作细节")
    return (
        f"【氛围去重重抛光·场{sn}】"
        f"本场景氛围母题「{family}」高频堆叠：「{terms}」在相邻两场浓度分别为 {v_a:.1f}/k 与 {v_b:.1f}/k 汉字，"
        f"属于相邻两场同一氛围母题高频堆叠信号。"
        f"请保留场景核心情节不变，将上述氛围词替换为[{direction}]方向的具体描写，"
        f"使本场氛围独特性显著提升。仅需改写氛围渲染部分，不动对白与动作。"
    )


def cn_chars(text: str) -> int:
    """统计文本中汉字数量（CJK Unified Ideographs）。"""
    return sum(1 for c in text if "一" <= c <= "鿿")


def cn_chars_total(text: str) -> int:
    """统计完整章节汉字数量。"""
    scenes = _split_scenes(text)
    return sum(cn_chars(s) for s in scenes)


def roll_back_ok(
    new_scene_text: str,
    orig_scene_text: str,
    new_chapter_total: int,
    min_scene_cn: int = 1000,
    min_ch_cn: int = 6800,
    max_ch_cn: int = 10660,
    gate_result: dict | None = None,
    forbidden_result: list[dict] | None = None,
    crisis_result: dict | None = None,
) -> bool:
    """判定重抛光后是否通过守门条件（软回滚判定，非硬阻断）。

    守：单场>=1000汉字、全章6800-10660汉字。
    额外检查：gate_result（hard/high issues）、forbidden_result（禁词）、
    crisis_result（危机门翻坏）。任一回归即回滚。
    返回 True 表示可通过（不回滚），False 表示需回滚。
    """
    if not new_scene_text or cn_chars(new_scene_text) < min_scene_cn:
        return False
    if new_chapter_total < min_ch_cn or new_chapter_total > max_ch_cn:
        return False
    if gate_result is not None:
        issues = gate_result.get("issues") or []
        if any(i.get("severity") in ("hard", "high") for i in issues):
            return False
    if forbidden_result is not None:
        if forbidden_result:
            return False
    if crisis_result is not None:
        if not crisis_result.get("passed", True):
            return False
    return True


def run_atmosphere_dedup(
    chapter_text: str,
    root: str | Path | None = None,
    jaccard_threshold: float = 0.60,
    min_shared: int = 8,
) -> dict:
    """R5 主入口：对整章文本做氛围母题堆叠检测，返回检测报告中命中列表。

    纯函数，零 LLM 调用。真实 LLM 重抛光由上层调用方负责。
    返回 dict：
      - hits: list[dict]，命中列表（含 scene_num, family, conc_a, conc_b, etc.）
      - jaccards: list[dict]，相邻场 Jaccard 全量（含未命中）
      - scene_cn_counts: list[int]，每场汉字数
      - total_cn: int，全章汉字总数
    """
    # 安全防御：None/空/单场直接返回
    if not chapter_text:
        return {"hits": [], "jaccards": [], "scene_cn_counts": [], "total_cn": 0}

    scenes = _split_scenes(chapter_text)
    if len(scenes) < 2:
        return {"hits": [], "jaccards": [], "scene_cn_counts": [], "total_cn": cn_chars_total(chapter_text)}

    freqs = [_family_freq(s, _THEME_FAMILIES) for s in scenes]
    cn_counts = [cn_chars(s) for s in scenes]

    # 旧 Jaccard 报告（保留，供日志/调试用）
    old_freqs = [scene_atmosphere_freq(s) for s in scenes]
    jaccs = adjacent_jaccards(old_freqs)

    # 新指标：母题族浓度共振
    hits: list[dict] = []
    for i in range(len(freqs) - 1):
        pair = _pair_concentration(freqs[i], freqs[i + 1], _FAMILY_THRESHOLDS)
        if pair:
            for fam, v1, v2 in pair:
                hits.append({
                    "scene_num": i + 1,
                    "family": fam,
                    "conc_scene_a": round(v1, 2),
                    "conc_scene_b": round(v2, 2),
                    "high_freq_terms": [w for w in _THEME_FAMILIES[fam] if w in scenes[i]],
                    "suggested_direction": _find_sensory_direction(fam),
                })

    return {
        "hits": hits,
        "jaccards": jaccs,
        "scene_cn_counts": cn_counts,
        "total_cn": sum(cn_counts),
    }
