# -*- coding: utf-8 -*-
"""CC round-18 D：命名一致性 + 伏笔极性一致性门（零 LLM 纯规则）。

两类检测：
1. 命名矛盾：canonical 地名（如「古井」=活井，伴取水/辘轳证据）与同章出现的
   矛盾变体（如「枯井」=无水源）共现 → 判硬问题，要求重生统一。
2. 伏笔极性矛盾：同一章对同一现象既反复定性「排斥/避开/怕脏东西」又无铺垫
   出现「吸引」→ 判 medium-high 一致性问题，要求重生显式二选一或补铺垫。
   含转折词（却/反而/反倒/并非/并未/不是）修饰吸引侧时降为 soft，不误杀合法对比。
   长线「吸氧/转化浊气」是后文真相，首次出现必须显式铺垫。

配置驱动：config/naming_consistency/*.json（可编辑词条与章节范围；无文件时用内置默认）。

P7B 修正：
- 引入现象词存在性前提
- 排斥/吸引事件与现象词在±3句窗口内绑定（不再要求同句）
- 吸引词分两层：craving（低歧义，需现象近窗）与 intake（需严格入身正则+现象近窗）
- 移除裸匹配易误报词（吞噬/牵引/循着/迎合/钻入/汲取/沉醉/沉迷/贪吃）
- 转化铺垫跨句识别
- 陈老根修炼排除
- 返回审计证据片段
"""
from __future__ import annotations

import json
import re
from pathlib import Path

_CFG_CACHE: dict | None = None

# 转折/否定修饰词
_CONTRAST_MARKERS = frozenset({
    "却", "反而", "反倒", "并非", "并未", "不是", "不叫", "非是", "倒非",
})

# P7B：转化铺垫连接词——排斥与吸引之间存在这些词则豁免硬冲突
# 仅保留明确表达态度转变的词；过滤掉描述恢复/物理过程的词（渐渐平复/慢慢转身等）
# 注意："原本" 已从集合中移除——它常作过去状态描述（如"原本平静"），非态度转变
# 注意："竟" 已移除——它常作"究竟"/"竟也"等副词，需配合态度动词才计为铺垫
_TRANSFORMATION_PAVING = frozenset({
    # 明确态度转变连接
    "起初", "先前", "一开始", "初见",
    "后来", "后来才", "之后", "转而", "于是开始",
    "学会", "开始", "尝试", "接纳",
    # 渐进转变（需与态度动词直接关联，见 _TRANSFORMATION_STRICT）
    "渐渐", "逐渐", "慢慢", "日子久了", "时日渐久",
})
# 严格模式：渐进词（渐渐/逐渐/慢慢）须与态度动词共现才算铺垫
_TRANSFORMATION_STRICT = frozenset({"渐渐", "逐渐", "慢慢"})
# 态度转变动词：与渐进词共现时才计为铺垫
_ATTITUDE_CHANGE_VERBS = frozenset({
    "接纳", "接受", "习惯", "适应", "学会", "尝试", "开始", "纳", "吸", "循",
})

# 陈老根修炼 register
_CULTIVATION_MARKERS = frozenset({
    "吐纳", "盘膝", "经脉", "行气", "丹田", "调息", "运功", "吐纳呼吸",
})

# P7B：现象词（含新扩展灰黑/污秽/浊物类）
_DEFAULT_PHENOMENON = [
    "浊气", "雾气", "异气", "邪气",
    "灰黑气息", "污秽之气", "黑雾", "灰雾",
    "浊物", "污浊", "瘴气",
]

# P7B：低歧义吸引词（仍需现象近窗，但不需入身绑定）
_DEFAULT_ATTRACT_CRAVING = [
    "吸引", "渴求", "渴望", "向往", "贪恋", "如饥似渴", "甘之如饴",
]

# P7B：高歧义吸引词——仅当严格入身/纳取正则命中才算
_DEFAULT_ATTRACT_INTAKE = [
    "牵引", "钻入", "汲取", "吸纳", "纳气", "纳浊", "循着", "迎合",
    "沉醉", "沉迷", "贪吃", "吞噬",
]

# P7B：排斥词
_DEFAULT_REPEL_MARKERS = [
    "排斥", "避开", "怕脏", "畏惧", "远离", "抗拒", "躲避", "躲开",
]

