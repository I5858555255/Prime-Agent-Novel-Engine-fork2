# -*- coding: utf-8 -*-
"""CC30（DS 裁决 Q1/Q3）：writer 按场退化信号自动切 agnes-2.5-pro + pro 用量预算（零 LLM）。

策略（DS Q1，选 b）：flash 先试，检测到退化信号后【按场】切 pro 重出；不常驻 pro，
也不一退化就整章切。阈值：
- 同场短稿重生（len<floor 或 coherently_short）累计 >=2 次 → 切 pro；
- 同场 REASONING_ONLY 空票 >=2 次 → 切 pro；
- 本章 REASONING_ONLY 累计 >=3 次 → 当前未完成场全部 pro（已完成场不重出）；
- 命中 latin wordstream（连续拉丁词>=40 或占比异常）→ 直接切 pro，不再 flash 重试；
- beats_covered=0 且重试 >=1 次仍 0 → 切 pro；
- 章级二线熔断：连续 K=3 章 best<82，或 5 章内终检 gap-continue >=2 次 → 下一章整章 pro。

预算（DS Q3）：pro 场占比目标 <=20%、硬上限 30%；每 10 章最多 8 个 pro 场（硬 12）；
每章最多 2 个 pro 场；退化场>=3 则整章 pro（计 4 场），每 10 章最多 2 个整章 pro；
滚动 50 章平均 pro 场占比 <=20%。正常场仍 flash。超预算不 HALT，只记 over_budget 降级信号。
"""
from __future__ import annotations

# ---- 阈值 ----
SHORT_RETRY_SWITCH = 2          # 同场短稿重生 >=2 次
SCENE_REASONING_SWITCH = 2      # 同场 REASONING_ONLY >=2 次
CHAPTER_REASONING_FORCE = 3     # 本章 REASONING_ONLY 累计 >=3 次，后续场全 pro
BEATS_ZERO_RETRY_SWITCH = 1     # beats_covered=0 且重试 >=1 次
FORCE_PRO_CONSECUTIVE_CHAPTERS = 3   # 连续 K 章 best<82
GAP_FORCE_WINDOW = 5            # 5 章内
GAP_FORCE_MIN = 2               # 终检 gap >=2 次
AUTO_LINE = 82.0

# ---- 预算 ----
WINDOW_CHAPTERS = 10
PRO_SCENES_SOFT = 8             # 每10章软目标（20%：40场中8）
PRO_SCENES_HARD = 12            # 硬上限（30%）
PRO_SCENES_PER_CHAPTER = 2
WHOLE_CHAPTER_SCENE_COST = 4
WHOLE_CHAPTER_MAX_PER_WINDOW = 2
ROLLING_CHAPTERS = 50
ROLLING_PRO_RATIO = 0.20
WHOLE_CHAPTER_DEGRADED_MIN = 3  # 退化场>=3 → 整章 pro


