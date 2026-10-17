# -*- coding: utf-8 -*-
"""前世揭示尺度检测（确定性，零 LLM）。

ch48 口径决策：author_intent 十卷 forbidden 无"前世/转世"禁令，
character_bible C001 将"神魂跨界重生"定为既定身份——因此不做 hard block，
改为"揭示尺度"soft_warn：150 字窗口内出现 >=3 个前世具象细节词时，
标记"揭示尺度偏高"软提示（不阻断，计入打分供人工审阅）。

阈值与词表来自 config/quality_thresholds.json 的 past_life_reveal 块，
缺省时使用本模块默认值。检测结果经 orchestrator 的 deterministic_signals
注入 reviewer（review_chapter 已有该参数），不新增任何 LLM 调用。
"""
from __future__ import annotations

DEFAULT_DETAIL_TERMS = [
    "前世", "上辈子", "上一世", "重生前", "弥留", "病床",
    "病房", "抢救", "临终", "咽气", "消毒水", "白墙",
    "吊瓶", "输液", "心电图",
]


def detect_past_life_reveal_scale(
    text: str,
    detail_terms: list[str] | None = None,
    window_chars: int = 150,
    detail_hits: int = 3,
) -> dict:
    """滑动窗口统计前世具象细节词命中，判定"揭示尺度偏高"。

    Args:
        text: 待检正文（净化后文本）。
        detail_terms: 前世具象细节词表；None 用默认词表。
        window_chars: 窗口字数（默认 150，与 quality_thresholds.json 对齐）。
        detail_hits: 单窗口命中词数阈值（默认 3）。

    Returns:
        {"reveal_scale_high": bool, "max_window_hits": int, "windows": [...]}
        windows 每项 {"start": 字符偏移, "hit_count": 命中数, "hits": 命中的词列表}，
        最多记录前 5 个超阈窗口。
    """
    _terms = detail_terms or DEFAULT_DETAIL_TERMS
    _terms = [t for t in _terms if t]
    if not text or not _terms:
        return {"reveal_scale_high": False, "max_window_hits": 0, "windows": []}
    n = len(text)
    if n <= window_chars:
        seg = text
        hits = [t for t in _terms if t in seg]
        if len(hits) >= detail_hits:
            return {
                "reveal_scale_high": True,
                "max_window_hits": len(hits),
                "windows": [{"start": 0, "hit_count": len(hits), "hits": hits[:6]}],
            }
        return {"reveal_scale_high": False, "max_window_hits": len(hits), "windows": []}

    windows: list[dict] = []
    step = max(1, window_chars // 2)
    for i in range(0, n - window_chars + 1, step):
        seg = text[i:i + window_chars]
        hits = [t for t in _terms if t in seg]
        if len(hits) >= detail_hits:
            windows.append({"start": i, "hit_count": len(hits), "hits": hits[:6]})
            if len(windows) >= 5:
                break
    max_hits = max((w["hit_count"] for w in windows), default=0)
    return {
        "reveal_scale_high": bool(windows),
        "max_window_hits": max_hits,
        "windows": windows,
    }