# 句子窗口大小：排斥事件可跨此数量句子绑定现象词（较宽，因排斥常分散叙述）
_REPEL_SENTENCE_WINDOW = 10
# 吸引事件须与现象词更紧密绑定（±5句）
_ATTRACT_SENTENCE_WINDOW = 5

# P7B：入身/纳取吸引正则
_ATTRACT_INTAKE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("往口鼻钻", re.compile(
        r"(?:往|向)[\s　]*[他其陆焕\s]*"
        r"(?:口鼻[间]?\s*钻|鼻腔\s*钻|呼吸\s*钻|肺\s*钻|怀里\s*(?:钻|吸|涌|渗))"
    )),
    ("纳取浊气", re.compile(r"纳[气浊取]\s*(?:浊|气|黑雾|灰雾)")),
    ("汲取浊气", re.compile(r"(?:汲取|吸纳|纳取|吸入)[^　，。]{0,6}(?:浊|气|黑雾|灰雾)")),
    ("如饥似渴觅食", re.compile(r"如饥似渴[^，。]{0,12}(?:嗅|寻|触)[^，。]{0,6}(?:食物|水源)")),
    ("干渴触水源", re.compile(
        r"(?:干渴|饥饿)[^　，。]{0,12}"
        r"(?:雏鸟|根须)[^　，。]{0,6}"
        r"(?:嗅到|触到)[^　，。]{0,6}(?:食物|水源)"
    )),
    ("循着浊气", re.compile(r"循[着那]?\s*(?:浊|气)")),
    # P7B: 纳 + 那/其/该 + 气息/浊气/气（覆盖"将那气息丝丝纳入"类写法）
    ("纳取气息", re.compile(r"纳.*[那其该].*(?:气息|浊气)|[那其该].*(?:气息|浊气).*纳")),
]


def reset_config_cache() -> None:
    global _CFG_CACHE
    _CFG_CACHE = None


def _load_cfg(root=None) -> dict:
    """加载 config/naming_consistency/*.json 全部配置。"""
    global _CFG_CACHE
    if _CFG_CACHE is not None:
        return _CFG_CACHE
    cfgs: dict = {}
    if root is not None:
        d = Path(root) / "config" / "naming_consistency"
    else:
        d = Path(__file__).parent.parent / "config" / "naming_consistency"
    if not d.exists():
        return cfgs
    for p in sorted(d.glob("*.json")):
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(obj, dict) and "arc" in obj:
                cfgs[obj["arc"]] = obj
        except Exception:
            continue
    _CFG_CACHE = cfgs
    return cfgs


def _split_sentences(text: str) -> list[str]:
    """按中文句号/叹号/问号切句。"""
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


def _find_first_hit(text: str, terms: list[str]) -> tuple[bool, int]:
    """返回 (是否命中, 首次命中位置)。"""
    for t in terms:
        idx = text.find(t)
        if idx >= 0:
            return True, idx
    return False, -1


def _has_phenomenon_in_window(
    sents: list[str], target_idx: int, window: int = _REPEL_SENTENCE_WINDOW
) -> tuple[bool, str]:
    """target_idx 句前后 window 句内是否有现象词。"""
    lo = max(0, target_idx - window)
    hi = min(len(sents), target_idx + window + 1)
    for i in range(lo, hi):
        for ph in _DEFAULT_PHENOMENON:
            if ph in sents[i]:
                return True, ph
    return False, ""


