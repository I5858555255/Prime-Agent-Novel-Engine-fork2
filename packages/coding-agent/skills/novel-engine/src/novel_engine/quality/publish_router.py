# -*- coding: utf-8 -*-
"""CC28/批D（DS 裁决第5点）：无人值守分层发布路由（纯规则，零 LLM）。

以 CC24 混合分（review_hybrid 混合后的归一化 0-100）+ 确定性硬门状态为权威：
- 硬门红：无论分多高都不自动提交（交修复/E-loop/人验）。
- >= review_free_line(85) 且硬门绿：免审自动提交。
- [auto_submit_line(82), 85) 且硬门绿：入已提交池 + 轻量抽检（按章号确定性抽样，
  默认比例 10%-15%，抽检命中只标记人验、不阻断发布）。
- < auto_submit_line：不直接转人工，先走 best-of/确定性修复（DifficultyQueue 难章）；
  连续 K 章仍 < 线由 dynamic_writer_upgrade 决定是否按章升级 pro writer。

判定纯函数化、可离线单测；采样用章号哈希实现，保证同一章结论稳定、不依赖随机态。
"""
from __future__ import annotations

# 默认阈值（DS 裁决）；可由 runtime_config.quality_policy 覆盖。
DEFAULT_AUTO_SUBMIT_LINE = 82.0
DEFAULT_REVIEW_FREE_LINE = 85.0
DEFAULT_SAMPLE_RATE = 0.125   # 10%-15% 区间取中值 12.5%

TIER_HARD_RED = "hard_red"              # 硬门红：不可自动提交
TIER_REVIEW_FREE = "review_free"        # > =85 绿：免审提交
TIER_SAMPLE_AUDIT = "sample_audit"      # 82-85 绿：提交+抽检
TIER_BEST_OF_FIX = "best_of_fix"        # <82：先 best-of/确定性修复


def _f(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def deterministic_sample_hit(chapter_num: int, rate: float = DEFAULT_SAMPLE_RATE) -> bool:
    """按章号确定性抽样：同章结论稳定，跨章比例≈rate。用 (n*2654435761 mod 2^32)/2^32。"""
    r = min(max(_f(rate, DEFAULT_SAMPLE_RATE), 0.0), 1.0)
    if r <= 0.0:
        return False
    if r >= 1.0:
        return True
    n = int(chapter_num)
    h = (n * 2654435761) & 0xFFFFFFFF
    return (h / 0x100000000) < r


def decide_publish_tier(score, hard_gates_clean: bool, chapter_num: int = 0,
                        *, auto_submit_line: float = DEFAULT_AUTO_SUBMIT_LINE,
                        review_free_line: float = DEFAULT_REVIEW_FREE_LINE,
                        sample_rate: float = DEFAULT_SAMPLE_RATE) -> dict:
    """返回 {tier, auto_submit, needs_manual_audit, score,...}。

    auto_submit=True 表示可无人值守写入正稿（review_free / 未命中抽检的 sample_audit）；
    sample_audit 命中抽检时 auto_submit 仍为 True（先发布），但 needs_manual_audit=True。
    """
    s = _f(score, None)
    if s is None:
        return {"tier": TIER_HARD_RED, "auto_submit": False, "needs_manual_audit": True,
                "score": None, "reason": "score_unparseable"}
    if not bool(hard_gates_clean):
        return {"tier": TIER_HARD_RED, "auto_submit": False, "needs_manual_audit": True,
                "score": s, "reason": "deterministic_hard_gate_red"}
    if s >= review_free_line:
        return {"tier": TIER_REVIEW_FREE, "auto_submit": True, "needs_manual_audit": False,
                "score": s, "reason": "review_free"}
    if s >= auto_submit_line:
        hit = deterministic_sample_hit(chapter_num, sample_rate)
        return {"tier": TIER_SAMPLE_AUDIT, "auto_submit": True,
                "needs_manual_audit": bool(hit), "sample_hit": bool(hit),
                "score": s, "reason": "sample_audit_hit" if hit else "sample_audit_pass"}
    return {"tier": TIER_BEST_OF_FIX, "auto_submit": False, "needs_manual_audit": False,
            "score": s, "reason": "below_auto_line_best_of_fix"}