def _i(v, d=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return d


def _f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def should_switch_scene_pro(scene: dict, chapter: dict | None = None) -> bool:
    """单场是否切 pro。scene/chapter 为退化计数字典（键缺失按 0 处理）。"""
    s = scene or {}
    ch = chapter or {}
    if bool(ch.get("force_pro")):
        return True
    if bool(s.get("latin_stream_hit")):
        return True
    if _i(s.get("beats_covered"), 0) == 0 and _i(s.get("retry_count")) >= BEATS_ZERO_RETRY_SWITCH:
        return True
    if _i(s.get("short_retry_count")) >= SHORT_RETRY_SWITCH:
        return True
    if _i(s.get("reasoning_only_count")) >= SCENE_REASONING_SWITCH:
        return True
    if _i(ch.get("reasoning_only_total")) >= CHAPTER_REASONING_FORCE and not bool(s.get("completed")):
        return True
    return False


def should_force_whole_chapter_pro(*, degraded_scene_count: int = 0,
                                   recent_best_scores: list[float] | None = None,
                                   recent_final_gap_flags: list[bool] | None = None) -> bool:
    """章级整章 pro：退化场>=3，或连续K章best<82，或近5章终检gap>=2。"""
    if _i(degraded_scene_count) >= WHOLE_CHAPTER_DEGRADED_MIN:
        return True
    scores = [_f(x) for x in (recent_best_scores or [])]
    tail = scores[-FORCE_PRO_CONSECUTIVE_CHAPTERS:]
    if len(tail) == FORCE_PRO_CONSECUTIVE_CHAPTERS and all(x < AUTO_LINE for x in tail):
        return True
    flags = list(recent_final_gap_flags or [])[-GAP_FORCE_WINDOW:]
    if sum(1 for f in flags if f) >= GAP_FORCE_MIN:
        return True
    return False


class ProSceneBudget:
    """按章记录 pro 场用量，做 10 章窗口 + 50 章滚动预算判定（纯内存确定性）。"""

    def __init__(self):
        # chapter_num -> {"pro_scenes": int, "whole": bool}
        self._chapters: dict[int, dict] = {}

    def record_chapter(self, chapter_num: int, pro_scene_count: int, whole_chapter_pro: bool = False):
        self._chapters[int(chapter_num)] = {
            "pro_scenes": WHOLE_CHAPTER_SCENE_COST if whole_chapter_pro else _i(pro_scene_count),
            "whole": bool(whole_chapter_pro)}

    def _window(self, current: int, size: int) -> list[dict]:
        return [self._chapters[c] for c in
                range(int(current) - size + 1, int(current) + 1) if c in self._chapters]

    def pro_scenes_in_window(self, current: int, size: int = WINDOW_CHAPTERS) -> int:
        return sum(rec["pro_scenes"] for rec in self._window(current, size))

    def whole_chapters_in_window(self, current: int) -> int:
        return sum(1 for rec in self._window(current, WINDOW_CHAPTERS) if rec["whole"])

    def rolling_ratio(self, current: int) -> float:
        recs = self._window(current, ROLLING_CHAPTERS)
        if not recs:
            return 0.0
        total = len(recs) * WHOLE_CHAPTER_SCENE_COST
        return sum(r["pro_scenes"] for r in recs) / max(total, 1)

    def plan_chapter(self, current: int, degraded_switch_scenes: list[bool],
                     force_whole_chapter: bool = False) -> dict:
        """给定本章各场“是否触发切 pro”的布尔列表，返回本章 pro 计划与预算状态。

        章级熔断（force_whole_chapter）或退化场>=3 → 整章 pro（计4场，每10章最多2章）；
        整章名额用尽则降级为按场最多2个并记 over_budget；否则每章最多2个 pro 场。
        """
        flags = [bool(x) for x in (degraded_switch_scenes or [])]
        n_deg = sum(flags)
        whole = bool(force_whole_chapter) or n_deg >= WHOLE_CHAPTER_DEGRADED_MIN
        if whole and self.whole_chapters_in_window(current) >= WHOLE_CHAPTER_MAX_PER_WINDOW:
            whole = False  # 整章名额用尽 → 按场降级
        pro_count = WHOLE_CHAPTER_SCENE_COST if whole else min(n_deg, PRO_SCENES_PER_CHAPTER)
        projected = self.pro_scenes_in_window(current) + pro_count
        status = "OK"
        over = False
        if projected > PRO_SCENES_HARD:
            status, over = "HARD_STOP_10", True
        elif projected > PRO_SCENES_SOFT:
            status = "SOFT_WARN_10"
        if status == "OK" and self.rolling_ratio(current) > ROLLING_PRO_RATIO:
            status = "ROLLING_WARN_50"
        return {"whole_chapter_pro": whole, "pro_scene_count": pro_count,
                "degraded_scene_count": n_deg, "over_budget": over, "budget_status": status,
                "window_projected": projected}
