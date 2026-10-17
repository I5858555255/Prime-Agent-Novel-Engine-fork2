# -*- coding: utf-8 -*-
"""CC round-18 C（phase3 D2 修订）：跨场事件连续性 / 悬空危机门（零 LLM 纯规则）。

核心修正：
1. 危机只认"召唤/报信"共现模式：召唤动作（捶门/拍门/喊/叫/喊住/报信/来报/闯进）
   ＋ 祈使或凶讯（快去/快/出事/走/不得了/出人命/闯祸…）；
   裸"井/死了/火/忽然/传来/不得了"不得单独构成危机。
2. 承接必须是对同一事件线程的实质回应（去井口/查看/赶到/处置该危机实体并与该
   实体共指）；"于是/随后/接着/然后/处理(泛)/查看(泛)"等通用词不得单独算承接。
3. 重置需"时间复位（同夜/当晚/是夜…）＋ 状态复位（平静/静坐/只字未提/没提…）"
   共现，且针对该召唤；分支二（无 reset 也报中间场）已移除。
4. 同时扫描 final text（当 root=None 且无结构化场景时）与 journal。

返回 {"off_card_crises": [...], "has_issue": bool}。
"""
from __future__ import annotations

import re
from pathlib import Path


_CALL_VERBS = frozenset({
    "捶门", "拍门", "敲门", "砸门", "擂门", "踹门", "推门",
    "喊住", "吼", "嚷", "催", "报信", "来报",
    "闯进", "闯入", "冲进来", "急跑", "急奔", "飞奔",
})
# P7-1a：单字「叫」「喊」过于宽泛（狗叫/鸡鸣/喊话均可命中），须配合门上下文才算危机召唤
_CALL_VERBS_SINGLE = frozenset({"叫", "喊"})

# P7-1：扩充紧急谓词
_CRISIS_PREDICATES = frozenset({
    "出事", "出事了", "快去", "快去看", "快", "赶紧", "赶快", "走", "闯祸",
    "闯大祸", "出人命", "死了", "出殡", "棺材", "衙门", "官差", "抓人",
    "要命", "不得了", "出大祸", "塌了", "塌方", "决口", "淹了", "起火",
    "大火", "走水", "惊闻", "忽听",
    "开门", "快开门", "救命", "来人", "不好了", "惊慌", "急切求救", "求你",
})

# P7-1c：回忆/心理标记——命中这些词的句不算实质承接
_RECALL_MARKERS = frozenset({
    "想起", "记得", "回忆", "那日", "平日", "心中", "暗想", "自语",
    "脑中", "眼前浮现", "想起来", "脑海中", "浮现在眼前", "历历在目", "记忆犹新",
})

# P7-1c：收紧承接模式，移除「眼前」「井边」等易误判词；补充「门前」「开门」以覆盖上门类危机响应
_RESOLVE_PATTERN = re.compile(
    r"(去|赶|跑|冲|忙|急忙|连忙|立刻|马上|起身|披衣|下床|点灯|拿起|"
    r"查看|处置|处理|解决|应对|前往|奔赴|探望|慰问|打听|询问|追问|查探|"
    r"迎出去|走进|出来|门前|开门)"
)

_GENERIC_CONNECTORS = frozenset({
    "于是", "随后", "接着", "然后", "因此", "所以", "因而", "可见", "看来",
})

_TIME_RESET_MARKERS = frozenset({
    "同夜", "当晚", "是夜", "当夜", "数时辰", "两三个时辰", "几个时辰",
    "半夜", "深夜", "夜里", "夜间", "子时", "丑时", "寅时",
    # P7-1：夜间复位词补全
    "夜色", "入夜", "天黑", "夜已深", "点灯", "掌灯", "油灯",
})

_STATE_RESET_MARKERS = frozenset({
    "平静的", "平静", "静坐", "沉默", "只字未提", "不提", "没提",
    "零提及", "没有提及", "置若罔闻", "当作没", "忘得", "不再提",
    # P7-1：状态复位词补全
    "静静坐着", "静静燃着", "安睡", "熟睡", "一动不动", "再无声响",
    "再无动静", "沉默良久",
})


# P7-1：事件窗口大小（前后各 N 句）
_WINDOW_SIZE = 2


