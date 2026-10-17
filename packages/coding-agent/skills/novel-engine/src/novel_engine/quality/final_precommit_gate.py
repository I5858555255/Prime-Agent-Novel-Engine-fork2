# -*- coding: utf-8 -*-
"""CC round-19 Q1：提交前统一终检（fail-closed）。

在 current_novel 即将写入 chapter_N.txt / 进入 COMMIT 之前，对**这同一份最终字节串**
重跑零成本确定性文本完整性谓词，杜绝"门对内存态某时点副本检测、缺陷却落盘"的脱节。

违规分两类（处置由编排层决定）：
- immediate（拉丁泄漏）：终检层不再重生，直接隔离转人工（能漏到这里=上游门失效）。
- repairable（标点流水句 / 脚手架指令头 / 半角标点）：允许修复 1 次后在同一份文本复检。

本模块只放纯规则函数，零 LLM、零 I/O 副作用，便于离线单测。
"""
import re

from novel_engine.quality import latin_leak_gate, punctuation_health

# 我方系统插入的章节标题（唯一允许保留的行首 markdown 标题）
_OUR_TITLE_RE = re.compile(r"^\s*#\s*第[一二三四五六七八九十百千零0-9]+章")

# 半角 -> 全角标点
_HALF_TO_FULL = {
    ",": "，",
    ".": "。",
    ":": "：",
    ";": "；",
    "?": "？",
    "!": "！",
}

_PUNCT_CHARS = set(",.:;?!")


def _is_cjk(ch: str) -> bool:
    return bool(ch) and "一" <= ch <= "鿿"


def find_instruction_headers(text: str) -> list[str]:
    """返回所有应被剥离的行首 markdown 指令/标题行（我方章节标题除外）。"""
    out = []
    for line in (text or "").split("\n"):
        s = line.strip()
        if s.startswith("#") and not _OUR_TITLE_RE.match(s):
            out.append(s)
    return out


def strip_instruction_headers(text: str) -> str:
    """确定性剥离行首 markdown 指令行（保留我方 '# 第N章' 标题）。"""
    kept = []
    for line in (text or "").split("\n"):
        s = line.strip()
        if s.startswith("#") and not _OUR_TITLE_RE.match(s):
            continue
        kept.append(line)
    return "\n".join(kept)


def _halfwidth_in_cjk_context(text: str, idx: int) -> bool:
    """半角标点是否处于中文上下文：前后任一字符为 CJK，且不是纯数字内标点
    （小数点 / 千分位 / 时分秒 / 比例的 12.5 / 1,000 / 12:30 不动）。"""
    ch = text[idx]
    prev_c = text[idx - 1] if idx > 0 else ""
    next_c = text[idx + 1] if idx + 1 < len(text) else ""
    # 两侧都是数字 -> 数字内标点，保留半角
    if prev_c.isdigit() and next_c.isdigit():
        return False
    # 紧邻 CJK 即判中文上下文
    return _is_cjk(prev_c) or _is_cjk(next_c)


def find_halfwidth_punctuation(text: str, limit: int = 20) -> list[dict]:
    """检出中文正文中误用的半角标点（排除数字内标点）。返回 [{char,index}]。"""
    hits = []
    for i, ch in enumerate(text or ""):
        if ch in _PUNCT_CHARS and _halfwidth_in_cjk_context(text, i):
            hits.append({"char": ch, "index": i})
            if len(hits) >= limit:
                break
    return hits


def normalize_halfwidth_punctuation(text: str) -> tuple[str, int]:
    """把中文上下文中的半角标点确定性转成全角；数字内标点保持半角。返回 (新文本, 替换数)。"""
    if not text:
        return text, 0
    chars = list(text)
    n = 0
    for i, ch in enumerate(chars):
        if ch in _HALF_TO_FULL and _halfwidth_in_cjk_context(text, i):
            chars[i] = _HALF_TO_FULL[ch]
            n += 1
    return "".join(chars), n


def evaluate_final_text(text: str) -> dict:
    """对最终成稿文本跑全部文本完整性谓词。零 LLM。

    返回 {"clean": bool, "violations": [{"kind","detail"}]}
    kind: latin_leak(immediate) / punctuation / scaffolding_header / halfwidth_punct
    """
    violations: list[dict] = []

    # 1) 拉丁字母泄漏（immediate 类）
    lat = latin_leak_gate.detect_latin_leak(text or "") or {}
    if lat.get("has_leak"):
        toks = lat.get("leaked_tokens") or []
        violations.append({"kind": "latin_leak",
                           "detail": f"拉丁字符 {len(toks)} 处，样例={toks[:12]}"})

    # 2) 标点不健康（流水句）
    try:
        bad_paras = punctuation_health.check_text_punctuation(text or "") or []
    except Exception:
        bad_paras = []
    for b in bad_paras:
        violations.append({"kind": "punctuation",
                           "detail": f"para#{b.get('para_index')} 最长无句读={b.get('max_unpunctuated_run')} "
                                     f"密度={b.get('punct_density_per_100')}"})

    # 3) 脚手架指令头
    headers = find_instruction_headers(text or "")
    if headers:
        violations.append({"kind": "scaffolding_header",
                           "detail": f"指令/标题行 {len(headers)} 条，样例={headers[:3]}"})

    # 4) 半角标点
    hw = find_halfwidth_punctuation(text or "")
    if hw:
        sample = sorted({h["char"] for h in hw})
        violations.append({"kind": "halfwidth_punct",
                           "detail": f"半角标点 {len(hw)} 处，种类={sample}"})

    return {"clean": not violations, "violations": violations}


IMMEDIATE_KINDS = {"latin_leak"}
REPAIRABLE_KINDS = {"punctuation", "scaffolding_header", "halfwidth_punct"}
