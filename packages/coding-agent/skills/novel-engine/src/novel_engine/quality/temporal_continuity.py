# -*- coding: utf-8 -*-
"""CC round-20：时间锚越界（回溯/前瞻）确定性切除自救。

flash 在转生/回忆类开篇常把"十六岁那年""三年后""次年"等跨出本章时间锚的叙述
写进单夜场景。定点重生改不动时，按任务卡 timeline_anchor 做相对判定，确定性切除
最小越界子句（整句兜底），供编排层切除后重跑 continuity 门复检；复检仍倒置则
gap-continue，不整批 HALT。纯规则、零 LLM。
"""
import re

# 第一层：高置信度绝对越界标记（短时间锚章节里几乎不可能合法）
ABSOLUTE_VIOLATION_PATTERNS = [
    r"[一二三四五六七八九十百千零两\d]+年[后前]",
    r"次年", r"翌[年日]", r"多年以后", r"多年后", r"童年",
    r"那年", r"那一年",
    r"[一二三四五六七八九十百两\d]+岁(?:那年|的时候|时)",
    r"\d+岁",
    r"孤儿院",  # 转生开篇前生场景回溯的高置信锚点
    r"小时候", r"年少时", r"年轻时",
]

# 第二层：单夜内合法时间推进词（命中即优先判合法）
LEGITIMATE_SAME_NIGHT_PATTERNS = [
    r"[子丑寅卯辰巳午未申酉戌亥]时[初中末]?",
    r"[半一二三两\d]+炷香", r"片刻", r"不多时", r"须臾", r"俄顷",
    r"一顿饭[的]?功夫", r"夜半", r"子夜", r"三更", r"五更",
    r"鸡[鸣叫][^。！？\n]{0,4}[一二三四两三\d]+遍",
    r"当夜", r"今夜", r"今晚",
]

# 第三层：跨日边界词
DAWN_BOUNDARY_PATTERNS = [r"天明", r"次日", r"翌日", r"清晨", r"黎明", r"天[刚蒙]?蒙?亮", r"天亮"]

# 锚点若限定不得晚于这些时刻（或明确不跨日），则天明/次日类判越界
_NIGHT_CAP_PATTERNS = [r"寅时末", r"丑时末", r"子时末", r"不跨日", r"当夜", r"当晚"]

_SENT_SPLIT_RE = re.compile(r"([^。！？!?…\n]+[。！？!?…]?)")
_CLAUSE_SPLIT_RE = re.compile(r"([，,、；;——]+)")

_ABS_RES = [re.compile(p) for p in ABSOLUTE_VIOLATION_PATTERNS]
_LEG_RES = [re.compile(p) for p in LEGITIMATE_SAME_NIGHT_PATTERNS]
_DAWN_RES = [re.compile(p) for p in DAWN_BOUNDARY_PATTERNS]
_CAP_RES = [re.compile(p) for p in _NIGHT_CAP_PATTERNS]


def normalize_anchor(timeline_anchor) -> str:
    if timeline_anchor is None:
        return ""
    if isinstance(timeline_anchor, str):
        return timeline_anchor
    if isinstance(timeline_anchor, dict):
        parts = [str(timeline_anchor.get(k, "")) for k in
                 ("timeline_anchor", "anchor", "latest_permissible_moment", "time_range", "constraint")]
        parts.extend(str(v) for v in timeline_anchor.values() if isinstance(v, str))
        return " ".join(p for p in parts if p)
    return str(timeline_anchor)


def _anchor_caps_at_night(anchor_text: str) -> bool:
    return any(rx.search(anchor_text) for rx in _CAP_RES)


def classify_time_reference(sentence: str, timeline_anchor) -> str:
    """返回 VIOLATION / LEGITIMATE / NEUTRAL。

    合法当夜词优先（即便同句伴随可疑词也判合法，避免误删正常推进）；其次高置信
    绝对越界；最后跨日边界词结合锚点是否封顶在当夜。
    """
    s = sentence or ""
    if any(rx.search(s) for rx in _LEG_RES):
        return "LEGITIMATE"
    if any(rx.search(s) for rx in _ABS_RES):
        return "VIOLATION"
    if any(rx.search(s) for rx in _DAWN_RES):
        if _anchor_caps_at_night(normalize_anchor(timeline_anchor)):
            return "VIOLATION"
        return "NEUTRAL"
    return "NEUTRAL"


def _split_keep(text: str, pattern: re.Pattern) -> list[str]:
    # 保留分隔符到相邻子句
    out: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if pattern.fullmatch(ch):
            out.append(buf)
            buf = ""
    if buf:
        out.append(buf)
    return out


def _is_violating_clause(clause: str, anchor) -> bool:
    return classify_time_reference(clause, anchor) == "VIOLATION"


def _remainder_ok(remainder: str) -> bool:
    core = re.sub(r"[，,、；;——\s]+$", "", remainder).strip()
    cjk = sum(1 for c in core if "一" <= c <= "鿿")
    return cjk >= 6  # 残留足够实义内容才保留，否则整句删除更干净


def excise_from_text(text: str, timeline_anchor) -> tuple[str, int]:
    """对整段场景文本做最小越界切除。返回 (新文本, 删除命中数)。无命中返回 (原文, 0)。"""
    anchor = timeline_anchor
    removed = 0
    out_paragraphs = []
    for para in (text or "").split("\n"):
        sentences = [m.group(0) for m in _SENT_SPLIT_RE.finditer(para)]
        if not sentences:
            out_paragraphs.append(para)
            continue
        kept_sentences = []
        for sent in sentences:
            cls = classify_time_reference(sent, anchor)
            if cls != "VIOLATION":
                kept_sentences.append(sent)
                continue
            # 最小子句切除：按子句切分，仅丢越界子句
            pieces = _split_keep(sent, re.compile(r"[，,、；;]"))
            kept_pieces = []
            for pc in pieces:
                if _is_violating_clause(pc, anchor):
                    removed += 1
                    continue
                kept_pieces.append(pc)
            remainder = "".join(kept_pieces).strip("，,、；; ")
            if _remainder_ok(remainder):
                # 保证句末有终止符
                if not re.search(r"[。！？…]$", remainder):
                    remainder += "。"
                kept_sentences.append(remainder)
            else:
                removed += 1  # 整句删除
        out_paragraphs.append("".join(kept_sentences))
    new_text = "\n".join(out_paragraphs)
    if removed == 0 or new_text.strip() == (text or "").strip():
        return text, 0
    return new_text, removed