def _scene_text_from_journal(root, chapter_num: int) -> dict[int, str]:
    from novel_engine.pipeline.chapter_journal import load_scenes
    try:
        scenes = load_scenes(root, chapter_num)
        return {int(s.get("scene_id", 0)): s.get("scene_text", "") or "" for s in scenes}
    except Exception:
        return {}


def _scene_text_from_task_card(task_card: dict) -> dict[int, str]:
    out = {}
    for bp in (task_card.get("scene_blueprints") or []):
        if not isinstance(bp, dict):
            continue
        sid = int(bp.get("scene_num", 0) or 0)
        goal = str(bp.get("goal", "") or "")
        beats = "；".join(str(b) for b in (bp.get("beats") or []) if b)
        out[sid] = f"{goal}；{beats}"
    return out


def _split_sentences(text: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for ch in text or "":
        buf.append(ch)
        if ch in "。！？!?…":
            s = "".join(buf).strip()
            if s:
                out.append(s)
            buf = []
    if "".join(buf).strip():
        out.append("".join(buf).strip())
    return out


def _has_call_crisis(sentence: str) -> bool:
    has_call = any(v in sentence for v in _CALL_VERBS)
    has_predicate = any(p in sentence for p in _CRISIS_PREDICATES)
    # P7-1a：单字「叫」「喊」须搭配门上下文或紧急谓词，否则视为噪声（狗叫/鸡鸣/寻常喊话）
    has_single_call = any(v in sentence for v in _CALL_VERBS_SINGLE)
    if has_single_call and not has_call:
        has_door_ctx = any(kw in sentence for kw in {"门", "门外", "院外", "门口", "院内"})
        if not has_door_ctx and not has_predicate:
            has_single_call = False
    return (has_call or has_single_call) and has_predicate


# P7-1：事件窗口检测（跨句聚合）
def _has_call_crisis_window(sents: list[str], idx: int) -> tuple[bool, str]:
    """在 idx 句为中心的事件窗口内，是否有召唤动作+紧急谓词共现。"""
    lo = max(0, idx - _WINDOW_SIZE)
    hi = min(len(sents), idx + _WINDOW_SIZE + 1)
    window = sents[lo:hi]
    window_text = " ".join(window)
    has_call = any(v in window_text for v in _CALL_VERBS)
    has_predicate = any(p in window_text for p in _CRISIS_PREDICATES)
    if has_call and has_predicate:
        for s in window:
            if any(v in s for v in _CALL_VERBS) or any(p in s for p in _CRISIS_PREDICATES):
                return True, s[:80]
    return False, ""


def _is_recall_sentence(sentence: str) -> bool:
    """句子是否处于回忆/心理语境。"""
    return any(m in sentence for m in _RECALL_MARKERS)


def _has_substantive_resolve(sentence: str, prior_crisis_entity: str = "") -> bool:
    """句子是否对前场危机做了实质性承接（非通用连接词 alone，且非回忆句）。"""
    # P7-1c：回忆句不算承接
    if _is_recall_sentence(sentence):
        return False
    # P4-5：通用连接词单独出现不算承接
    has_any_connector = any(c in sentence for c in _GENERIC_CONNECTORS)
    has_resolve_pattern = bool(_RESOLVE_PATTERN.search(sentence))
    if has_any_connector and not has_resolve_pattern:
        return False
    # P7-1c：上门/来人类危机，必须明确响应"门/来人"才算承接
    # 通用动词"去"（如"进去的人""传出去""睡去"）在门危机中不构成承接
    if prior_crisis_entity:
        is_door_crisis = any(kw in prior_crisis_entity for kw in {"门", "来人"})
        if is_door_crisis:
            # 明确门响应动作才算承接
            door_action_patterns = re.compile(
                r"(开[门]|迎出去|走向门|门前|敲门|开门|问是谁|来者|迎上|出门查看)"
            )
            if door_action_patterns.search(sentence):
                return True
            return False
    if has_resolve_pattern:
        return True
    return False


def _has_time_reset(sentence: str) -> bool:
    return any(m in sentence for m in _TIME_RESET_MARKERS)


def _has_state_reset(sentence: str) -> bool:
    return any(m in sentence for m in _STATE_RESET_MARKERS) or bool(
        re.search(r"对.*(?:只字未提|没提|不提|零提及|没有提及)", sentence)
    )


def _split_text_into_scenes(text: str, task_card: dict) -> dict[int, str]:
    """当 journal 为空时，尝试将最终文本切分为多场。

    优先级：
    1. 按 ※（U+203B）或 •（U+2605）分隔符切分
    2. 若无分隔符，按总长度比例在段落边界切为 expected_count 场
    """
    bps = task_card.get("scene_blueprints") or []
    expected_count = len(bps)
    if expected_count <= 1:
        return {}

    # 优先：按分隔符切分（生产用※，部分测试用•）
    for sep in (chr(0x203B), chr(0x2605)):
        if sep in text:
            parts = text.split(sep)
            result = {}
            for i, part in enumerate(parts):
                stripped = part.strip()
                if stripped:
                    result[i + 1] = stripped
            return result

    # 退化：按总长度比例切分（在段落边界处分割）
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    if len(paragraphs) < expected_count:
        return {}

    total_chars = sum(len(p) for p in paragraphs)
    splits = []
    target = total_chars // expected_count
    accumulated = 0
    for i, p in enumerate(paragraphs):
        accumulated += len(p)
        if len(splits) < expected_count - 1 and accumulated >= (len(splits) + 1) * target:
            splits.append(i + 1)
    splits.append(len(paragraphs))

    result = {}
    prev = 0
    for i, sp in enumerate(splits):
        if i < expected_count:
            result[i + 1] = "\n\n".join(paragraphs[prev:sp])
        prev = sp
    return result


def _full_text_fallback(text: str, chapter_num: int, task_card: dict) -> dict:
    """fail-closed 兜底：将整段 text 作为单场运行检测。

    仅当召唤事件 + 后续时间/状态复位且无承接时才报。
    """
    sents = _split_sentences(text)
    card_events = [
        str(e.get("one_line_summary", ""))
        for e in (task_card.get("chapter_events") or [])
        if isinstance(e, dict)
    ]
    crises: list[dict] = []
    for si, s in enumerate(sents):
        hit, desc = _has_call_crisis_window(sents, si)
        if not hit:
            continue
        is_on_card = False
        for ev in card_events:
            if len(ev) >= 4 and (ev in desc or desc[:30] in ev or ev[:30] in desc):
                is_on_card = True
                break
        if is_on_card:
            continue
        later_sents = sents[si + 1:]
        has_resolve = any(_has_substantive_resolve(ls, "") for ls in later_sents)
        has_time_reset = any(_has_time_reset(ls) for ls in later_sents)
        has_state_reset = any(_has_state_reset(ls) for ls in later_sents)
        if has_time_reset and has_state_reset and not has_resolve:
            crises.append({
                "scene_id": "?",
                "crisis_desc": desc,
                "reset_desc": "".join(later_sents[:3])[:80],
            })
            break
    return {"off_card_crises": crises, "has_issue": bool(crises)}


def detect_off_card_crisis(
    text: str,
    chapter_num: int,
    task_card: dict,
    root=None,
    scene_texts: dict[int, str] | None = None,
) -> dict:
    """检测跨场悬空危机。

    返回 {"off_card_crises": [{scene_id, crisis_desc, reset_desc}], "has_issue": bool}。
    仅当某场引入危机（召唤+凶讯）、但后续场无实质承接、且存在时间+状态双重重置迹象时才报。
    若任务卡 chapter_events 包含同语义描述则视为 on-card（允许悬空到章末钩子）。

    参数优先级：scene_texts > 最终文本按分隔符切分 > journal > blueprint > {0:text}
    """
    # P7D：归一化 scene_texts——接受 str（含※/•分隔符的 assembled 文本）或 dict[int,str]
    # 字符串形态为生产实际传参类型，必须兼容避免 AttributeError
    if scene_texts is not None:
        if isinstance(scene_texts, str):
            normalized = scene_texts.strip()
            if normalized:
                scene_texts = _split_text_into_scenes(normalized, task_card)
            else:
                scene_texts = {}
        elif isinstance(scene_texts, dict):
            pass  # already correct
        else:
            raise TypeError(
                f"scene_texts must be str or dict[int,str], got {type(scene_texts).__name__}"
            )
    # 优先级1：显式传入的场景文本（生产调用时使用）
    if scene_texts is not None and scene_texts:
        pass  # already normalized above
    # 优先级2：从 journal 加载
    elif root is not None:
        scene_texts = _scene_text_from_journal(root, chapter_num)
    else:
        scene_texts = {}
    # 优先级3：若 journal 为空但 text 非空且有多个 blueprint，尝试按分隔符切分
    if not scene_texts and text and (task_card.get("scene_blueprints") or []):
        scene_texts = _split_text_into_scenes(text, task_card)
    # 优先级4：从 task_card 的 blueprint 组装（仅 goal/beats，非正文）
    if not scene_texts:
        scene_texts = _scene_text_from_task_card(task_card)
    # 优先级5：退化为单场
    if not scene_texts:
        scene_texts = {0: text}

    # Fail-closed：若 blueprint 场数>1 但最终只解析出1个场，至少对整段 text 跑兜底判定
    bp_count = len(task_card.get("scene_blueprints") or [])
    if bp_count > 1 and len(scene_texts) <= 1 and text:
        fallback = _full_text_fallback(text, chapter_num, task_card)
        if fallback["has_issue"]:
            return fallback

    card_events = [
        str(e.get("one_line_summary", ""))
        for e in (task_card.get("chapter_events") or [])
        if isinstance(e, dict)
    ]

    crises: list[dict] = []
    ordered_sids = sorted(scene_texts.keys())

    for i, sid in enumerate(ordered_sids):
        sents = _split_sentences(scene_texts.get(sid, ""))
        # P7-1：事件窗口检测（跨句聚合）
        crisis_found = False
        crisis_desc = ""
        crisis_si = -1
        for si, s in enumerate(sents):
            hit, desc = _has_call_crisis_window(sents, si)
            if hit:
                crisis_found = True
                crisis_desc = desc
                crisis_si = si
                break
        if not crisis_found:
            continue

        is_on_card = False
        for ev in card_events:
            if len(ev) >= 4 and (ev in crisis_desc or crisis_desc[:30] in ev or ev[:30] in crisis_desc):
                is_on_card = True
                break
        if is_on_card:
            continue

        # 提取危机实体关键词：仅在触发窗口内搜索，不扫描全场景（避免早段无关词干扰）
        # P7-1c：上门类危机优先识别"门/来人"而非场景中的其他实体（如"井"）
        window_text = ""
        for _esi, _es in enumerate(sents):
            _ehit, _ = _has_call_crisis_window(sents, _esi)
            if _ehit:
                _elo = max(0, _esi - _WINDOW_SIZE)
                _ehi = min(len(sents), _esi + _WINDOW_SIZE + 1)
                window_text = " ".join(sents[_elo:_ehi])
                break
        crisis_entity = ""
        for kw in ["来人", "门", "出事", "人命", "火", "塌", "棺材", "衙门", "官差", "井"]:
            if kw in window_text:
                crisis_entity = kw
                break

        later_text = " ".join(
            scene_texts.get(ns, "") for ns in ordered_sids[i + 1:]
        )
        later_sents = _split_sentences(later_text)
        # P7C：承接检查仅针对后续场景，避免同场景内危机触发句与响应句边界模糊
        # （如"快开门！"是召唤的一部分，不应误判为承接）
        has_resolve = any(
            _has_substantive_resolve(s, crisis_entity) for s in later_sents
        )
        has_time_reset = any(_has_time_reset(s) for s in later_sents)
        has_state_reset = any(_has_state_reset(s) for s in later_sents)
        has_full_reset = has_time_reset and has_state_reset

        if has_full_reset and not has_resolve:
            crises.append({
                "scene_id": sid,
                "crisis_desc": crisis_desc,
                "reset_desc": later_text[:80],
            })

    return {
        "off_card_crises": crises,
        "has_issue": bool(crises),
    }


def off_card_crisis_fix_directive(crises: list[dict]) -> str:
    lines = ["本场景引入突发危机事件，但后续场景未承接也未解决，造成跨场不连贯。"]
    for c in crises:
        sid = c.get("scene_id", "?")
        desc = c.get("crisis_desc", "")
        lines.append(f"场景{sid}危机：「{desc}」")
        if c.get("reset_desc"):
            rdesc = c.get("reset_desc", "")
            lines.append(f"后续场景重置：「{rdesc}」")
    lines.append(
        "必须满足其一：(i) 在后续场景承接/解决该危机；(ii) 将该危机改为章末钩子并写入任务卡 chapter_events。"
        "禁止引入后不承接即时间/状态重置。"
    )
    return "\n".join(lines)
