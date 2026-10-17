# -*- coding: utf-8 -*-
"""CC30（DS Q1/Q2/Q3）桥接控制器：把按场 pro 路由、退化窗口状态机、pro 预算组合成
writer 每场可直接查询的调度决策。零 LLM、纯内存；不持有也不调用任何客户端。

writer 热路径接线方式（后续 behind flag 开启，mock 路径不实例化）：
  ctrl = SceneFailoverController(chapter_num, budget, degrade, force_whole=...)
  for idx, scene in enumerate(blueprints):
      client = pro_client if ctrl.use_pro_for_scene(idx) else flash_client
      out = client.chat_completion(...)
      ctrl.feed_result(...)          # 归一化信号：ro/short/waste/valid
      if ctrl.pending_cooldown: time.sleep(ctrl.take_cooldown())   # 冷却由上层执行
      conc = ctrl.concurrency(4)    # P1->2, P0/P2->1
本模块只产出决策（含 cooldown_seconds），不自行 sleep。
"""
from __future__ import annotations
import threading

from novel_engine.quality import scene_model_router as sr
from novel_engine.quality import degrade_window as dw

# 一次场景初稿被判 REASONING_ONLY 空票的中文字符上限
REASONING_EMPTY_CJK = 20
# 场景硬地板（与质量门 scene floor 对齐）
SCENE_HARD_FLOOR = 860


def classify_scene_attempt(*, scene_text: str, beats_covered_count: int,
                           finish_reason: str = "", short_floor: int = SCENE_HARD_FLOOR,
                           latin_wordstream: bool = False) -> dict:
    """把一次结构化场景（重）生成结果归一化为退化信号（纯函数，零 IO）。

    返回 {reasoning_only, short, latin, beats_zero, valid, short_floor}。
    - reasoning_only: 近空（CJK<=20）且非 length 截断 → 模型空转票；
    - short: 正文低于本场短稿地板；
    - latin: 命中拉丁词流（>=40 连续拉丁字母）；
    - beats_zero: 未交付任何 beats_covered；
    - valid: 非空、非短、非拉丁即视为一次有效初稿（beats 不足由质量门另判）。
    """
    t = scene_text or ""
    cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    reasoning_only = cjk <= REASONING_EMPTY_CJK and finish_reason != "length"
    short = (not latin_wordstream) and len(t) < int(short_floor) and cjk > REASONING_EMPTY_CJK
    beats_zero = int(beats_covered_count or 0) == 0
    valid = (not reasoning_only) and (not short) and (not latin_wordstream) and cjk > REASONING_EMPTY_CJK
    return {"reasoning_only": reasoning_only, "short": bool(short),
            "latin": bool(latin_wordstream), "beats_zero": beats_zero,
            "valid": bool(valid), "cjk": cjk}



