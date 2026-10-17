# -*- coding: utf-8 -*-
"""前世揭示尺度检测（reveal_scale_gate）纯函数测试。hermetic，零 LLM。"""
from __future__ import annotations

from novel_engine.quality.reveal_scale_gate import detect_past_life_reveal_scale


def test_window_with_3_detail_terms_flags_high():
    """150 字窗口内出现 >=3 个前世具象细节词 → 揭示尺度偏高。"""
    text = (
        "陆烬在病床上渐渐咽气，白墙四壁，吊瓶里的液体一滴一滴落下，"
        "前世最后的弥留记忆如潮水般涌来，他看见护士的身影。"
    )
    result = detect_past_life_reveal_scale(text)
    assert result["reveal_scale_high"] is True
    assert result["max_window_hits"] >= 3


def test_sparse_terms_not_flagged():
    """词分散在不同窗口（单窗口 <3 词）→ 不标记。"""
    text = (
        "陆烬今日照常练功。" + "山" * 160 + "前世的事他很少再想。"
        + "山" * 160 + "病床上的事更是无人知晓。"
    )
    result = detect_past_life_reveal_scale(text)
    assert result["reveal_scale_high"] is False


def test_isolated_single_term_not_flagged():
    """单章仅出现一个前世词（无细节堆叠）→ 不标记。"""
    text = "陆烬偶尔会想起前世的一些模糊画面，但没有更多细节。"
    result = detect_past_life_reveal_scale(text)
    assert result["reveal_scale_high"] is False


def test_empty_text_passes():
    assert detect_past_life_reveal_scale("")["reveal_scale_high"] is False
    assert detect_past_life_reveal_scale(None)["reveal_scale_high"] is False


def test_custom_terms_and_threshold():
    """自定义词表/阈值生效（如放宽或收紧均可用）。"""
    text = "他想起前世，上辈子，上一世的画面。"
    # 默认词表：3 词同窗 → high
    assert detect_past_life_reveal_scale(text)["reveal_scale_high"] is True
    # 提高阈值到 5 → 不标记
    assert detect_past_life_reveal_scale(
        text, detail_hits=5)["reveal_scale_high"] is False


def test_window_report_structure():
    """超阈窗口包含 start/hit_count/hits，最多 5 条。"""
    text = (
        "病床 抢救 心电图 前世。" + "山" * 300 + "病床 抢救 心电图 上辈子。"
        + "山" * 300 + "病床 抢救 心电图 上一世。" + "山" * 300
        + "病床 抢救 心电图 重生前。" + "山" * 300 + "病床 抢救 心电图 弥留。"
        + "山" * 300 + "病床 抢救 心电图 临终。"
    )
    result = detect_past_life_reveal_scale(text)
    assert result["reveal_scale_high"] is True
    assert len(result["windows"]) <= 5
    assert all("start" in w and "hit_count" in w and "hits" in w
               for w in result["windows"])
