# -*- coding: utf-8 -*-
"""Deterministic timeline / protagonist-age jump gate (CC round-7 Q5).

The director declares a ``timeline_anchor`` per chapter stating the legal in-story
time span and explicit ``forbidden_markers``. After assembly we scan the prose for
(1) any director-forbidden marker and (2), when the chapter is confined to a short
span, generic growth / multi-year / protagonist-age jump phrases (the chapter-2
failure jumped from birth night to "陆烬五岁那年").

Everything here is pure text logic so it is fully unit-testable offline; no LLM is
involved in the verdict.
"""
from __future__ import annotations

import re

# Generic forward-time jumps. Backward references ("三十年前", 回忆) are intentionally
# NOT matched -- only forward leaps that skip the story the chapter is meant to show.
_GENERIC_JUMP_PATTERNS = [
    r"[0-9一二两三四五六七八九十百]+\s*岁\s*那\s*[年日]",   # 五岁那年 / 五岁那日
    r"(?:多年|数年|十几年|数十年|十数年|数载|十数载)\s*以?\s*后",
    r"长大\s*(?:以后|之后|后|成|了)",
    r"成年(?:以后|之后|之后|时)?",
    r"少年时期",
    r"[0-9一二两三四五六七八九十百]+\s*年\s*后",          # 三年后 / 数年后（"三日前"不匹配）
]

# Compiled once.
_GENERIC_RE = [re.compile(p) for p in _GENERIC_JUMP_PATTERNS]

# CC round-11 R3：复用 continuity_gate 的引号 mask 逻辑（避免对白中的时间词被误判）
from novel_engine.quality.continuity_gate import _mask_quotes as _mask_quotes_timeline

# CC round-11 R3：心理/回忆叙述整句屏蔽模式。
# 角色在当下对过去的语言引用（心里想着/回想起/想起/记起/回忆等）时间仍在叙事当下，
# 该整句被替换为空白后不再触发禁写标记检查；但电影化场景重定位（画面回到/镜头回到等）
# 不属于心理叙述，命中禁写标记即应上报。
# CC round-12 R1：补充完整的"想起/记起/回忆"家族词表（真机 ch3 原句含"想起"）。
_THOUGHT_FULL_PATTERNS = [
    r"心里[想思]着[^。！？]*[昨晚夜]",
    r"心想[^。！？]*[昨晚夜]",
    r"想着[^。！？]*[昨晚夜]",
    r"想起[^。！？]*[昨晚夜]",
    r"记起[^。！？]*[昨晚夜]",
    r"回忆起[^。！？]*[昨晚夜]",
    r"回想起[^。！？]*[昨晚夜]",
    r"回忆[^。！？]*[昨晚夜]",
    r"记得[^。！？]*[昨晚夜]",
    r"浮想[^。！？]*[昨晚夜]",
    # 一句揭示性回述：原来昨夜/原来当时/原来那天——属于心理/认知范畴，放行
    r"原来[昨晚夜][^\n。！？]*",
]
_THOUGHT_FULL_RE = [re.compile(p) for p in _THOUGHT_FULL_PATTERNS]


def _mask_thoughts(text: str) -> str:
    """将角色心理/回忆叙述整句（含一句揭示性回述）替换为空白，只保留客观叙事文本。"""
    if not text:
        return ""
    result = text
    for rx in _THOUGHT_FULL_RE:
        result = rx.sub(lambda m: "　" * len(m.group(0)), result)
    return result


# CC round-12 R1：回溯性时间词集合。仅字面提及、无重演 cue 时不判硬伤。
_BACK_REF_MARKERS = frozenset({
    "昨夜", "昨晚", "昨儿", "前夜", "前日", "前天",
    "此前", "先前", "之前", "刚才",
    "那日", "那天", "当年", "曾经", "过去",
})