def _has_transformation_paving(
    sents: list[str], repel_idx: int, attract_idx: int
) -> bool:
    """repel_idx 和 attract_idx 之间（含两端）是否有转化铺垫词。

    P7B 严格模式：
    - 同句内：须同时含排斥侧+吸引侧+显式转化结构才豁免
    - 跨句相邻（≤2句）：吸引句自身带转化连接则豁免
    - 跨句远离（>2句）：无豁免（硬反转必报）
    """
    lo, hi = min(repel_idx, attract_idx), max(repel_idx, attract_idx)

    # 同句情况：必须同时含排斥侧+吸引侧+显式转化结构
    if lo == hi:
        sent = sents[lo]
        has_repel_side = any(rm in sent for rm in _DEFAULT_REPEL_MARKERS)
        has_attract_side = (
            any(am in sent for am in _DEFAULT_ATTRACT_CRAVING)
            or any(pat.search(sent) for _, pat in _ATTRACT_INTAKE_PATTERNS)
        )
        if not (has_repel_side and has_attract_side):
            return False
        # 转折锚 + 时间递进 + 渐进学习义 + 纳取，至少满足一组
        has_anchor = any(m in sent for m in {"起初", "原本", "先前", "一开始", "初见"})
        has_progress = any(m in sent for m in {"后来", "之后", "而后", "随后", "日子久了", "时日渐久"})
        has_gradual = any(m in sent for m in _TRANSFORMATION_STRICT) or any(
            m in sent for m in {"竟", "居然", "开始", "学着", "学会", "尝试"}
        )
        has_intake = any(av in sent for av in _ATTITUDE_CHANGE_VERBS)
        if (has_anchor or has_progress) and has_gradual and has_intake:
            return True
        return False

    # 跨句情况
    if hi - lo <= 2:
        # 相邻≤2句：检查吸引句自身是否带转化连接
        attract_sent = sents[hi]
        has_progress_word = any(
            m in attract_sent for m in {"后来", "之后", "而后", "随后", "日子久了", "时日渐久"}
        )
        has_gradual_word = any(m in attract_sent for m in _TRANSFORMATION_STRICT) or any(
            m in attract_sent for m in {"竟", "居然", "学着", "学会", "尝试"}
        )
        has_intake_word = any(av in attract_sent for av in _ATTITUDE_CHANGE_VERBS)
        if has_progress_word and has_gradual_word and has_intake_word:
            return True
        return False

    # 相隔>2句：不豁免硬反转
    return False


def _is_cultivation_sentence(sentence: str) -> bool:
    """句子是否在描述陈老根本人修炼（非婴儿对浊气的感知）。"""
    return ("陈老根" in sentence or "他" in sentence) and any(
        m in sentence for m in _CULTIVATION_MARKERS
    )


def _check_attraction_event(
    sent_idx: int, sentence: str, sents: list[str]
) -> tuple[bool, str]:
    """检查句子是否包含婴儿主动吸引/纳取目标现象的事件。

    P7B 严格规则：
    - craving 词（吸引/渴求等）：现象须在 ±ATTRACT_WINDOW 句内
    - intake 词（牵引/钻入/汲取等）：必须含婴儿主体词 + 现象近窗 + 正则匹配
    """
    # 婴儿主体标记：吸引事件必须有明确婴儿视角
    _BABY_SUBJECTS = frozenset({"陆烬", "婴儿", "那孩子", "婴孩", "他"})

    # 1. 低歧义 craving 词（现象须在 ±ATTRACT_WINDOW 句内）
    for term in _DEFAULT_ATTRACT_CRAVING:
        if term in sentence:
            has_ph, _ = _has_phenomenon_in_window(sents, sent_idx, _ATTRACT_SENTENCE_WINDOW)
            if has_ph:
                return True, f"{term!r} near phenomenon"

    # 2. 高歧义 intake 正则（必须含婴儿主体 + 现象近窗 + 正则匹配）
    # 主体检查：当前句或前3句内有婴儿主体词（覆盖"他/她"指代婴儿的情况）
    _BABY_SUBJECTS = frozenset({"陆烬", "婴儿", "那孩子", "婴孩", "他", "她"})
    has_baby_subject = any(
        s in sentence
        for s in _BABY_SUBJECTS
    ) or any(
        any(s in prev_sent for s in _BABY_SUBJECTS)
        for prev_sent in sents[max(0, sent_idx - 3):sent_idx]
    )
    # 排除眼睛/目光类误报（如"目光被牵引"）
    has_gaze_subject = any(s in sentence for s in {"目光", "视线", "眼"})
    if has_gaze_subject and not has_baby_subject:
        return False, ""
    # CC round-25：未然/设想语境排除——"若是传授…让他学着…或许"是陈老根设想
    # （处理浊气的方案），非婴儿既成的纳取/渴求事件，不得判为吸引。
    _HYPOTHETICAL_MARKERS = frozenset({"若是", "假如", "倘若", "要是", "如果", "或许", "也许", "设想", "盘算", "打算", "计划"})
    if any(m in sentence for m in _HYPOTHETICAL_MARKERS):
        return False, ""
    for label, pat in _ATTRACT_INTAKE_PATTERNS:
        m = pat.search(sentence)
        if m:
            has_ph, _ = _has_phenomenon_in_window(sents, sent_idx, _ATTRACT_SENTENCE_WINDOW)
            if has_ph and has_baby_subject:
                return True, f"{label}: {m.group()[:40]}"
    return False, ""


