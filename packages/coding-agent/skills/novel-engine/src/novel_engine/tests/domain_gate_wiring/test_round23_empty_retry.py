# -*- coding: utf-8 -*-
"""CC round-23 P0-3：摆烂空稿跨温度重试温度与指令（纯函数离线测试）。"""
from novel_engine.quality.scene_resilience import (
    degenerate_retry_temperature, empty_scene_retry_directive)


def test_degenerate_temperature_escalates_then_caps():
    assert degenerate_retry_temperature(0) == 0.7
    assert degenerate_retry_temperature(1) == 0.85
    assert degenerate_retry_temperature(2) == 0.85  # 封顶 0.85，不升到 1.0
    assert degenerate_retry_temperature(99) == 0.85


def test_degenerate_temperature_invalid_input_defaults_first():
    assert degenerate_retry_temperature("x") == 0.7
    assert degenerate_retry_temperature(None) == 0.7


def test_empty_directive_zero_length():
    d = empty_scene_retry_directive(0, 3)
    assert "空白" in d and "0字" in d and "完整正文" in d and "3 个情节点" in d


def test_empty_directive_short_length():
    d = empty_scene_retry_directive(34, 4)
    assert "34 字" in d and "4 个情节点" in d and "完整正文" in d