class SceneFailoverController:
    def __init__(self, chapter_num: int, budget: sr.ProSceneBudget,
                 degrade: "dw.DegradeWindow | None" = None,
                 force_whole_chapter: bool = False, n_scenes: int = 4, enabled: bool = True):
        self.chapter_num = int(chapter_num)
        self.budget = budget
        self.degrade = degrade if degrade is not None else dw.DegradeWindow()
        self.enabled = bool(enabled)
        self._n = int(n_scenes)
        # 每场的退化信号（scene dict 形态见 scene_model_router.should_switch_scene_pro）
        self._scene_sig: list[dict] = [dict() for _ in range(self._n)]
        self._chapter_ctx: dict = {"reasoning_only_total": 0}
        self._force_whole = bool(force_whole_chapter)
        self._plan: dict | None = None
        self._pro_idx: set[int] = set()
        self._cooldown_queue: list[int] = []
        self.lock = threading.RLock()
        self._recompute()

    # ---- 信号登记 ----
    def mark_scene_signal(self, idx: int, **sig):
        if 0 <= idx < self._n:
            self._scene_sig[idx].update(sig)
        self._recompute()

    def report_scene_attempt(self, idx: int, classified: dict, now: float = 0.0,
                             feed_fsm: bool = False):
        """登记某场一次（重）生成的归一化结果；累计该场退化计数并按需喂退化窗口。

        feed_fsm=False（默认）：只累计【按场切 pro】计数并上报 valid 恢复；REASONING/SHORT
        的退化窗口计数由 LLMClient 退化事件观察者负责（HTTP 层按调用统计，更准、不重复）。
        feed_fsm=True：无客户端观察者时由本场结果直接喂状态机。
        返回退化窗口 action（可能带 cooldown_seconds）或 None。
        """
        if not (self.enabled and 0 <= idx < self._n):
            return None
        with self.lock:
            sig = self._scene_sig[idx]
            sig["retry_count"] = int(sig.get("retry_count", 0)) + 1
            action = None
            if classified.get("latin"):
                sig["latin_stream_hit"] = True
            if classified.get("reasoning_only"):
                sig["reasoning_only_count"] = int(sig.get("reasoning_only_count", 0)) + 1
                if feed_fsm:
                    action = self.feed_result(reasoning_only=True, now=now)
            elif classified.get("short"):
                sig["short_retry_count"] = int(sig.get("short_retry_count", 0)) + 1
                if feed_fsm:
                    action = self.feed_result(short=True, now=now)
            # beats_covered 反映本场最近一次交付（0 时配合 retry_count 触发切换）
            sig["beats_covered"] = 0 if classified.get("beats_zero") else 1
            if classified.get("valid"):
                sig["completed"] = True
                action = self.feed_result(valid=True, now=now) or action
            self._recompute()
            return action

    def observe_client_degenerate(self, deg_cls: str, now: float = 0.0):
        """LLMClient 退化 200 事件入口（REASONING_ONLY/DEGENERATE_SHORT）→ 退化窗口。"""
        if not self.enabled:
            return None
        if deg_cls == "REASONING_ONLY":
            return self.feed_result(reasoning_only=True, now=now)
        if deg_cls == "DEGENERATE_SHORT":
            return self.feed_result(short=True, now=now)
        return None


    def report_parse_waste(self, now: float = 0.0):
        """结构化解析失败（废票）喂退化窗口。"""
        if not self.enabled:
            return None
        return self.feed_result(waste=True, now=now)


    def note_chapter_reasoning(self, count: int):
        self._chapter_ctx["reasoning_only_total"] = int(count)
        self._recompute()

    def _quality_flags(self):
        """仅按场自身质量信号（不含章级 REASONING_ONLY 强制），用于退化场>=3 判整章。"""
        return [sr.should_switch_scene_pro(self._scene_sig[i], {}) for i in range(self._n)]

    def _combined_flags(self):
        """质量信号 + 章级 REASONING_ONLY 累计（仅未完成场），用于决定哪些场用 pro。"""
        ch = dict(self._chapter_ctx)
        if self._force_whole:
            ch["force_pro"] = True
        return [sr.should_switch_scene_pro(self._scene_sig[i], ch) for i in range(self._n)]

    def _recompute(self):
        if not self.enabled:
            self._plan = None
            self._pro_idx = set()
            return
        qflags = self._quality_flags()
        cflags = self._combined_flags()
        whole = self._force_whole or sum(qflags) >= sr.WHOLE_CHAPTER_DEGRADED_MIN
        if whole:
            self._plan = self.budget.plan_chapter(
                self.chapter_num, qflags, force_whole_chapter=self._force_whole
                or sum(qflags) >= sr.WHOLE_CHAPTER_DEGRADED_MIN)
            # 整章名额用尽时 budget 会降级为按场；以 budget 结论为准
            if self._plan["whole_chapter_pro"]:
                self._pro_idx = set(range(self._n))
            else:
                hits = [i for i, f in enumerate(cflags) if f]
                self._pro_idx = set(hits[:self._plan["pro_scene_count"]])
        else:
            # 章级强制只命中未完成场；受每章最多 pro 场预算护栏限幅
            hits = [i for i, f in enumerate(cflags) if f]
            pro_count = min(len(hits), sr.PRO_SCENES_PER_CHAPTER)
            projected = self.budget.pro_scenes_in_window(self.chapter_num) + pro_count
            status, over = "OK", False
            if projected > sr.PRO_SCENES_HARD:
                status, over = "HARD_STOP_10", True
            elif projected > sr.PRO_SCENES_SOFT:
                status = "SOFT_WARN_10"
            if status == "OK" and self.budget.rolling_ratio(self.chapter_num) > sr.ROLLING_PRO_RATIO:
                status = "ROLLING_WARN_50"
            self._plan = {"whole_chapter_pro": False, "pro_scene_count": pro_count,
                          "degraded_scene_count": sum(qflags), "over_budget": over,
                          "budget_status": status, "window_projected": projected}
            self._pro_idx = set(hits[:pro_count])

    # ---- writer 查询 ----
    def use_pro_for_scene(self, idx: int) -> bool:
        if not self.enabled:
            return False
        # 退化态统一切 pro（P0/P2 当前场；P3 全部）
        lvl = self.degrade.level
        if lvl >= 3:
            return True
        return idx in self._pro_idx

    def concurrency(self, base: int = 4) -> int:
        if not self.enabled:
            return base
        lvl = self.degrade.level
        if lvl >= 2:
            return 1
        if lvl == 1:
            return 2
        return base

    @property
    def pending_cooldown(self) -> bool:
        return bool(self._cooldown_queue)

    def take_cooldown(self) -> int:
        return int(self._cooldown_queue.pop(0)) if self._cooldown_queue else 0

    @property
    def plan(self) -> dict:
        return self._plan or {"whole_chapter_pro": False, "pro_scene_count": 0,
                              "degraded_scene_count": 0, "over_budget": False,
                              "budget_status": "DISABLED", "window_projected": 0}

    # ---- 结果观测（归一化事件 -> 退化窗口 -> 冷却队列）----
    def feed_result(self, *, reasoning_only: bool = False, short: bool = False,
                    waste: bool = False, valid: bool = False, now: float = 0.0):
        if not self.enabled:
            return None
        with self.lock:
            action = None
            if reasoning_only:
                action = self.degrade.observe_call(True, now=now)
                self._chapter_ctx["reasoning_only_total"] += 1
                self._recompute()
            elif short:
                action = self.degrade.observe_scene(True, now=now)
            elif waste:
                action = self.degrade.observe_parse(True, now=now)
            elif valid:
                action = self.degrade.observe_valid_draft(now=now)
            if action and action.get("cooldown_seconds"):
                self._cooldown_queue.append(int(action["cooldown_seconds"]))
            return action
