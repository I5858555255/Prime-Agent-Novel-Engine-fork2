# -*- coding: utf-8 -*-
"""CC round-18：拉丁字母泄漏门 + 修订后长度三分支判定。"""
from novel_engine.quality.latin_leak_gate import detect_latin_leak, latin_leaks_by_scene
from novel_engine.quality.post_fix_length import post_fix_length_decision


# ---------- 拉丁泄漏 ----------

def test_real_latin_leak_detected():
    # 真机 ch2 scene4 原句
    r = detect_latin_leak("他暗暗下了决心，he decision 不对外透露。")
    assert r["has_leak"] is True
    assert "he" in r["leaked_tokens"]
    assert "decision" in r["leaked_tokens"]


def test_dialogue_not_exempt_from_latin():
    r = detect_latin_leak("他沉声道：“这事很 OK，你放心。”")
    assert r["has_leak"] is True


def test_clean_chinese_and_arabic_digits_pass():
    assert detect_latin_leak("陈老根顿了顿枣木棍，笃的一声，3 名村民退后半步。")["has_leak"] is False
    # 单字母不判
    assert detect_latin_leak("他比了个 A 字形的手势。")["has_leak"] is False


def test_latin_leaks_by_scene():
    scenes = [
        {"scene_id": 1, "scene_text": "全中文的一段，没有任何外文。"},
        {"scene_id": 2, "scene_text": "忽然冒出一个 token 残片。"},
    ]
    out = latin_leaks_by_scene(scenes)
    assert [x["scene_id"] for x in out] == [2]


# ---------- 修订后长度 ----------

def test_length_ok():
    assert post_fix_length_decision(7000)["status"] == "ok"


def test_length_needs_topup_band():
    # 6800*0.85=5780；6086 在可自动回补区间
    r = post_fix_length_decision(6086)
    assert r["status"] == "needs_topup"
    assert r["gap"] == 714


def test_length_severe_shortfall():
    # 真机 ch2 4675 < 5780 → 转人工、游标继续
    r = post_fix_length_decision(4675)
    assert r["status"] == "severe_shortfall"
    assert r["topup_line"] == 5780


def test_length_boundary_inclusive():
    assert post_fix_length_decision(5780)["status"] == "needs_topup"
    assert post_fix_length_decision(6800)["status"] == "ok"
