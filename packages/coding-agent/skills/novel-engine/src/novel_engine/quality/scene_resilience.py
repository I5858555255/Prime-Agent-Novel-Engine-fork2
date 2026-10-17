# -*- coding: utf-8 -*-
"""CC round-15 scene 初稿韧性处理（零 LLM 纯规则，离线可测）。

把 Agnes flash 偶发的“摆烂/截断/夹带脚手架字段”从整章 HALT 降级为可自动救活的常规瑕疵：
- strip_leaked_tokens：剥离孤立泄漏到正文的脚手架 token（如裸 "C009"），引号对话/合理上下文豁免。
- classify_scene_output：L0 净化后把初稿分为 USABLE/UNDER_COVERED/DEGENERATE_SHORT/
  MALFORMED_SHORT/TRUNCATED/HARD_DEGENERATE，供编排决定放行扩写、续写或重生。
- usable_draft：P0-2 初稿放行组合条件（有效句≥5 + beat覆盖≥0.3 + 有句末 + 无泄漏）。
"""
from __future__ import annotations

import re

_ID_PATTERN = re.compile(r"[A-Z]\d{3,4}")
_SENT_SPLIT = re.compile(r"[。！？!?…]+")
_SENT_END = set("。！？…”.’」』）)!?~”’")
_CLOSE_QUOTE = set("\"'”’")
_LEGIT_CTX = ("编号", "代号", "序号", "代码", "代号为", "编号为", "ID", "id")
# 成对引号（中英文）
_QUOTE_PAIRS = [("“", "”"), ("‘", "’"), ('"', '"'), ("'", "'")]


def is_inside_quotes(text: str, pos: int) -> bool:
    """位置是否落在真正的对话引号内。

    泄漏字段常自带一个位于行首（空白行之后）的孤立开引号（如 …作响。\\n\\n"C009"），
    不算对话；只有“非行首、紧跟在冒号/逗号/上一句之后”的引号才开启对话区间。
    """
    state = {"curly": False, "dquote": False, "squote": False}
    for i, ch in enumerate(text):
        if i >= pos:
            break
        at_line_start = (i == 0) or text[i - 1] in "\n\r\t "
        if ch == "“":  # 中文开引号
            if not at_line_start:
                state["curly"] = True
        elif ch == "”":
            state["curly"] = False
        elif ch == "‘":
            if not at_line_start:
                state["curly"] = True
        elif ch == "’":
            state["curly"] = False
        elif ch == '"':
            if not state["dquote"] and not at_line_start:
                state["dquote"] = True
            else:
                state["dquote"] = False
        elif ch == "'":
            if not state["squote"] and not at_line_start:
                state["squote"] = True
            else:
                state["squote"] = False
    return any(state.values())


def has_legitimate_context(text: str, start: int, end: int) -> bool:
    head = text[max(0, start - 6):start]
    return any(k in head for k in _LEGIT_CTX)


def trim_to_last_sentence_ending(text: str) -> str:
    """截到最后一个句末边界；若全文无句末则原样返回（交给上层判 malformed）。"""
    t = text.rstrip()
    best = -1
    for i, ch in enumerate(t):
        if ch in _SENT_END:
            best = i
    if best >= 0:
        return t[:best + 1].rstrip()
    return text


def strip_leaked_tokens(text: str) -> tuple[str, bool]:
    """剥离孤立脚手架 token（默认 C009 类 ID）。返回 (cleaned, had_leak)。

    豁免：①处于成对引号对话内；②前面紧邻“编号/代号/序号”等指示词；
    ③与实质字词直接相连（如“样本C009”）。仅剥“句末/换行后孤立出现”的泄漏；剥后重定句末。
    """
    s = text or ""
    remove: list[tuple[int, int]] = []
    for m in _ID_PATTERN.finditer(s):
        st, en = m.start(), m.end()
        if is_inside_quotes(s, st) or has_legitimate_context(s, st, en):
            continue
        before = s[:st].rstrip()
        # 紧贴在实质字词之后（非句读/换行/引号衔接）视为正文用词，不剥；
        # 句末、换行、或泄漏字段自带的开引号之后 → 判为孤立
        if before and before[-1] not in "。！？!?…\n；;，,、\"'“‘":
            continue
        # 泄漏字段常以 JSON 字符串形式出现（"C009"）：把紧邻包裹的引号一并纳入删除区间
        lst = st
        while lst - 1 >= 0 and s[lst - 1] in "\"'“‘ \t\n\r":
            lst -= 1
        ren = en
        while ren < len(s) and s[ren] in "\"'”’ \t\n\r":
            ren += 1
        remove.append((lst, ren))
    if not remove:
        return s, False
    out_parts, last = [], 0
    for st, en in sorted(remove):
        out_parts.append(s[last:st])
        last = en
    out_parts.append(s[last:])
    cleaned = "".join(out_parts).strip()
    cleaned = trim_to_last_sentence_ending(cleaned)
    return cleaned, True


