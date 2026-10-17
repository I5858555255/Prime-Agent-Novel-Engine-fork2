# -*- coding: utf-8 -*-
"""CC28 批B：场景写入前语言纯度硬闸（零 LLM）。

r41 ch0 scene2 出现一条 9855 字的纯英文“词汇瀑布”（simulate/counterfeit/fraud/
deceive/hypnotism/meditate… 同义词链），是模型的退化输出、完全不是中文正文，
却被 append_scene 写进 journal，事后 latin 门清理又在 E-loop best 快照回流时再次
污染成稿，最终提交前终检阻断、整章进人验队列。

本模块只在【写入层】判定“这一场根本不是中文正文”的严重退化，判废即不写 journal、
由上游重试；它不处理零星英文碎片（那仍由 latin_leak_gate 在组装后清理），因此阈值
刻意保守，避免误伤正常中文里夹带一两个英文词的情况。
"""
from __future__ import annotations

import re

# 仅当场景非空白字符达到该体量才启用“占比”判定（短文本不参与比例退化判定）
MIN_CHARS_FOR_RATIO = 200
# 连续拉丁单词（空格分隔也连算）的字母数达到该值 → 直接判退化（词汇瀑布特征）
DEGEN_LATIN_RUN_LETTERS = 40
# 非空白字符中拉丁字母占比 ≥ 此值，且中文占比 < CJK_RATIO_MAX → 判退化
DEGEN_LATIN_RATIO = 0.25
DEGEN_CJK_RATIO_MAX = 0.5
# 拉丁单词个数 ≥ 此值且中文占比很低 → 判退化（成串英文词）
DEGEN_LATIN_WORD_COUNT = 12
DEGEN_CJK_RATIO_LOW = 0.35

_CJK_RE = re.compile(r"[一-鿿]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z]")
_LATIN_WORDS_RE = re.compile(r"[A-Za-z]{2,}")
# 连续拉丁单词序列：单词之间允许空格/连字符/撇号，整体作为一条“词流”，统计其中字母数
_LATIN_WORDSTREAM_RE = re.compile(r"[A-Za-z]+(?:[\s'\-]+[A-Za-z]+)*")


def _latin_wordstream_max_letters(text: str) -> int:
    best = 0
    for m in _LATIN_WORDSTREAM_RE.finditer(text or ""):
        letters = len(_LATIN_LETTER_RE.findall(m.group(0)))
        if letters > best:
            best = letters
    return best


def assess_scene_language(text: str) -> dict:
    """返回场景语言纯度判定。

    is_degenerate=True 表示“这不是中文正文”（英文词汇瀑布/大段非中文），应在写入层
    直接判废重试，而不是写进 journal。短文本、纯中文、或仅夹带零星英文词都不算退化。
    """
    t = text or ""
    nonspace = [ch for ch in t if not ch.isspace()]
    n = len(nonspace)
    latin_letters = len(_LATIN_LETTER_RE.findall(t))
    cjk_chars = len(_CJK_RE.findall(t))
    latin_words = len(_LATIN_WORDS_RE.findall(t))
    max_ws_letters = _latin_wordstream_max_letters(t)

    latin_ratio = (latin_letters / n) if n else 0.0
    cjk_ratio = (cjk_chars / n) if n else 0.0

    reasons = []
    if max_ws_letters >= DEGEN_LATIN_RUN_LETTERS:
        reasons.append(f"latin_wordstream_{max_ws_letters}>={DEGEN_LATIN_RUN_LETTERS}")
    if n >= MIN_CHARS_FOR_RATIO:
        if latin_ratio >= DEGEN_LATIN_RATIO and cjk_ratio < DEGEN_CJK_RATIO_MAX:
            reasons.append(f"latin_ratio_{latin_ratio:.2f}>= {DEGEN_LATIN_RATIO} "
                           f"& cjk_{cjk_ratio:.2f}<{DEGEN_CJK_RATIO_MAX}")
        if latin_words >= DEGEN_LATIN_WORD_COUNT and cjk_ratio < DEGEN_CJK_RATIO_LOW:
            reasons.append(f"latin_words_{latin_words}>={DEGEN_LATIN_WORD_COUNT} "
                           f"& cjk_{cjk_ratio:.2f}<{DEGEN_CJK_RATIO_LOW}")

    return {
        "chars": n,
        "latin_letters": latin_letters,
        "latin_words": latin_words,
        "cjk_ratio": round(cjk_ratio, 3),
        "latin_ratio": round(latin_ratio, 3),
        "max_latin_wordstream_letters": max_ws_letters,
        "is_degenerate": bool(reasons),
        "reasons": reasons,
    }


def is_degenerate_non_chinese(text: str) -> bool:
    return assess_scene_language(text)["is_degenerate"]