# CC round-12 R1：场景重定位/重演 cue。与回溯性时间词同句出现时才判硬伤。
_REPOSITION_CUES = [
    "画面回到", "镜头回到", "视线回到",
    "一切回到", "时间倒回", "时光倒流",
    "闪回", "记忆闪回",
]
_REPOSITION_RE = [re.compile(re.escape(c)) for c in _REPOSITION_CUES]


def _has_reposition_cue_in_sentence(text: str, pos: int) -> bool:
    """在包含 pos 的完整句子（。！？… 为边界）内，是否同时存在重演 cue。

    搜索整个句子（向前找到句首、向后找到句尾），因为重演 cue 可能在 marker 之前或之后。
    """
    # 向前找句首（找最近的句号/问号/感叹号，取其后方位置）
    s_start = 0
    for sep in ("。", "！", "？"):
        prev = text.rfind(sep, 0, pos)
        if prev >= 0:
            s_start = prev + 1
            break
    # 向后找句尾（找最近的句号/问号/感叹号/省略号）
    s_end = len(text)
    for sep in ("。", "！", "？", "…"):
        idx = text.find(sep, pos)
        if idx >= 0:
            s_end = min(s_end, idx + 1)
            break
    seg = text[s_start:s_end]
    return any(rx.search(seg) for rx in _REPOSITION_RE)


def allows_year_span(max_time_progression: str | None) -> bool:
    """A declared cap containing an explicit year span permits year-scale movement.

    A cap expressed in 日/夜/周/旬/月 (or empty for the opening infant arc) does not.
    """
    s = str(max_time_progression or "")
    return "年" in s


# CC round-14 R1：空间地点短语守卫。when forbidden_marker 实际是地点方位词（如
# "禁区边缘"/"禁地边上"/"后山深处"/"林子边上"/"井边"）时，不应判为时间线越界。
_SPATIAL_LOCATION_ROOTS = frozenset({
    "禁区", "禁地", "后山", "林子", "山林", "山林边", "山边", "井边", "井台",
    "村口", "村头", "祠堂", "屋后", "坡下", "洼处", "洼地", "雾中", "雾里",
    "河边", "江畔", "湖边", "塘边", "道旁", "路旁", "巷口", "街口",
})
_SPATIAL_SUFFIXES = frozenset({
    "边缘", "边上", "边", "深处", "入口", "外围", "附近", "外头", "那边",
    "周围", "里头", "里面", "旁边", "尽头", "顶端", "底端",
})
# CC round-30：director 偶把"地名揭示禁词"（如"青河县"）误填入 forbidden_markers，
# 地名不是时间标记，不得触发时间线越界。地理后缀命中即视为地点短语。
_GEO_SUFFIXES = frozenset({
    "县", "镇", "村", "寨", "庄", "山", "岭", "坡", "河", "江", "湖", "海",
    "溪", "潭", "谷", "沟", "滩", "湾", "港", "关", "城", "坊", "街", "道",
    "林", "原", "野", "园", "祠", "庙", "观", "亭", "桥", "井", "泉", "洞",
    "口", "州", "府", "郡", "国", "界",
    # CC round-30 追加：建筑/场所类地名（武馆、镖局、客栈、书院等），仅限 2 字明确场所词
    "武馆", "镖局", "客栈", "酒楼", "书院", "医馆", "药铺", "府邸",
    "庄园", "山寨", "门派", "当铺", "衙门", "驿站", "码头", "营地",
})


def _is_spatial_marker(marker: str) -> bool:
    """判断 forbidden_marker 是否为空间地点短语（非时间词）。"""
    if not marker or len(marker) < 2:
        return False
    has_root = any(root in marker for root in _SPATIAL_LOCATION_ROOTS)
    has_suffix = any(marker.endswith(s) for s in _SPATIAL_SUFFIXES)
    if has_root and has_suffix:
        return True
    if has_root and len(marker) >= 2:
        return True
    # 地理后缀：地点名（如"青河县""雾隐村"）不是时间标记
    if any(marker.endswith(s) for s in _GEO_SUFFIXES) and len(marker) >= 2:
        return True
    return False