def has_proper_ending(text: str) -> bool:
    t = (text or "").rstrip()
    if not t:
        return False
    probe = t[:-1] if (t[-1] in _CLOSE_QUOTE and len(t) >= 2) else t
    return probe[-1] in _SENT_END


def count_effective_sentences(text: str) -> int:
    parts = [p for p in _SENT_SPLIT.split(text or "") if re.sub(r"[\s\W_]", "", p)]
    return len(parts)


def beat_coverage(text: str, beats: list[str]) -> float:
    """轻量 beat 覆盖估计：beat 关键词（去标点后≥2字的实义片段）在正文中出现的比例。

    beats 是短情节点标签而非逐字台词，用字符/关键词命中即可（与密度门的语义判定互补，
    此处只用于初稿“是否值得下游处理”的粗判，不做出版裁决）。
    """
    core = [re.sub(r"[\s\W_0-9a-zA-Z]", "", str(b)) for b in (beats or [])]
    core = [b for b in core if len(b) >= 2]
    if not core:
        return 1.0
    body = text or ""
    hit = 0
    for b in core:
        if b in body:
            hit += 1
            continue
        # 取 beat 中任意 2 字连续片段命中视作部分演到
        grams = {b[i:i + 2] for i in range(len(b) - 1)}
        if sum(1 for g in grams if g in body) >= max(1, len(grams) // 3):
            hit += 1
    return hit / len(core)


# 分级阈值（CC round-15）
HARD_DEGENERATE_CHARS = 80
MALFORMED_CHARS = 300
MIN_USABLE_SENTENCES = 5
MIN_USABLE_BEAT_COV = 0.3
L3_BEAT_COV = 0.3


def usable_draft(text: str, beats: list[str], had_leak: bool = False) -> bool:
    """P0-2 初稿组合放行：有效句≥5 + beat覆盖≥0.3 + 有正常句末 + 无字段泄漏。"""
    if had_leak:
        return False
    if not has_proper_ending(text):
        return False
    if count_effective_sentences(text) < MIN_USABLE_SENTENCES:
        return False
    if beat_coverage(text, beats) < MIN_USABLE_BEAT_COV:
        return False
    return True


def degenerate_retry_temperature(degenerate_attempt: int) -> float:
    """CC round-23 P0-3：0字/极短摆烂稿的跨温度快速重试温度。

    第 1 次退化重试 0.7，之后 0.85（封顶 0.85，不升到 1.0：高温在本模型上会诱发
    逐字自我重复；0.85 也是 REASONING_ONLY 空200 已验证有效的救援温度）。
    入参为“已连续发生的退化重试次数”（0 起）。
    """
    try:
        idx = int(degenerate_attempt)
    except (TypeError, ValueError):
        idx = 0
    return 0.7 if idx <= 0 else 0.85


def empty_scene_retry_directive(text_len: int, required_beats: int) -> str:
    """0字/极短摆烂稿专用指令：明确要求一次性输出完整正文，而非空答复/残句。"""
    if text_len <= 0:
        return ("上一稿返回了空白内容（0字），属于无效答复。本次必须【直接输出本场景完整正文】，"
                f"一次性写齐全部 {required_beats} 个情节点的动作/对话/感官细节；不要解释、不要只给思考、不要空响应。")
    return (f"上一稿仅 {text_len} 字，内容严重不足。本次必须直接输出本场景完整正文，"
            f"逐拍写齐全部 {required_beats} 个情节点，严禁再次返回空白或残句。")


def classify_scene_output(text: str, finish_reason: str, target_chars: int,
                          beats: list[str], had_leak: bool = False) -> str:
    """返回 USABLE / UNDER_COVERED / DEGENERATE_SHORT / MALFORMED_SHORT / TRUNCATED / HARD_DEGENERATE。"""
    n = len(text or "")
    if n < HARD_DEGENERATE_CHARS:
        return "HARD_DEGENERATE"
    if str(finish_reason or "") == "length":
        return "TRUNCATED"
    if not has_proper_ending(text) and n < MALFORMED_CHARS:
        return "MALFORMED_SHORT"
    cov = beat_coverage(text, beats)
    if n < int(float(target_chars) * 0.3):
        # 有句末、演到一点的短稿可交扩写（L3）；完全没演到 beat 的按欠覆盖/退化处理
        if has_proper_ending(text) and not had_leak and cov >= L3_BEAT_COV:
            return "DEGENERATE_SHORT"
        return "MALFORMED_SHORT"
    if cov < 0.5:
        return "UNDER_COVERED"
    return "USABLE"
