# -*- coding: utf-8 -*-
"""CC28 批B 3b：相邻场同一现场/同一时刻近重复重演检测（零 LLM）。

r40 ch0 真硬伤：scene3 已演完弃婴现场，scene4 又把同一片老松根、襁褓啼哭、抱起婴孩
几乎原样演了一遍（叙事时间没有推进）。逐字重复门只看全章 token 自重复，判不了“相邻两
场把同一事件再演一次”。

本门为高置信、零误伤的【近重复】版：用相邻两场 CJK 字符 4-gram 的 Jaccard 相似度，
并豁免显式闪回（“他回忆起…”）与真实时空推进（出现新时辰/时间跳跃且带来足量新内容）。
阈值刻意高，只抓“近乎重演”，绝不误伤正常续场。

注：完全换词、但语义仍重演的情形，纯字符零 LLM 无法可靠判别（会误伤），其根治放在批C
director 的结构化 blueprint 门（scene_anchor_hash / time_delta / irreversible_state_delta
/ repeat_prior_scene 在任务卡源头拒卡），本门不越界猜测语义。
"""
from __future__ import annotations

import re

# 相邻场 CJK 4-gram Jaccard 达到该值即视为近重复重演。
REPRISE_JACCARD = 0.55
# 时空推进豁免：后场新 4-gram（前场未出现）占比达到该值，说明有足量新内容。
ADVANCE_NEW_RATIO = 0.45
# 后场过短不参与判定（短过渡场天然与前场共享措辞）。
MIN_SCENE_CHARS = 120

_CJK_RE = re.compile(r"[一-鿿]")
# 显式闪回/回忆标记：后场以回忆视角重提前事属于合法叙事，不报重演。
_FLASHBACK_RE = re.compile(
    r"回忆起|忆起|想起|记起|记得|记忆里|脑海中|脑海里|那日|当年|从前|多年前|许多年前|"
    r"上一世|前世的|那一幕|往事|回想")
# 时间推进标记：后场出现前场未出现的时辰/时间跳跃，视为时间已推进。
_TIME_RE = re.compile(
    r"子时|丑时|寅时|卯时|辰时|巳时|午时|未时|申时|酉时|戌时|亥时|"
    r"次日|翌日|第二日|三日后|几日后|数日后|多日后|半个时辰|一个时辰|数个时辰|"
    r"入夜|黎明|黄昏|拂晓|天明|天黑|次日清晨|翌日清晨")


def _cjk_only(text: str) -> str:
    return "".join(_CJK_RE.findall(text or ""))


def _ngrams(s: str, n: int = 4) -> set[str]:
    if len(s) < n:
        return {s} if s else set()
    return {s[i:i + n] for i in range(len(s) - n + 1)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def score_pair(prev_scene: str, scene: str) -> dict:
    """判定后场 scene 是否把前场 prev_scene 的同一现场近重复重演。"""
    a = _cjk_only(prev_scene)
    b = _cjk_only(scene)
    ga, gb = _ngrams(a), _ngrams(b)
    sim = _jaccard(ga, gb)
    new_ratio = (len(gb - ga) / len(gb)) if gb else 0.0

    flashback = bool(_FLASHBACK_RE.search(scene or ""))
    times_prev = set(_TIME_RE.findall(prev_scene or ""))
    times_now = set(_TIME_RE.findall(scene or ""))
    time_advance = bool(times_now - times_prev)

    reasons = []
    if len(b) < MIN_SCENE_CHARS:
        return {"similarity": round(sim, 3), "new_ratio": round(new_ratio, 3),
                "flashback": flashback, "time_advance": time_advance,
                "is_reprise": False, "reasons": ["scene_too_short"]}
    if sim >= REPRISE_JACCARD:
        reasons.append(f"4gram_jaccard_{sim:.2f}>={REPRISE_JACCARD}")
    if flashback:
        reasons.append("explicit_flashback")
    if time_advance and new_ratio >= ADVANCE_NEW_RATIO:
        reasons.append("time_advanced_with_new_content")

    is_reprise = sim >= REPRISE_JACCARD and not flashback and not (
        time_advance and new_ratio >= ADVANCE_NEW_RATIO)
    return {"similarity": round(sim, 3), "new_ratio": round(new_ratio, 3),
            "flashback": flashback, "time_advance": time_advance,
            "is_reprise": is_reprise, "reasons": reasons}


def detect_adjacent_reprise(scenes: list[str]) -> list[dict]:
    """对有序场景文本逐对判定相邻重演，返回命中的后场序号（1-based 相邻对的后场 index）。"""
    hits: list[dict] = []
    for i in range(1, len(scenes)):
        r = score_pair(scenes[i - 1], scenes[i])
        if r["is_reprise"]:
            hits.append({"prev_index": i - 1, "index": i, **r})
    return hits
