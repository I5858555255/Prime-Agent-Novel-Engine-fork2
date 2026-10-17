# -*- coding: utf-8 -*-
"""CC round-12 R2：句末口径长句检测与零字改写安全断句测试。

覆盖需求：
- detect_long_sentences：旁白超长句检测、引号内对话豁免
- split_long_sentences：强边界前插"。\n"、对话内不动、无强边界只计数不拆
- 去标点逐字恒等校验
- 与 repair_text_punctuation 集成后最终文本健康
"""
import pytest

from novel_engine.quality import punctuation_health as ph


# ---------- detect_long_sentences ----------

def test_detect_long_narration_sentence():
    """旁白超长句（>=48中文字）应被检测出。"""
    text = "的" * 50
    result = ph.detect_long_sentences(text)
    assert len(result) == 1
    assert result[0]["cn_chars"] == 50
    assert result[0]["sentence_index"] == 0


def test_detect_short_sentence_not_flagged():
    """短于阈值的句子不应被检测出。"""
    text = "他走了。"
    result = ph.detect_long_sentences(text)
    assert result == []


def test_dialogue_inside_quotes_ignored():
    """引号内的长对话不应被检测为超长句。"""
    dialogue = "这是一段非常非常非常非常非常长的对话内容没有任何标点符号"
    text = f'他低声说："《{dialogue}》然后继续往前走。'
    result = ph.detect_long_sentences(text)
    assert result == []


def test_mixed_narration_and_dialogue():
    """旁白超长且夹带引号对话——旁白部分应被检测，对话部分豁免。"""
    long_narr = "的" * 50
    dialogue = '“这是一段很长的对话内容完全没有标点符号需要处理”'
    text = long_narr + dialogue
    result = ph.detect_long_sentences(text)
    assert len(result) == 1
    assert result[0]["cn_chars"] == 50


# ---------- split_long_sentences ----------

def test_split_at_strong_boundary():
    """旁白长句含强边界词——应在边界前插"。\n"。"""
    text = "的" * 50 + "然后" + "的" * 20
    fixed, stats = ph.split_long_sentences(text)
    assert stats["long_sentences_detected"] == 1
    assert stats["split_count"] >= 1
    assert "。\n" in fixed
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_no_strong_boundary_only_count():
    """无强边界的长句——只计数不拆分。"""
    text = "的" * 60
    fixed, stats = ph.split_long_sentences(text)
    assert stats["long_sentences_detected"] == 1
    assert stats["split_count"] == 0
    assert fixed == text


def test_dialogue_not_split():
    """纯对话长句——不应被检测也不应被拆分。"""
    text = '“' + "的" * 60 + '”'
    fixed, stats = ph.split_long_sentences(text)
    assert stats["long_sentences_detected"] == 0
    assert stats["split_count"] == 0
    assert fixed == text


def test_dialogue_preserved_after_split():
    """拆分后引号内对话内容完整保留。"""
    dialogue = '“这是很长的一段对话内容完全没有标点符号需要处理”'
    narration = "的" * 50 + "然后" + "的" * 20
    text = narration + dialogue
    fixed, stats = ph.split_long_sentences(text)
    assert dialogue in fixed
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_max_splits_per_sentence():
    """每句至多3处拆分（max_splits=3，P8B升级）。"""
    text = ("的" * 50 + "然后" + "的" * 20
            + "接着" + "的" * 20 + "随后" + "的" * 20)
    fixed, stats = ph.split_long_sentences(text)
    split_count = fixed.count("。\n")
    assert split_count <= 3
    assert stats["split_count"] <= 3


def test_weak_boundary_not_used_for_split():
    """弱边界词（因为/由于/虽然…）不应触发句号拆分。"""
    text = "的" * 50 + "因为" + "的" * 20
    fixed, stats = ph.split_long_sentences(text)
    assert "。\n" not in fixed
    assert stats["split_count"] == 0


