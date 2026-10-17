# -*- coding: utf-8 -*-
"""ch267/ch325 归因回归：正文整段英文（语言跑飞）必须有更强的确定性拦截。

背景：writer prompt 已有"严禁英文"，latin_leak_gate 检测残片并定点重生 1 次，但
ch267（63 处）/ch325（859 处整段英文 'The purple fog pressed against the outside
of his body blocking all...'）重生后仍残留 → 最终门拦截 FAILED。修复：①新增
detect_english_sentences（≥5 连续拉丁单词 = 完整英文句，区别于零星残片）；②gate
对英文整句用加码 directive（点名语言跑飞 + 真实英文样例）重生；③writer 三处 prompt
强化 100% 中文硬约束。零 LLM：检测纯函数；gate 用 stub orchestrator 验证 directive。
"""
from __future__ import annotations

import json
from typing import Any

from novel_engine.quality.latin_leak_gate import detect_english_sentences
from novel_engine.pipeline import gates as gates_mod

_ENGLISH_WATERFALL = ("The purple fog pressed against the outside of his body "
                      "blocking all light from the broken wall")
_CHINESE_PROSE = ("陈老根顿了顿枣木棍，笃的一声，三名村民退后半步，"
                  "谁也没敢再往前凑。")


def test_detect_english_sentences_hits_full_english():
    r = detect_english_sentences(_ENGLISH_WATERFALL)
    assert r["has_english_sentence"] is True
    assert "purple" in (r["sample"] or "")


def test_detect_english_sentences_allows_single_ok_word():
    r = detect_english_sentences("他沉声道：“这事很 OK，你放心。”")
    assert r["has_english_sentence"] is False


def test_detect_english_sentences_allows_pure_chinese_and_digits():
    assert detect_english_sentences(_CHINESE_PROSE)["has_english_sentence"] is False
    assert detect_english_sentences("陈老根数了数，3 名村民退后半步。")["has_english_sentence"] is False


class _Scene:
    def __init__(self, scene_id: int, scene_text: str):
        self.scene_id = scene_id
        self.scene_text = scene_text


class _StubOrchestrator:
    """鸭子类型 stub：记录 _regen_scene_for_id 收到的指令，重生为纯中文场景。"""

    def __init__(self, clean_text: str = _CHINESE_PROSE):
        self.config = {"llm": {"use_mock": False}}
        self.writer = type("W", (), {"last_scenes": []})()
        self._last_deterministic_issues: list[str] = []
        self._chapter_gate_fired = False
        self.human_flags: list[tuple] = []
        self.regen_calls: list[dict] = []
        self.clean_text = clean_text

    def _regen_scene_for_id(self, task_card, sid, scenes,
                            negative_examples=None, fix_directive=None,
                            frequency_penalty=0.0, presence_penalty=0.0):
        self.regen_calls.append({
            "sid": sid, "negative_examples": negative_examples,
            "fix_directive": fix_directive,
        })
        return _Scene(sid, self.clean_text)

    def _flag_for_human(self, chapter_num, score, reason):
        self.human_flags.append((chapter_num, score, reason))


def test_gate_english_sentence_uses_strong_directive_with_sample():
    """英文整句场景：gate 必须用"语言跑飞"级 directive 并携带真实英文样例重生。"""
    stub = _StubOrchestrator()
    scenes = [_Scene(1, _ENGLISH_WATERFALL), _Scene(2, _CHINESE_PROSE)]
    assembled = "The purple fog pressed against the outside of his body blocking all light"
    out = gates_mod._run_latin_leak_gate(
        stub, chapter_num=325, task_card={}, scenes=scenes, assembled_text=assembled)
    assert stub.regen_calls, "english-sentence scene must trigger targeted regen"
    call = stub.regen_calls[0]
    assert call["sid"] == 1
    directive = call["fix_directive"] or ""
    assert "语言跑飞" in directive, "english-sentence must use language-runaway directive"
    assert "purple" in directive, "directive must carry the real english sample"
    assert any("严禁" in (ne or "") for ne in (call["negative_examples"] or [])), \
        "negative examples must carry the forbidden english sample"
    # 重生为纯中文后复检通过：无 hard、返回净化正文
    assert stub._last_deterministic_issues == [], "no hard issue when regen is clean"
    assert stub.human_flags == []
    assert "purple" not in out.lower()


def test_gate_latin_fragment_keeps_default_directive():
    """零星残片场景：仍走原残片 directive（不含语言跑飞字样）。"""
    stub = _StubOrchestrator()
    scenes = [_Scene(1, "他暗暗下了决心，he decision 不对外透露。")]
    gates_mod._run_latin_leak_gate(
        stub, chapter_num=1, task_card={}, scenes=scenes,
        assembled_text="他暗暗下了决心，he decision 不对外透露。")
    assert stub.regen_calls
    directive = stub.regen_calls[0]["fix_directive"] or ""
    assert "语言跑飞" not in directive
    assert "英文字母/英文单词" in directive
