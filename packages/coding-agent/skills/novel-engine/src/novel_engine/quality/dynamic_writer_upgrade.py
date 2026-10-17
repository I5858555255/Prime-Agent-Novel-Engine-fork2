# -*- coding: utf-8 -*-
"""CC28/批D（DS 裁决第5点）：按章动态 writer 模型升级（纯规则，零 LLM）。

非常驻 pro writer：连续 K=3 章 best-of 后 CC24 混合分仍 < 自动提交线（或同维无增益），
才对下一章临时启用 agnes-2.5-pro 出初稿；预算硬约束：每 10 章最多 2 章用 pro、
单章 pro 至多 1 次；超预算不升级，转人工抽检（不阻断流水线）。

pro 初稿采纳判据：相对 flash 基线 CC24 提升 >=2 且 >=auto_submit_line 才采纳，
否则回退 flash 初稿 + best-of。所有判定纯函数/小状态类，可离线单测。
"""
from __future__ import annotations

DEFAULT_K = 3
DEFAULT_WINDOW = 10
DEFAULT_MAX_PRO_PER_WINDOW = 2
DEFAULT_ADOPT_GAIN = 2.0
DEFAULT_AUTO_LINE = 82.0


def should_upgrade_writer(*, consecutive_below: int, pro_used_in_window: int,
                          pro_used_this_chapter: bool = False,
                          k: int = DEFAULT_K,
                          window_size: int = DEFAULT_WINDOW,
                          max_pro_per_window: int = DEFAULT_MAX_PRO_PER_WINDOW) -> dict:
    """返回 {upgrade, reason}。连续 K 章欠线 + 窗口预算未满 + 本章未用过 pro 才升级。"""
    cb = int(consecutive_below or 0)
    used = int(pro_used_in_window or 0)
    if pro_used_this_chapter:
        return {"upgrade": False, "reason": "pro_already_used_this_chapter"}
    if used >= int(max_pro_per_window):
        return {"upgrade": False, "reason": "pro_budget_exceeded_to_manual_sample"}
    if cb < int(k):
        return {"upgrade": False, "reason": "below_k_threshold"}
    return {"upgrade": True, "reason": f"consecutive_{cb}_below_line_budget_ok"}


def adopt_pro_draft(pro_score, flash_baseline_score,
                    *, adopt_gain: float = DEFAULT_ADOPT_GAIN,
                    auto_line: float = DEFAULT_AUTO_LINE) -> dict:
    """pro 初稿是否采纳：提升>=adopt_gain 且达到自动线；否则回退 flash 初稿。"""
    try:
        ps = float(pro_score)
    except (TypeError, ValueError):
        return {"adopt": False, "reason": "pro_score_unparseable_fallback_flash"}
    try:
        fb = float(flash_baseline_score)
    except (TypeError, ValueError):
        fb = None
    gain = (ps - fb) if fb is not None else None
    if ps < float(auto_line):
        return {"adopt": False, "gain": gain, "reason": "pro_still_below_auto_line_fallback_flash"}
    if gain is not None and gain < float(adopt_gain):
        return {"adopt": False, "gain": gain, "reason": "pro_gain_insufficient_fallback_flash"}
    return {"adopt": True, "gain": gain, "reason": "pro_adopted"}


class ProWriterBudget:
    """滚动窗口预算计数（窗口=最近 window_size 章）。纯内存，确定性。"""

    def __init__(self, window_size: int = DEFAULT_WINDOW,
                 max_pro_per_window: int = DEFAULT_MAX_PRO_PER_WINDOW, k: int = DEFAULT_K):
        self.window_size = int(window_size)
        self.max_pro_per_window = int(max_pro_per_window)
        self.k = int(k)
        self._pro_chapters: list[int] = []

    def record(self, chapter_num: int, used_pro: bool) -> None:
        if used_pro:
            self._pro_chapters.append(int(chapter_num))
        self._evict(int(chapter_num))

    def _evict(self, current: int) -> None:
        floor = current - self.window_size + 1
        self._pro_chapters = [c for c in self._pro_chapters if c >= floor]

    def pro_used_in_window(self, current: int) -> int:
        self._evict(int(current))
        return len(self._pro_chapters)

    def can_upgrade(self, current: int, consecutive_below: int) -> dict:
        return should_upgrade_writer(
            consecutive_below=int(consecutive_below),
            pro_used_in_window=self.pro_used_in_window(int(current)),
            k=self.k, window_size=self.window_size,
            max_pro_per_window=self.max_pro_per_window)
