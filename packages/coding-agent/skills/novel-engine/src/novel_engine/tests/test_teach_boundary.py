# -*- coding: utf-8 -*-
"""CC 2026-10-06: 传授既成 vs 提及/认知 边界回归（ch59/ch60 实证来源）。

- ch59 L56（体感+传授既成）必须判违规
- ch60 L234（"您教我……呼吸的法子"传授既成）必须判违规
- 认知句（"陆烬知道根伯会些调息的法子"）必须放行
- 纯提及句（"陈老根提起过呼吸法"无传授/无体感）必须放行
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.quality.scope_gate import _has_teach_violation


def test_ch59_teach_violation():
    """ch59：体感描写 + 传授既成（"教他呼吸法时"）→ 违规。"""
    s = "陆烬能感觉到，根伯说话时气息的走向与平日教他呼吸法时那种奇特的平稳节奏隐隐相合"
    assert _has_teach_violation(s) is True


def test_ch60_teach_violation():
    """ch60：传授既成（"您教我……呼吸的法子"——方法已交付陆烬）→ 违规。"""
    s = "您教我认字，教我那些呼吸的法子，还有辨认药草。"
    assert _has_teach_violation(s) is True


def test_cognition_mention_allowed():
    """认知/提及（"知道根伯会些法子"——被告知结果，无传授动词）→ 放行。"""
    s = "陆烬知道根伯会些调息的法子"
    assert _has_teach_violation(s) is False


def test_pure_mention_allowed():
    """纯提及（陈老根提及呼吸法，无"教/传"动词、无陆烬体感）→ 放行。"""
    s = "陈老根偶尔会说起一些调息的法子，但从不细讲"
    assert _has_teach_violation(s) is False