# ---------- repair_text_punctuation integration ----------

def test_repair_text_punctuation_resolves_long_sentences():
    """repair_text_punctuation 对含长句的文本应检测并拆分，最终健康。"""
    text = "的" * 50 + "然后" + "的" * 20
    fixed, stats = ph.repair_text_punctuation(text)
    assert stats["long_sentences_detected"] >= 1
    assert ph.check_text_punctuation(fixed) == []
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_repair_text_punctuation_no_false_split():
    """健康文本不应被错误修改。"""
    text = "他走了很长一段路，然后停下来休息。"
    fixed, stats = ph.repair_text_punctuation(text)
    assert fixed == text
    assert stats["long_sentences_detected"] == 0


def test_repair_text_punctuation_preserves_dialogue():
    """修复过程中引号内对话不被触碰。"""
    dialogue = '“这是一段很长的对话内容完全没有标点符号需要处理”'
    text = "的" * 50 + dialogue
    fixed, stats = ph.repair_text_punctuation(text)
    assert dialogue in fixed
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


# ---------- CC round-12 R2c：引号占位符与换行硬句界 ----------

def test_dialogue_tags_do_not_merge_across_paragraphs():
    """多段对话标签夹旁白不得被拼成假长句（chr(0)+\\n 为硬句界）。"""
    text = (
        "旁白前段持续铺垫情绪氛围背景描述细节丰富。"
        '「第一句对话内容没有任何标点符号需要处理。」'
        "旁白后段接着继续叙述。"
        '「第二句对话内容。」'
        "最终旁白收尾。"
    )
    sents = ph.detect_long_sentences(text)
    # 每个旁白段单独检测，不应有跨段落拼接的假长句
    over_50 = [s for s in sents if s["cn_chars"] >= 50]
    # 每段旁白本身不超过 50 字（构造的短段）
    assert len(over_50) == 0, f"Expected no cross-para false long sentence, got {over_50}"


def test_quote_sentinel_does_not_trigger_split():
    """引号占位符 chr(0) 不得作为切分插入点（句号不会插在对话位置）。"""
    dialogue = '“这是一段很长的对话完全没有标点符号”'
    text = "的" * 25 + dialogue + "的" * 25 + "然后停下。"
    fixed, stats = ph.split_long_sentences(text)
    # 拆分只能在旁白区，不能插在引号占位符处
    assert "\x00。\n" not in fixed
    assert "。\n\x00" not in fixed
    # 去标点恒等
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_newline_also_hard_boundary():
    """换行符（含空行）为硬句界，不产生跨行拼接假长句。"""
    text = "的" * 20 + chr(10) + chr(10) + "的" * 20 + chr(10) + "然后停下。"
    sents = ph.detect_long_sentences(text)
    # 每段各自独立，不应合并
    over_50 = [s for s in sents if s["cn_chars"] >= 50]
    assert len(over_50) == 0


# ---------- CC round-12 R2c：gate 持久化回归 ----------

def test_split_long_sentences_persists_when_no_runon():
    """长句拆分后若 check_text_punctuation 干净，仍应保留拆分结果（模拟 gate C2）。

    真实 ch3 场景：逗号充分导致流水段检测为 healthy（bad=[]），但 split_long_sentences
    已成功拆分。C2 修复要求在此情况下持久化 sc.scene_text。
    """
    text = "的" * 25 + "，随即" + "的" * 30 + "，牢牢钉在原地。"
    ls_text, ls_stats = ph.split_long_sentences(text)
    assert ls_text != text, "split_long_sentences should have modified text"
    bad = ph.check_text_punctuation(ls_text)
    assert bad == [], "After split, text should be healthy (no run-on paragraphs)"
    # C2 场景：ls_text != text 且 bad == []，gate 必须持久化 ls_text
    assert ph.strip_all_punctuation(ls_text) == ph.strip_all_punctuation(text)
    assert "。" in ls_text
