# -*- coding: utf-8 -*-
"""CC30（DS 裁决 Q2）：Agnes 退化时间窗批次级自动响应状态机（零 LLM，纯规则）。

按【调用次数】滑动窗口观测（无人值守下不依赖易漂移的绝对时间）：
- REASONING_ONLY 空票：最近 20 次 LLM 调用，率>=30% 且次数>=3 → P1；>=50% 且>=5 → P0。
- 短稿：最近 12 次 scene 初稿/重生，率>=50% 且次数>=3 → P1。
- 废票(parse/schema/空)：最近 20 次，率>=40% 且>=5 → P1；>=60% 且>=8 → P0。

动作优先级：P0 严重熔断（冷却3分钟、并发→1、当前场 pro）> P1 降并发 4→2 + 未完成场按
Q1 切 pro > P2（P1 后 5 次调用未降：短冷却、并发→1）> P3（10 次仍高：统一切 pro 限时
10 分钟/最多5章）。恢复：最近10次 ro<=10%、短稿<=20%、废票<=10%，且连续3次有效初稿。
冷却上限：单次3分钟，每小时最多2次，第3次只降并发不 sleep。一次偶发废票不触发（有最小样本）。

本状态机只产出 action（含 cooldown_seconds 但【不自行 sleep】），由上层调度执行。
"""
from __future__ import annotations
import threading
from collections import deque

CALL_WIN = 20
SCENE_WIN = 12
PARSE_WIN = 20

RO_P1_RATE, RO_P1_N = 0.30, 3
RO_P0_RATE, RO_P0_N = 0.50, 5
SHORT_P1_RATE, SHORT_P1_N = 0.50, 3
WASTE_P1_RATE, WASTE_P1_N = 0.40, 5
WASTE_P0_RATE, WASTE_P0_N = 0.60, 8

P2_AFTER = 5      # P1 后 5 次调用未降 → P2
P3_AFTER = 10     # 10 次仍高 → P3
COOLDOWN_SECONDS = 180
COOLDOWN_PERIOD = 3600
COOLDOWN_MAX_PER_PERIOD = 2

RECOVER_RO = 0.10
RECOVER_SHORT = 0.20
RECOVER_WASTE = 0.10
RECOVER_VALID_STREAK = 3


def _rate(window, flag):
    if not window:
        return 0.0, 0
    n = sum(1 for x in window if x == flag)
    return n / len(window), n


class DegradeWindow:
    def __init__(self):
        self.calls = deque(maxlen=CALL_WIN)   # True=reasoning_only
        self.scenes = deque(maxlen=SCENE_WIN)  # True=short
        self.parse = deque(maxlen=PARSE_WIN)  # True=waste
        self.level = 0                         # 0 正常,1=P1,2=P0/P2(冷却),3=P3
        self._elevated_obs = 0
        self._valid_streak = 0
        self._p0_fired = False
        self._cooldown_at: list[float] = []
        self.lock = threading.RLock()

    # ---- 观测入口，返回 action(dict) 或 None ----
    def observe_call(self, reasoning_only: bool, now: float = 0.0):
        with self.lock:
            self.calls.append(bool(reasoning_only))
            return self._evaluate(now)

    def observe_scene(self, short: bool, now: float = 0.0):
        with self.lock:
            self.scenes.append(bool(short))
            return self._evaluate(now)

    def observe_parse(self, waste: bool, now: float = 0.0):
        with self.lock:
            self.parse.append(bool(waste))
            return self._evaluate(now)

    def observe_valid_draft(self, now: float = 0.0):
        """记录一次有效初稿（用于恢复的连续有效计数），并尝试恢复判定。"""
        with self.lock:
            self._valid_streak += 1
            if self.level > 0 and self._recovery_met():
                action = {"level": "RECOVER", "conc": 4, "cooldown_seconds": 0,
                          "pro_scope": "none", "flash": True}
                self.level = 0
                self._elevated_obs = 0
                self._valid_streak = 0
                self._p0_fired = False
                return action
            return None

    # ---- 信号判定 ----
    def _signals(self):
        ro_r, ro_n = _rate(self.calls, True)
        sh_r, sh_n = _rate(self.scenes, True)
        w_r, w_n = _rate(self.parse, True)
        p0 = ((ro_r >= RO_P0_RATE and ro_n >= RO_P0_N) or
              (w_r >= WASTE_P0_RATE and w_n >= WASTE_P0_N))
        p1 = ((ro_r >= RO_P1_RATE and ro_n >= RO_P1_N) or
              (sh_r >= SHORT_P1_RATE and sh_n >= SHORT_P1_N) or
              (w_r >= WASTE_P1_RATE and w_n >= WASTE_P1_N))
        return p0, p1

    def _recovery_met(self) -> bool:
        ro_r, _ = _rate(list(self.calls)[-10:], True)
        sh_r, _ = _rate(list(self.scenes)[-10:], True)
        w_r, _ = _rate(list(self.parse)[-10:], True)
        return (ro_r <= RECOVER_RO and sh_r <= RECOVER_SHORT and w_r <= RECOVER_WASTE
                and self._valid_streak >= RECOVER_VALID_STREAK)

    def _can_cooldown(self, now: float):
        self._cooldown_at = [t for t in self._cooldown_at if now - t < COOLDOWN_PERIOD]
        return len(self._cooldown_at) < COOLDOWN_MAX_PER_PERIOD

    def _evaluate(self, now: float):
        p0, p1 = self._signals()
        if not (p0 or p1):
            # 信号消退：恢复初始态（恢复动作由 observe_valid_draft 判定）
            if self.level > 0:
                self._valid_streak = 0
                self._elevated_obs = 0
                self._p0_fired = False
                self.level = 0
            return None

        # 仍处于高信号
        self._valid_streak = 0
        if p0 and not self._p0_fired:
            self._p0_fired = True
            return self._action(0, now)  # P0 严重熔断（一次 episode 只熔断一次）
        if self.level == 0 and p1:
            return self._action(1, now)  # P1
        # 已在降级态，累计未改善观测
        self._elevated_obs += 1
        if self.level == 1 and self._elevated_obs >= P2_AFTER:
            return self._action(2, now)
        if self.level in (1, 2) and self._elevated_obs >= P3_AFTER:
            return self._action(3, now)
        return None

    def _action(self, lvl: int, now: float):
        if lvl == 0:
            cool = self._can_cooldown(now)
            self.level = 2  # P0 后进入冷却态（不再触发 P2 二次短冷却），持续恶化才升 P3
            self._elevated_obs = 0
            if cool:
                self._cooldown_at.append(now)
            return {"level": "P0", "conc": 1,
                    "cooldown_seconds": COOLDOWN_SECONDS if cool else 0,
                    "pro_scope": "current", "cooled": cool}
        if lvl == 1:
            self.level = 1
            self._elevated_obs = 0
            return {"level": "P1", "conc": 2, "cooldown_seconds": 0,
                    "pro_scope": "unfinished"}
        if lvl == 2:
            cool = self._can_cooldown(now)
            self.level = 2
            if cool:
                self._cooldown_at.append(now)
            return {"level": "P2", "conc": 1,
                    "cooldown_seconds": COOLDOWN_SECONDS if cool else 0,
                    "pro_scope": "unfinished", "cooled": cool}
        # P3
        self.level = 3
        return {"level": "P3", "conc": 1, "cooldown_seconds": 0,
                "pro_scope": "all", "pro_window_seconds": 600, "pro_max_chapters": 5}
