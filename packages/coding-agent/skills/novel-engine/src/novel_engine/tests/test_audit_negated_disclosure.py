# -*- coding: utf-8 -*-
"""audit_mid_outline 两处确定性修复的 hermetic 测试。

1) R14 否定语境豁免：forbidden 规则命中"解读/用途"类词时，若紧邻前文是否定式
   （无法解读/未能参透…），视为未坐实解读，不得判 needs_regenerate
   （ch559/ch573 实证："翻出七窍碑残页…无法解读，暂且封存"）。
2) A 路线模式补词：陆烬体内"灵气/气机"同样属于修炼样式动作
   （ch581 实证："体内灵气波动"漏判，仅落 pending_human）。

零 LLM；decide_entry 纯函数直接判定；不触碰 config/planning 文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

from novel_engine.scripts.audit_mid_outline import (
    _A_ROUTE_PATTERNS,
    _load_decision_rules,
    _is_negated_disclosure,
    decide_entry,
)

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_RULES = _load_decision_rules()


def _decide(event: str, ch: int = 559) -> tuple[str, str, str | None]:
    return decide_entry(event, ["C001", "C002"], "", ch, _RULES)


def test_negated_disclosure_helper_true_for_negation():
    """否定语境识别：无法解读 / 未能参透真正用途 → True。"""
    assert _is_negated_disclosure("纹路古怪，无法解读，暂且封存")
    assert _is_negated_disclosure("指尖发麻却无法解读其全貌")
    assert _is_negated_disclosure("七窍碑残页，未能参透真正用途")
    assert _is_negated_disclosure("残卷上的秘密尚不能解读")


def test_negated_disclosure_helper_false_for_affirmation():
    """肯定语境识别：完成解读 / 真正用途 / 为阵法钥匙 → False（应命中 R14）。"""
    assert not _is_negated_disclosure("原来残页真正用途是开启阵法")
    assert not _is_negated_disclosure("解读出七窍碑残页的秘密")
    assert not _is_negated_disclosure("残卷为阵法钥匙")


def test_r14_negated_no_regenerate_ch559():
    """ch559 实证：翻出残页"无法解读，暂且封存" → 不因 R14 重生成。"""
    st, _, rid = _decide("陆烬整理旧物时翻出七窍碑残页，只觉纹路古怪，无法解读，暂且封存", 559)
    assert rid != "R14"
    assert st != "needs_regenerate"


def test_r14_negated_no_regenerate_ch573():
    """ch573 实证："触碰七窍碑残页…无法解读其全貌" → 不因 R14 重生成。"""
    st, _, rid = _decide("陆烬在整理皇室秘藏时触碰七窍碑残页，感到指尖发麻却无法解读其全貌", 573)
    assert rid != "R14"
    assert st != "needs_regenerate"


def test_r14_affirmative_still_regenerate():
    """肯定式解读仍须重生成：'残页真正用途是开启阵法' → R14 needs_regenerate。"""
    st, _, rid = _decide("陆烬看出七窍碑残页的真正用途是开启阵法", 559)
    assert st == "needs_regenerate" and rid == "R14"


def test_r14_partial_negation_still_regenerate():
    """仅否定修饰语不足以免除：'几乎解读出全貌'（非否定词）仍命中 R14。"""
    st, _, rid = _decide("陆烬几乎解读出七窍碑残页的全貌", 559)
    assert st == "needs_regenerate" and rid == "R14"


def test_a_route_internal_qi_pattern_extended():
    """A 路线模式库已补词：体内灵气/气机应被覆盖（ch581 实证）。"""
    joined_pat = "".join(p for p, _ in _A_ROUTE_PATTERNS)
    assert "灵气" in joined_pat and "气机" in joined_pat


def test_a_route_internal_qi_catches_ch581():
    """ch581 实证：'体内灵气波动' 触发 A 路线硬红线（陆烬修炼样式动作）。

    A 路线判定在 audit main() 分支（独立于 decision_rules 的 decide_entry），
    此处直接验证模式库第 3 条（体内气机类）能匹配该文本。
    """
    import re
    text = "陆烬在修炼门槛处停滞，体内灵气波动引发周围侍从异样反应"
    body_pat = _A_ROUTE_PATTERNS[2][0]  # 陆烬体内.{0,20}(运转|流转|共鸣|气感|气流|暖意|灵气|气机)
    assert re.search(body_pat, text) is not None


def test_other_rules_unaffected():
    """既有规则不受影响：R01 传授、R15 筑基、R08 断供类判定保持原行为。"""
    st, _, rid = _decide("陆烬回忆道：您教我认字，教我那些呼吸的法子", 60)
    assert st == "needs_regenerate" and rid == "R01"
    st, _, rid = _decide("陆烬终于突破至筑基境界", 500)
    assert st == "needs_regenerate" and rid == "R15"
