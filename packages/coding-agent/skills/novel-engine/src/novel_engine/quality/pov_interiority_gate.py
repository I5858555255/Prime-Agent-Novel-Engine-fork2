# -*- coding: utf-8 -*-
"""CC round-22 P0-1（缺陷A）：低能动性主角 POV 内心独白健康门（零 LLM 纯规则）。

问题：婴儿/重伤/被囚等低能动性章节，flash 会把任务卡里"至少 1 处转世意识评判性
内在观察"的密度条款执行成大段**成人式抽象独白**（恩情/誓约/命运/修为/前世/此生不负
+ 连贯成人论证），违背角色当前认知，使情节原地踏步。

本门只对 director 标注的低能动性状态（RESTRICTED_LEVELS）生效，其余章节完全跳过。
定位"内心独白引导词"引导到句末的片段，判两类违规：
- length_exceeded：内心片段长度 > max_chars（默认 50）；
- abstract_concept：片段命中 forbidden 抽象概念/前世身份术语。

修复（零 LLM）：
- 能通过"在最近句读处截断到 <= max_chars"且截断后不含禁词的片段 -> 确定性截断；
- 截断后仍含禁词、或片段本身就短却含禁词 -> 无法靠截断清除，交由编排层对该场景
  带负例定点重生 1 次（极少数）。
"""
from __future__ import annotations

import re

# 低能动性状态：只在这些状态下启用本门
RESTRICTED_LEVELS = frozenset({"infant_low", "injured", "imprisoned"})

# 低能动性内心独白里禁止出现的抽象概念 / 前世身份术语
DEFAULT_FORBIDDEN_ABSTRACT = (
    "恩情", "誓约", "命运", "修为", "前世", "转世", "此生不负", "不负今生", "报恩",
)

# 内心独白引导词（第三人称叙事 + 直接心理活动）
_CUE_RE = re.compile(
    r"(?:心里|心中|心头|心想|暗想|暗道|心道|暗忖|思忖|意识到|脑海中|脑海里|他想|她想|不禁想)"
)
# 句末/句内停顿（截断时优先在这些标点后收束）
_STOP_PUNCT = "。！？!?；，、；：:"


def is_restricted(agency_level: str | None) -> bool:
    return bool(agency_level) and str(agency_level).strip().lower() in RESTRICTED_LEVELS


def _iter_segments(text: str):
    """产出 (start, end, segment)：引导词起，到本句句末标点/换行止（不含重叠）。"""
    spans = []
    for m in _CUE_RE.finditer(text or ""):
        s = m.start()
        e = len(text)
        for j in range(s, len(text)):
            if text[j] in "。！？!?\n":
                e = j + 1
                break
        # 去掉与已收录片段的重叠（取较长者）
        if any(not (e <= os_ or s >= oe_) for os_, oe_ in [(a, b) for a, b, _ in spans]):
            # 与既有片段重叠：跳过，避免重复计数
            continue
        spans.append((s, e, text[s:e]))
    return spans


def _truncate_at_punctuation(segment: str, max_chars: int) -> str:
    """把独白片段截断到 <= max_chars，优先在其内最后一个停顿标点后收束。"""
    if len(segment) <= max_chars:
        return segment
    head = segment[:max_chars]
    cut = -1
    for k, ch in enumerate(head):
        if ch in _STOP_PUNCT:
            cut = k + 1
    if cut >= 4:  # 至少保留几个字，避免切空
        return head[:cut]
    return head


def check_pov_interiority_health(text: str, agency_level: str | None,
                                 max_chars: int = 50,
                                 forbidden: tuple | list = DEFAULT_FORBIDDEN_ABSTRACT) -> dict:
    """检测单场景正文。非受限状态直接 clean。返回 {clean, violations}。"""
    if not is_restricted(agency_level):
        return {"clean": True, "violations": []}
    violations = []
    for s, e, seg in _iter_segments(text):
        terms = [w for w in forbidden if w in seg]
        too_long = len(seg) > max_chars
        if too_long:
            violations.append({"type": "length_exceeded", "start": s, "end": e,
                               "segment": seg, "terms": terms})
        if terms and not too_long:
            # 短却含抽象禁词：单独记一条（超长且含词的已在上面记录，terms 已带）
            violations.append({"type": "abstract_concept", "start": s, "end": e,
                               "segment": seg, "terms": terms})
    return {"clean": not violations, "violations": violations}


def plan_and_excise(text: str, agency_level: str | None, max_chars: int = 50,
                    forbidden: tuple | list = DEFAULT_FORBIDDEN_ABSTRACT):
    """零 LLM 修复规划 + 就地截断。

    返回 (new_text, regen_terms, changed)：
    - 能纯截断清除（截后不含禁词）的片段已在 new_text 中被截短；
    - regen_terms：截断无法清除的抽象禁词（需编排层定点重生），去重排序；
    - changed：是否发生过确定性截断。
    非受限状态原样返回。
    """
    if not is_restricted(agency_level) or not text:
        return text, [], False

    repls = []  # (start,end,newseg)
    regen_terms: list[str] = []
    for s, e, seg in _iter_segments(text):
        terms = [w for w in forbidden if w in seg]
        too_long = len(seg) > max_chars
        if not too_long and not terms:
            continue
        # 扣除独白引导词前紧邻的主语前缀（如“他”），保证整段含主语不超过 max_chars
        line_start = 0
        for k in range(s - 1, -1, -1):
            if text[k] in "。！？!?\n":
                line_start = k + 1
                break
        prefix = text[line_start:s]
        eff_max = max(20, max_chars - len(prefix))
        cand = _truncate_at_punctuation(seg, eff_max)
        leftover = [w for w in forbidden if w in cand]
        if not leftover and (prefix + cand) != text[line_start:e]:
            repls.append((line_start, e, prefix + cand))
        else:
            # 截断后仍有禁词（或无需截断却有禁词）-> 交重生；本段不做截断
            for w in (leftover or terms):
                if w not in regen_terms:
                    regen_terms.append(w)

    if not repls:
        return text, sorted(regen_terms), False

    # 从后往前替换，保证索引有效
    new_text = text
    for s, e, cand in sorted(repls, key=lambda x: x[0], reverse=True):
        new_text = new_text[:s] + cand + new_text[e:]
    return new_text, sorted(regen_terms), True
