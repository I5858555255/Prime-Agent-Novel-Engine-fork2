# -*- coding: utf-8 -*-
"""CC28 批B：场景写入前语言纯度硬闸（零 LLM）单测。"""
from novel_engine.quality.scene_language_gate import (
    assess_scene_language, is_degenerate_non_chinese)
from novel_engine.pipeline import chapter_journal as cj
from novel_engine.pipeline.chapter_journal import append_scene, load_scenes

# 复刻 r41 ch0 scene2 的退化输出形态：纯英文同义词词流
ENGLISH_WATERFALL = (
    "simulate counterfeit fake forgery fraud cheat swindle con trick deceive delude "
    "mislead misinform bewitch enchant captivate charm fascinate mesmerize hypnosis "
    "hypnotism somnambulism sleepwalking dream dreaming daydream fantasize imagine "
    "fancy suppose consider contemplate ponder reflect muse brood cogitate think "
    "meditate deliberate") * 3

CHINESE_PROSE = (
    "浓雾在老松树的根须间缓缓流动，带着泥土与腐叶的潮气。"
    "襁褓里的婴孩啼哭了几声，便被一只粗糙而温暖的大手抱起。"
    "铜锣声从村子方向传来，子时已到，山风掠过林梢，惊起几只夜鸟。") * 4


def test_english_waterfall_is_degenerate():
    v = assess_scene_language(ENGLISH_WATERFALL)
    assert v["is_degenerate"] is True
    assert v["cjk_ratio"] < 0.1
    assert is_degenerate_non_chinese(ENGLISH_WATERFALL)


def test_normal_chinese_prose_not_degenerate():
    assert is_degenerate_non_chinese(CHINESE_PROSE) is False


def test_chinese_with_small_english_fragment_not_degenerate():
    # 零星英文残片交 latin_leak_gate 组装后清理，不在写入层判退化
    text = CHINESE_PROSE + "他压低声音说这件事 he decision 不对外透露，旁人只当没听见。" * 2
    v = assess_scene_language(text)
    assert v["is_degenerate"] is False


def test_short_english_text_not_flagged():
    # 短文本不做比例退化判定，且无超长拉丁词流
    assert is_degenerate_non_chinese("hello world") is False


def test_append_scene_rejects_waterfall_when_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_ENGINE_REAL_LANG_ENFORCE", "1")
    ok = append_scene(tmp_path, 0, {"scene_id": 1, "scene_text": ENGLISH_WATERFALL})
    assert ok is False
    assert load_scenes(tmp_path, 0) == []  # 退化输出不得落盘


def test_append_scene_accepts_chinese_when_enforced(tmp_path, monkeypatch):
    monkeypatch.setenv("NOVEL_ENGINE_REAL_LANG_ENFORCE", "1")
    ok = append_scene(tmp_path, 0, {"scene_id": 1, "scene_text": CHINESE_PROSE})
    assert ok is True
    recs = load_scenes(tmp_path, 0)
    assert len(recs) == 1 and recs[0]["scene_text"] == CHINESE_PROSE


def test_append_scene_default_off_keeps_primitive_behavior(tmp_path, monkeypatch):
    monkeypatch.delenv("NOVEL_ENGINE_REAL_LANG_ENFORCE", raising=False)
    # 离线默认不启用语言闸：原语对任意文本照常写入（向后兼容，单字 mock 也安全）
    assert append_scene(tmp_path, 3, {"scene_id": 1, "scene_text": "a"}) is True
    assert append_scene(tmp_path, 3, {"scene_id": 2, "scene_text": ENGLISH_WATERFALL}) is True
    assert len(load_scenes(tmp_path, 3)) == 2


def test_explicit_enforce_override(tmp_path):
    # 无 env 时也可用显式参数强制启用
    ok = append_scene(tmp_path, 7, {"scene_id": 1, "scene_text": ENGLISH_WATERFALL},
                      enforce_language=True)
    assert ok is False and load_scenes(tmp_path, 7) == []