def detect_timeline_jump(
    scene_text: str,
    timeline_anchor: dict | None = None,
    chapter_num: int = 0,
) -> str | None:
    """Return a hard-issue string when the scene jumps beyond its declared time span.

    Explicit director ``forbidden_markers`` are always enforced. Generic growth-jump
    phrases are enforced only when the chapter's legal span is shorter than a year
    (or, without an anchor, during the opening infant arc chapters 1-10).

    CC round-12 R1：回溯性时间词（昨夜/昨晚/此前/当年…）在无重演 cue 的同句内
    仅字面提及时不判硬伤；有重演 cue（画面回到/闪回/时间倒回…）时才上报。
    顺向跳跃（X岁那年/多年以后/成年）维持现有硬拦截逻辑。
    """
    text = scene_text or ""
    anchor = timeline_anchor if isinstance(timeline_anchor, dict) else {}

    # (1) Director-declared forbidden markers are authoritative.
    # CC round-11 R3：先 mask 引号（对白），再 mask 心理/回忆整句，
    # 排除对白与心理引用中的时间词干扰；仅一句揭示性回述（原来昨夜等）也豁免。
    # CC round-12 R1：回溯性时间词需配合重演 cue 才判硬伤。
    masked_text = _mask_quotes_timeline(text) if text else ""
    if text:
        masked_text = _mask_thoughts(masked_text)
    for marker in anchor.get("forbidden_markers", []) or []:
        m = str(marker or "").strip()
        if len(m) >= 2 and m in masked_text:
            # CC round-14 R1：空间地点短语不得当时间越界拦截
            if _is_spatial_marker(m):
                continue
            # CC round-26e：寿命/命运表述（或许活不到成年/活到成年已是难事）不是
            # 时间跳跃——陈老根担心婴儿夭折的当前心理，非成年之后的顺向成长叙述
            if m == "成年" and re.search(r"活[不到至过满]{0,3}[^。！？]{0,8}成年", masked_text):
                continue
            # CC round-12 R1：回溯性时间词 —— 仅在重演 cue 同句才判
            if m in _BACK_REF_MARKERS:
                pos = masked_text.find(m)
                if not _has_reposition_cue_in_sentence(masked_text, pos):
                    continue  # 仅字面提及，不判硬伤
            return f"[时间线越界] 命中导演禁写时间标记\"{m}\""

    # (2) Generic patterns only inside a sub-year legal span.
    span = anchor.get("max_time_progression")
    if span is None and chapter_num and chapter_num <= 10:
        sub_year = True
    else:
        sub_year = bool(span) and not allows_year_span(span)
    if sub_year:
        for rx in _GENERIC_RE:
            m = rx.search(text)
            if m:
                # CC round-26e：寿命/命运表述（活不到成年/活到成年）非时间跳跃，跳过
                if m.group(0) == "成年" and re.search(r"活[不到至过满]{0,3}[^。！？]{0,8}成年", text):
                    continue
                return f"[时间线越界] 本章时间跨度限于\"{span or '开篇短时段'}\"，却出现越界叙述\"{m.group(0)}\""
    return None


def anchor_fix_directive(issue: str, timeline_anchor: dict | None = None) -> str:
    """Build a writer-facing repair directive for a timeline violation."""
    span = ""
    if isinstance(timeline_anchor, dict):
        span = str(timeline_anchor.get("max_time_progression", "") or "")
    return (
        f"上一稿触发时间线越界硬门（{issue}）。本章时间范围严格限于\"{span or '当下短时段'}\"，"
        "只允许连续演当下发生的场景，严禁提前叙述成长、多年以后、主角长大或具体年岁之后的事；"
        "如需交代过去只能用人物简短回忆带过，且不得出现'X岁那年/多年后/长大后'等跳跃表述。"
        "本次请删除一切越界段落，把篇幅用于演足当下这一个场景。"
    )
