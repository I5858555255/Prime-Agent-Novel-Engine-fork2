# -*- coding: utf-8 -*-
"""CC round-22：三评极不稳定章按中位分路由（无人值守不中断）。"""
from novel_engine.quality.noncommit_routing import route_review_unstable


def test_below_softline_gap_continues():
    # r22 ch2：median=67.5、极差22.5 -> 不可出版，gap-continue，不 HALT
    assert route_review_unstable(67.5, soft_line=85) == "gap"
    assert route_review_unstable(72.5, soft_line=85) == "gap"
    assert route_review_unstable(84.9, soft_line=85) == "gap"


def test_at_or_above_softline_halts():
    # 近出版线却评审分歧 -> 保留 HALT 留人判，避免漏好章
    assert route_review_unstable(85, soft_line=85) == "halt"
    assert route_review_unstable(90, soft_line=85) == "halt"


def test_non_numeric_score_halts():
    assert route_review_unstable(None, soft_line=85) == "halt"
    assert route_review_unstable("N/A", soft_line=85) == "halt"
