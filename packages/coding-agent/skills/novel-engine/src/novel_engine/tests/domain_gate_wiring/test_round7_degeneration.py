# -*- coding: utf-8 -*-
"""CC round-7 P0-2 degeneration helpers."""
from novel_engine.quality.degeneration import find_self_repetition, is_near_empty


def test_self_repetition_detects_duplicated_block():
    block = "他提起油灯推开木门冷雾扑面而来带着陈年霉味"  # 20+ chars
    text = block + "，四周寂静。\n\n" + block + "，他心头一紧。"
    hit, sample = find_self_repetition(text, n=20)
    assert hit is True
    assert len(sample) == 20


def test_self_repetition_clean_text_false():
    text = (
        "他穿过雾隐村外那片终年不散的浓雾，脚下碎石被夜露打得微湿。"
        "远处传来一两声犬吠，随即又被厚重的寂静吞没。他把怀里的婴儿裹得更紧了些。"
    )
    hit, _ = find_self_repetition(text, n=20)
    assert hit is False


def test_short_text_no_false_positive():
    assert find_self_repetition("夜雾深沉。", n=20)[0] is False


def test_is_near_empty_thresholds():
    # target 2000 -> rescue floor max(2000*0.15,150)=300
    assert is_near_empty("短。", 2000) is True
    assert is_near_empty("x" * 400, 2000) is False
    # very small target still enforces the 150 hard floor
    assert is_near_empty("x" * 100, 500) is True
    assert is_near_empty("x" * 160, 500) is False
