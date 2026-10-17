# -*- coding: utf-8 -*-
"""ch292 归因回归：review_unstable 低分章必须接入 A 类整章重写而非直接强制人工。

背景：ch292（5258 字严重短章）评审 4 票 [37.8,29.0,47.5,57.0] 不收敛、score=42.65，
原 review_unstable 分支在 A 类低分升级之前 return，绕过已验证有效的整章重写路径
（ch299 rewrite→fix 有效）。修复：unstable + 0<score<floor + 升级启用 → 先重写。
"""
from __future__ import annotations

from novel_engine.pipeline.pipeline_orchestrator import _unstable_upgrade_eligible

_DEFAULT_POLICY = {"low_score_rewrite": {"enabled": True, "floor": 60}, "fix_threshold": 60}


def test_unstable_low_score_upgrades():
    """unstable + 低分 + 启用 → 应走 A 类整章重写（不直接人工）。"""
    assert _unstable_upgrade_eligible(_DEFAULT_POLICY, 42.65) is True


def test_unstable_default_policy_upgrades():
    """policy 为空时默认启用（enabled=True, floor=60），低分 unstable 应升级。"""
    assert _unstable_upgrade_eligible({}, 42.65) is True


def test_unstable_high_score_does_not_upgrade():
    """unstable 但高分（>=floor）→ 保持原设计直接强制人工。"""
    assert _unstable_upgrade_eligible(_DEFAULT_POLICY, 68.0) is False


def test_unstable_upgrade_disabled_keeps_human():
    """A 类升级被显式关闭 → 维持强制人工（不绕过配置）。"""
    cfg = {"low_score_rewrite": {"enabled": False, "floor": 60}, "fix_threshold": 60}
    assert _unstable_upgrade_eligible(cfg, 42.65) is False


def test_unstable_nonpositive_score_no_upgrade():
    """score<=0 不触发（0/负分走其他失败路径，不误进重写）。"""
    assert _unstable_upgrade_eligible(_DEFAULT_POLICY, 0) is False
    assert _unstable_upgrade_eligible(_DEFAULT_POLICY, -1) is False


def test_unstable_custom_floor_respected():
    """自定义 floor 生效：score 低于自定义 floor 才升级。"""
    cfg = {"low_score_rewrite": {"enabled": True, "floor": 50}, "fix_threshold": 60}
    assert _unstable_upgrade_eligible(cfg, 45.0) is True
    assert _unstable_upgrade_eligible(cfg, 55.0) is False
