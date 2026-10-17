# -*- coding: utf-8 -*-
"""修复 ch524 根因：中文借用词豁免 + 修复 prompt 点名泄漏词。

ch524 FAILED 归因：正文"呈V字形的裂隙"（合理中文借用词）被 _LATIN_RE
误判违规，LLM 修复 3 次因判断该词合理而保留 → fail-closed 硬门拒绝整章。
修复：
1) detect_latin_leak 豁免「拉丁词+中文借用后缀」（V字形/U型/T恤/X光）；
   孤立拉丁词（Scene/decision/孤立 V）仍判违规。
2) fix_latin_leak 的修复 prompt 点名泄漏词，明确 LLM 必须处理的目标，
   避免修复模型自行判断"合理"而保留。
hermetic：纯函数断言 + stub LLM，零网络。
"""
from __future__ import annotations

from novel_engine.quality.chinese_gate import (
    detect_latin_leak,
    fix_latin_leak,
    is_chinese_clean,
)


class _StubLLM:
    """捕获 prompt 并返回给定文本的 LLM 替身。"""

    def __init__(self, fixed_text: str):
        self.fixed_text = fixed_text
        self.calls: list[list[dict]] = []

    def chat_completion(self, messages, max_tokens=None):
        self.calls.append(list(messages))
        return {"content": self.fixed_text}


# --- 借用词豁免 ---

def test_borrow_word_v_shape_clean():
    """V字形（ch524 原句）为中文借用词，不判违规。"""
    text = "背部撞击在巨岩凹陷处，那是一块呈V字形的裂隙。"
    assert detect_latin_leak(text) == []
    assert is_chinese_clean(text)


def test_borrow_word_various_suffixes_clean():
    """U型/T恤/X光 等常见借用词均豁免。"""
    for text in ("他绕开U型弯道。", "他套上一件T恤。", "X光片摊在桌上。"):
        assert detect_latin_leak(text) == [], text


def test_isolated_latin_still_flagged():
    """孤立拉丁词/整词英文仍判违规，豁免不扩大化。"""
    assert detect_latin_leak("混入一段 Scene 字样") == ["Scene"]
    assert detect_latin_leak("孤立 V 字母仍违规") == ["V"]
    assert detect_latin_leak("他暗暗下了决心，he decision 不对外透露。") == ["he", "decision"]


def test_borrow_word_does_not_exempt_elsewhere():
    """借用词豁免只作用于原文紧邻后缀的位置。"""
    text = "V字形旁边还写着一个 V"
    assert detect_latin_leak(text) == ["V"], "孤立的第二个 V 仍应违规"


# --- 修复 prompt 点名泄漏词 ---

def test_fix_prompt_names_leaked_words():
    """修复 prompt 必须点名泄漏词，防止修复模型自行保留合理表达。"""
    stub = _StubLLM("他暗暗下了决心，不对外透露。")
    fixed, ok, reason = fix_latin_leak(
        "他暗暗下了决心，he decision 不对外透露。", stub, max_retries=0
    )
    assert ok and reason == ""
    assert fixed == "他暗暗下了决心，不对外透露。"
    prompt = stub.calls[0][-1]["content"]
    assert "he" in prompt and "decision" in prompt, \
        "prompt must name the leaked words, got: " + prompt


def test_fix_verify_trust_still_fail_closed():
    """修复后复检仍泄漏 → 返回原文与失败，fail-closed 不变。"""
    stub = _StubLLM("他下定决心，但 V 还是留着。")
    fixed, ok, reason = fix_latin_leak("V 出现在开头。", stub, max_retries=0)
    assert not ok and reason == "post_fix_verify_failed"
    assert fixed == "V 出现在开头。", "fail-closed 必须返回原文不放行"