def _check_repulsion_event(
    sent_idx: int, sentence: str, sents: list[str]
) -> tuple[bool, str]:
    """检查句子是否包含婴儿排斥目标现象的事件。"""
    for term in _DEFAULT_REPEL_MARKERS:
        if term in sentence:
            has_ph, _ = _has_phenomenon_in_window(sents, sent_idx, _REPEL_SENTENCE_WINDOW)
            if has_ph:
                return True, f"{term!r} near phenomenon"
    return False, ""


def detect_naming_conflict(text: str, chapter_num: int, root=None) -> dict:
    """检测命名矛盾。返回 {"conflicts": [...], "has_issue": bool}。"""
    conflicts: list[dict] = []
    cfgs = _load_cfg(root)
    groups: list[dict] = []
    if cfgs:
        ch = int(chapter_num or 0)
        for arc_cfg in cfgs.values():
            rng = arc_cfg.get("chapters")
            if isinstance(rng, list) and len(rng) == 2:
                try:
                    if int(rng[0]) <= ch <= int(rng[1]):
                        groups.extend(arc_cfg.get("naming_conflicts", []))
                except (TypeError, ValueError):
                    continue
    if not groups:
        groups = [{
            "canonical": ["古井"],
            "conflicting": ["枯井"],
            "evidence_terms": ["取水", "打水", "辘轳", "汲水", "水泵", "井水"],
            "desc": "活井（古井）与枯井同章共现且活井证据成立",
        }]
    for group in groups:
        can_hit, _ = _find_first_hit(text, group.get("canonical", []))
        conf_hit, _ = _find_first_hit(text, group.get("conflicting", []))
        ev_terms = group.get("evidence_terms", [])
        ev_hit = bool(ev_terms) and _find_first_hit(text, ev_terms)[0]
        if can_hit and conf_hit and ev_hit:
            conflicts.append({
                "canonical": group["canonical"],
                "conflicting": group["conflicting"],
                "desc": group.get("desc", ""),
            })
    return {"conflicts": conflicts, "has_issue": bool(conflicts)}


def detect_polarity_conflict(text: str, chapter_num: int, root=None) -> dict:
    """检测伏笔极性矛盾。返回 {"conflicts": [...], "has_issue": bool}。

    P7B 严格模式：
    1. 目标现象词必须在本章出现
    2. 排斥/吸引事件与现象词在±3句窗口内绑定
    3. 吸引事件必须是婴儿主动入身或渴求式明喻
    4. 转化铺垫连接词存在则豁免
    5. 陈老根自身修炼排除
    """
    conflicts: list[dict] = []
    cfgs = _load_cfg(root)
    groups: list[dict] = []
    if cfgs:
        ch = int(chapter_num or 0)
        for arc_cfg in cfgs.values():
            rng = arc_cfg.get("chapters")
            if isinstance(rng, list) and len(rng) == 2:
                try:
                    if int(rng[0]) <= ch <= int(rng[1]):
                        groups.extend(arc_cfg.get("polarity_conflicts", []))
                except (TypeError, ValueError):
                    continue
    if not groups:
        groups = [{
            "phenomenon": _DEFAULT_PHENOMENON,
            "repel_markers": _DEFAULT_REPEL_MARKERS,
            "desc": "对同一现象既判排斥又判吸引，无铺垫调和",
        }]

    for group in groups:
        phen_terms = group.get("phenomenon", _DEFAULT_PHENOMENON)
        repel_markers = group.get("repel_markers", _DEFAULT_REPEL_MARKERS)

        # Step 1: 现象词存在性前提
        phen_exists = any(ph in text for ph in phen_terms)
        if not phen_exists:
            continue

        # Step 2: 逐句扫描，收集所有排斥事件和吸引事件
        sents = _split_sentences(text)
        repel_events: list[tuple[int, str]] = []   # (sent_idx, evidence)
        attract_events: list[tuple[int, str]] = []  # (sent_idx, evidence)

        for si, sent in enumerate(sents):
            # 排除陈老根修炼句
            if _is_cultivation_sentence(sent):
                continue

            # 检查排斥事件
            repel_hit, repel_ev = _check_repulsion_event(si, sent, sents)
            if repel_hit:
                repel_events.append((si, repel_ev))

            # 检查吸引事件
            attract_hit, attract_ev = _check_attraction_event(si, sent, sents)
            if attract_hit:
                attract_events.append((si, attract_ev))

        if not repel_events or not attract_events:
            continue

        # Step 3: 检查是否存在转化铺垫（任意排斥句与任意吸引句之间）
        # P7C：ri==ai 也检查（同句显式转化）；跨句仅允许≤2句间隔
        has_paving = False
        for ri, _ in repel_events:
            for ai, _ in attract_events:
                if _has_transformation_paving(sents, ri, ai):
                    has_paving = True
                    break
            if has_paving:
                break
        if has_paving:
            continue

        # Step 4: 检查转折/否定修饰（吸引句本身）
        # P7B：排除"并不是吸引而是排斥"类合法对比结构
        attract_first_idx = min(ai for ai, _ in attract_events)
        attract_sent = sents[attract_first_idx]
        # 若吸引句内同时含排斥词和吸引词，且存在对比连接词，则视为合法对比不报
        has_repel_in_attract_sent = any(
            rm in attract_sent for rm in _DEFAULT_REPEL_MARKERS
        )
        has_attract_in_attract_sent = any(
            am in attract_sent for am in _DEFAULT_ATTRACT_CRAVING
        ) or any(
            pat.search(attract_sent) for _, pat in _ATTRACT_INTAKE_PATTERNS
        )
        _EXPLICIT_CONTRAST = frozenset({"而是", "并非", "并不", "倒非", "非是"})
        if has_repel_in_attract_sent and has_attract_in_attract_sent:
            if any(c in attract_sent for c in _EXPLICIT_CONTRAST):
                continue
        # "不是"仅在紧邻吸引词前（10字内）且无其他现象宾语时才计为对比
        # 排除"那不是哭闹的前兆"类误报（"不是"否定的是"哭闹的前兆"，非吸引）
        if "不是" in attract_sent:
            # 检查"不是"是否紧邻吸引词
            found_contrast = False
            for ci, cm in enumerate(attract_sent):
                if cm in {"吸", "渴", "向", "贪"}:
                    ctx = attract_sent[max(0, ci-10):ci+10]
                    if "不是" in ctx and not any(ph in ctx for ph in _DEFAULT_PHENOMENON):
                        found_contrast = True
                    break
            if found_contrast:
                continue
        # 传统 contrast 标记检查（吸引句内）
        has_contrast = any(m in attract_sent for m in _CONTRAST_MARKERS - {"不是"})
        if has_contrast:
            continue

        # Step 5: 构建冲突
        repel_first = repel_events[0]
        attract_first = attract_events[0]
        conflicts.append({
            "phenomenon": phen_terms,
            "repel_evidence": repel_first[1],
            "attract_evidence": attract_first[1],
            "repel_sentence": sents[repel_first[0]][:80],
            "attract_sentence": sents[attract_first[0]][:80],
            "desc": group.get("desc", ""),
        })

    return {"conflicts": conflicts, "has_issue": bool(conflicts)}


def naming_conflict_fix_directive(conflicts: list[dict]) -> str:
    lines = ["本场景存在命名矛盾："]
    for c in conflicts:
        lines.append(f"  - {c['desc']}（canonical={c['canonical']}, 矛盾={c['conflicting']}）")
    lines.append("统一命名：以 canonical 词为准，删除/改写所有矛盾变体。")
    return "\n".join(lines)


def polarity_conflict_fix_directive(conflicts: list[dict]) -> str:
    lines = ["本场景存在伏笔极性自相矛盾："]
    for c in conflicts:
        lines.append(
            f"  - {c['desc']}（repel={c.get('repel_evidence','')}, "
            f"attract={c.get('attract_evidence','')}）"
        )
    lines.append(
        "显式二选一或补铺垫：明确本章婴儿的感知是排斥还是吸引；"
        "长线真相（吸氧/转化浊气）的首次出现必须显式铺垫，不得与本章直接感官冲突。"
    )
    return "\n".join(lines)
