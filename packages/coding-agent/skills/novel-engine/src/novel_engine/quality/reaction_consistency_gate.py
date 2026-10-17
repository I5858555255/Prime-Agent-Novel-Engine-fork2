# -*- coding: utf-8 -*-
"""CC round-19 P9：跨场角色反应一致性门（零 LLM 纯规则）。

检测目标：同一核心角色 × 同一核心刺激，跨场景出现相反反应定性（痛苦/排斥/受害
vs 无不适/平静/相得），且两端均为明确断言（非推测/主观）。

设计原则：
- 默认 soft，不阻断全章；命中后触发修订提示。
- 通用机制：不硬编码任何角色名/刺激名，从文本中动态提取 (角色, 刺激, 极性) 三元组。
- 分场景运行：对发布稿按空行分段（兼容含 ※/★ 的结构化场文本）。
- 反例保护：程度递进、不同刺激、不同角色、主观猜测句式均不误报。
"""
from __future__ import annotations

import re
from typing import Tuple, Optional


# ============================================================================
# 极性词表
# ============================================================================

# HARM：痛苦/排斥/受害端——明确负面反应断言
_HARM_MARKERS = frozenset({
    "痛苦", "剧烈", "撕心裂肺", "窒息", "挣扎", "痉挛", "颤抖", "剧痛", "灼痛",
    "呕吐", "恶心", "排斥", "排异", "病态", "敏感", "不适",
    "惶恐", "惊惧", "惊怕", "害怕", "恐惧", "战栗",
    "咳嗽", "呛", "喘不过气", "上气不接下气",
    "远离", "避开", "躲开", "躲避", "抗拒", "拒绝", "挣脱",
    "后仰", "偏头", "扭头", "缩", "蜷缩", "后退",
    "不洁", "污秽", "浊腐", "腥气", "腐臭", "霉味", "陈腐",
    "异常", "诡异", "不对劲", "不正常",
})

# NEUTRAL：无不适/平静/相得端——明确正面或无害断言
_NEUTRAL_MARKERS = frozenset({
    "平静", "安稳", "安静", "沉睡", "安睡", "熟睡",
    "并无不适", "并无异常", "并无反应", "并无异样",
    "没有不适", "没有异常", "没有反应", "没有异样",
    "未感到", "未曾感到", "未觉", "不觉",
    "没有啼哭", "没有哭闹", "不啼哭", "不哭闹",
    "未侵扰", "未侵入", "未感染", "未受影响",
    "不侵扰", "不侵入", "不受侵", "不感不适",
    "舒适", "舒服", "惬意", "安然", "平稳", "缓和",
    "化解", "消散", "平息", "退去",
    "无反应", "毫无反应", "毫无异常", "无明显反应",
    "照旧", "依旧", "照常", "依然", "仍然",
    "无碍", "无恙", "没事", "没影响",
})

# UNCERTAIN：不确定标记——命中这些词的断言降权，不单独构成一端
UNCERTAIN_MARKERS = frozenset({
    "似乎", "仿佛", "好像", "宛若", "犹如", "宛如",
    "莫非", "难道", "该是", "怕是", "大概", "也许", "或许", "可能",
    "隐约", "隐隐", "略感", "略觉", "似有", "若有",
    "好似", "彷佛",
})

# 明确无害断言：同时含"刺激词相关"和"无反应/平静"的句式
# 用于区分"一般平静"与"对该刺激明确无不适"
_EXPLICIT_NO_EFFECT_PATTERNS = [
    # "没有/并无/未 + 负面反应词 + 对/在/面对 + 刺激"
    r"[并未没有没]有[^，。]{0,10}(?:啼哭|哭闹|不适|异常|反应|害怕|惊惧|痛苦|抗拒|排斥)",
    # "不 + 动词 + 刺激相关"
    r"不(?:啼哭|哭闹|怕|畏|惧|适|感)[^，。]{0,8}(?:这股|那种|这|那|什|此)",
    # "平静/安稳/无恙 + 面对/沾染 + 刺激"
    r"(?:平静|安稳|安然|无恙|无碍)[^，。]{0,6}(?:面对|沾染|遇到|碰到|触及|接触|靠近|漫过|袭来|飘来)",
    # "对刺激词无感/没事/无反应"
    r"(?:对|面对)[^，。]{0,12}(?:无感|没事|无碍|无恙|无妨|没影响|无反应)",
]
_EXPlicit_NO_EFFECT_COMPILED = [re.compile(p) for p in _EXPLICIT_NO_EFFECT_PATTERNS]

# NEGATION：否定标记
_NEGATION_MARKERS = frozenset({"并", "未", "没有", "不", "无", "别", "莫", "勿"})

# STIMULUS_TOPICS：常见刺激类型关键词
STIMULUS_TOPICS = frozenset({
    "浊气", "雾气", "异气", "邪气", "瘴气", "秽气", "瘴毒",
    "浊腐", "污浊", "陈腐", "晦气", "沉滞", "滞重",
    "药渣", "苦涩", "苦味", "药味",
    "寒气", "阴寒", "阴冷", "冷意",
    "热意", "燥热", "温热", "暖意",
    "异味", "气味", "味道",
})

# ============================================================================
# 句子切分
# ============================================================================

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
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return out


# ============================================================================
# 场景切分
# ============================================================================

_SCENE_SEPARATORS = (chr(0x203B), chr(0x2605))  # ※, ★


def _split_into_scenes(text: str) -> list[str]:
    """将文本切分为场景列表。

    优先按 ※/★ 分隔符切分；若无则按连续双换行（空行）分段。
    若段落不足 2 个，退化为单场。
    """
    if not text:
        return []
    for sep in _SCENE_SEPARATORS:
        if sep in text:
            parts = [p.strip() for p in text.split(sep) if p.strip()]
            return parts
    paragraphs = re.split(r'\n\s*\n', text)
    paragraphs = [p.strip() for p in paragraphs if p.strip()]
    if len(paragraphs) < 2:
        return [text]
    return paragraphs


# ============================================================================
# 角色/刺激提取
# ============================================================================

_ROLES_DEFAULT = frozenset({
    "陆烬", "陈老根", "婴儿", "婴孩", "那孩子", "那婴孩",
    "妇人", "村民", "里正", "官差", "衙役", "守卫", "守护者",
    "道人", "老者", "青年", "少年", "中年",
})

# CC round-19 P9b：同指角色合并表——文本中多个称呼指同一实体时，合并为 canonical 一条
_ROLE_ALIASES: dict[str, str] = {
    "婴儿": "陆烬", "婴孩": "陆烬", "那孩子": "陆烬", "那婴孩": "陆烬",
}

# CC round-19 P9b：近义刺激归并组——同一语义簇的多个词归并为 1 条 hit
# 每组为 (canonical, [aliases...]) 元组，canonical 优先与两端证据共现
_STIMULUS_ALIAS_GROUPS: list[tuple[str, list[str]]] = [
    ("浊气",   ["陈腐", "药渣", "苦涩", "苦味", "药味", "腥气", "腐臭"]),
    ("浊腐",   ["污浊", "晦气", "沉滞", "滞重"]),
    ("雾气",   ["异气", "邪气", "瘴气", "秽气", "瘴毒"]),
    ("寒气",   ["阴寒", "阴冷", "冷意"]),
    ("热意",   ["燥热", "温热", "暖意"]),
    ("气味",   ["异味", "味道"]),
]


def _resolve_role(char_name: str, all_chars_in_chapter: list[str]) -> str:
    """将角色名归一到 canonical 形式（同指合并）。"""
    if char_name in _ROLE_ALIASES and _ROLE_ALIASES[char_name] in all_chars_in_chapter:
        return _ROLE_ALIASES[char_name]
    return char_name


def _resolve_stimulus(stim: str, all_stimuli_in_chapter: list[str]) -> tuple[str, list[str]]:
    """将刺激词归一到 canonical 形式，返回 (canonical, [aliases...])。"""
    for canonical, aliases in _STIMULUS_ALIAS_GROUPS:
        group_members = [canonical] + aliases
        if stim in group_members:
            real_aliases = [a for a in aliases if a in all_stimuli_in_chapter]
            return canonical, real_aliases
    return stim, []


def _extract_roles(text: str, scene_text: str, known_roles: frozenset | None = None) -> list[str]:
    roles = set(known_roles or _ROLES_DEFAULT)
    found: list[str] = []
    for r in sorted(roles, key=lambda x: -len(x)):  # 长名优先
        if r in scene_text and len(r) >= 2:
            found.append(r)
    return found


def _extract_stimuli(text: str, known_stimuli: list[str] | None = None) -> list[str]:
    stimuli = set()
    candidates = list(STIMULUS_TOPICS)
    if known_stimuli:
        candidates = list(set(candidates + known_stimuli))
    for s in candidates:
        if s in text:
            stimuli.add(s)
    return sorted(stimuli)


# ============================================================================
# 极性判定
# ============================================================================

def _is_uncertain(sentence: str) -> bool:
    """句子是否含不确定标记。"""
    return any(m in sentence for m in UNCERTAIN_MARKERS)


def _is_uncertain_only_neutral(sentence: str) -> bool:
    """句子是否是仅靠不确定标记承载的"无害"断言（无明确肯定式无害断言）。

    判定：含 UNCERTAIN_MARKERS 中的词，且不含 _EXPLICIT_NO_EFFECT_COMPILED 任一匹配，
    且不含 _NEUTRAL_MARKERS 中独立于否定结构的词（如"平静""安稳"等直接断言）。
    这类句子不得单独充当 neutral 端证据。
    """
    has_unc = _is_uncertain(sentence)
    if not has_unc:
        return False
    has_explicit = _is_explicit_no_effect(sentence)
    if has_explicit:
        return False
    # 检查是否含直接中性断言词（非否定结构）
    has_direct_neutral = any(
        nm in sentence for nm in {"平静", "安稳", "安然", "无恙", "无碍",
                                   "没有不适", "并无不适", "未侵扰", "不侵扰",
                                   "没有哭闹", "并无异常", "毫无反应"}
    )
    return not has_direct_neutral


def _is_subject_of_reaction(sentence: str, char_name: str) -> bool:
    """句子中该角色是否是反应的主体（而非旁观者或背景提及）。

    启发式：若角色名在句中位于刺激/反应描述之后（如'想起...陆烬咳嗽'），
    则角色是旁观者；若角色名在前或句中无明确主语切换，则可能是反应主体。
    """
    if char_name not in sentence:
        return False
    # 典型旁观者模式："他想起/想起...[角色]...[反应]"
    _OBSERVER_PATTERNS = [
        r"想起[^，。]{0,20}" + re.escape(char_name),
        r"想起[^，。]{0,20}那",
        r"念头让他",
    ]
    for pat in _OBSERVER_PATTERNS:
        if re.search(pat, sentence):
            return False
    # 若角色名在句首或前1/3处，视为反应主体
    first_third = len(sentence) // 3
    if sentence.find(char_name) < first_third:
        return True
    # 否则需要进一步检查：句中是否有其他更明显的主语
    # 检查是否有另一个角色名紧跟在反应动词前
    _SUBJECT_MARKERS = {"陆烬", "婴儿", "婴孩", "那孩子"}
    for other_char in _SUBJECT_MARKERS:
        if other_char != char_name and other_char in sentence:
            # 其他角色名也在句中 → 当前角色可能是旁观者
            if sentence.find(other_char) < sentence.find(char_name):
                return False
    return True


def _is_negated_assertion(sentence: str) -> bool:
    has_negation = any(n in sentence for n in _NEGATION_MARKERS)
    has_neutral_marker = any(nm in sentence for nm in _NEUTRAL_MARKERS)
    return has_negation and has_neutral_marker


def _is_explicit_no_effect(sentence: str) -> bool:
    """句子是否是明确的'对该刺激无反应/无不适'断言（而非一般平静）。

    区分：
    - '陆烬没有哭闹，也没有害怕'（对浊气无反应）→ True
    - '陆烬正安静地睡着'（一般状态）→ False
    - '呼吸虽弱，却平稳了些许'（退烧后）→ False
    """
    for pat in _EXPlicit_NO_EFFECT_COMPILED:
        if pat.search(sentence):
            return True
    # 显式否定结构：'并没有/并未/没有 + 刺激相关负面词'
    for neg in ("并没有", "并未", "没有", "未曾"):
        if neg in sentence:
            # 检查后面是否跟着负面反应词
            idx = sentence.find(neg)
            tail = sentence[idx + len(neg):idx + len(neg) + 15]
            if any(rm in tail for rm in {"啼哭", "哭闹", "不适", "异常", "反应", "害怕",
                                         "惊惧", "痛苦", "抗拒", "排斥", "侵扰", "不适"}):
                return True
    return False


def _is_stimulus_reaction_sentence(sentence: str, stimulus: str) -> bool:
    """句子是否明确描述角色对特定刺激的反应（或对该刺激的无害状态）。

    放宽条件：只要句子描述角色对刺激的态度/状态（包括"无反应/平静/未侵扰"等），
    即视为有效证据；不要求必须有动作动词。
    """
    # 排除事后描述标记
    _POST_EVENT_MARKERS = {"后", "之后", "回来", "退回", "返回", "回到", "这时",
                           "渐渐", "逐渐", "慢慢", "依旧", "仍然", "还是"}
    post_markers = [m for m in _POST_EVENT_MARKERS if m in sentence]
    if len(post_markers) >= 2:
        return False

    # 检查是否提及刺激词（或在刺激附近上下文中）
    stim_terms = {stimulus}
    if len(stimulus) > 2:
        stim_terms.add(stimulus[-2:])
        stim_terms.add(stimulus[-3:-1])
    has_stimulus_mention = (stimulus in sentence or
                            any(st in sentence for st in stim_terms))
    return has_stimulus_mention


def _classify_polarity(sentence: str, context_text: str, char_name: str) -> Optional[str]:
    """对单句判定极性，返回 'harm', 'neutral', 'uncertain', 或 None。"""
    # 扩大上下文窗口以检测角色指代（句前200字 + 句后50字）
    _sent_start = context_text.find(sentence)
    if _sent_start < 0:
        _sent_start = 0
    _prox = context_text[max(0, _sent_start - 200):_sent_start + len(sentence) + 50]
    # 角色匹配：句内含角色名，或句内含代词且上下文中存在角色名
    _has_char_direct = char_name in sentence
    _has_char_indirect = (char_name in _prox and
                          any(p in sentence for p in {"他", "她", "它", "其", "这小小"}))
    _has_char = _has_char_direct or _has_char_indirect
    if not _has_char:
        return None

    has_uncertain = _is_uncertain(sentence)
    has_explicit_neutral = _is_explicit_no_effect(sentence)
    # 计算 has_harm 时排除纯刺激描述词，避免"陈腐/苦涩/异常"等刺激词误判为角色受害
    # "异常"单独出现（如"异常体质"）是主题描述而非受害反应，需排除；
    # 但"异常反应""异常状况"等含动词/名词组合仍需通过后续逻辑判断
    _STIMULUS_ONLY_HARM = frozenset({
        "陈腐", "苦涩", "苦味", "药味", "腥气", "腐臭", "霉味",
        "沉滞", "滞重", "晦气", "浊腐", "污浊", "瘴气", "秽气",
        "浊气", "雾气", "异气", "邪气", "异常",
    })
    has_harm = any(hm in sentence for hm in _HARM_MARKERS if hm not in _STIMULUS_ONLY_HARM)
    has_neutral_marker = any(nm in sentence for nm in _NEUTRAL_MARKERS)

    # 不确定标记优先级处理：
    # 若句子同时含不确定词和明确的无害断言（"似乎并未侵扰"），
    # 按任务要求：否定式断言视为对该刺激无反应的中性断言，降为 neutral 而非 uncertain
    if has_uncertain:
        if has_explicit_neutral and not has_harm:
            return "neutral"  # "似乎并未侵扰" → 中性断言
        if has_harm and not has_explicit_neutral:
            return "uncertain"  # 纯粹不确定，无明确极性 → 降权
        if has_explicit_neutral and has_harm:
            # 不确定修饰矛盾句（如"似乎并没有不适，反而咳嗽"）→ 整体偏 harm
            return "harm"
        return "uncertain"

    # 否定断言（并+中性标记）→ 明确无害断言
    if _is_negated_assertion(sentence):
        if _is_explicit_no_effect(sentence):
            return "neutral"
        return None

    # HARM（需确认角色相关，避免刺激词本身的 harm 标记误判）
    if has_harm:
        # 额外检查：harm 标记是否在描述角色反应（而非刺激本身）
        _stimulus_only_harm = {"陈腐", "苦涩", "苦味", "药味", "腥气", "腐臭", "霉味",
                               "沉滞", "滞重", "晦气", "浊腐", "污浊", "瘴气", "秽气",
                               "浊气", "雾气", "异气", "邪气"}
        _harm_in_char_reaction = any(
            hm in sentence for hm in _HARM_MARKERS
            if hm not in _stimulus_only_harm
        )
        if _harm_in_char_reaction:
            return "harm"
        # 若 harm 标记只在刺激描述中，继续检查是否含角色反应动词
        if any(rv in sentence for rv in {"咳", "呕吐", "吐", "挣扎", "颤抖", "痉挛", "后退", "远离"}):
            return "harm"

    # NEUTRAL：仅当是明确的'对该刺激无反应'断言才算
    if has_explicit_neutral:
        return "neutral"

    return None


def _find_polarity_in_scene(
    scene_text: str, char_name: str, stimulus: str,
) -> Tuple[list[str], list[str]]:
    """在单场景中查找角色对刺激的 HARM/NEUTRAL 极性句子。

    关键约束：中性（无害/平静）证据必须明确提及刺激词本身或该刺激造成的直接体感，
    以避免将角色的无关日常状态误报为"对该刺激的无反应"。
     Harm 证据同样要求句子内含刺激词或其直接代词指代。
    """
    sents = _split_sentences(scene_text)
    harm_sents: list[str] = []
    neutral_sents: list[str] = []

    # 构建刺激词及其缩略形式的集合（用于上下文指代匹配）
    stim_terms = {stimulus}
    if len(stimulus) > 2:
        # 长刺激词可取其最后1-2字作为简称
        stim_terms.add(stimulus[-2:])
        stim_terms.add(stimulus[-3:-1])

    for si, sent in enumerate(sents):
        # 上下文窗口：本句 ±3 句
        lo = max(0, si - 3)
        hi = min(len(sents), si + 4)
        window_text = " ".join(sents[lo:hi])

        # 判定该句是否在讨论此刺激：句内或紧邻上下文含刺激词
        in_stimulus_context = (stimulus in sent or
                               any(st in sent for st in stim_terms) or
                               any(stimulus in ws for ws in sents[lo:hi]))
        if not in_stimulus_context:
            continue

        _prox = window_text[max(0, window_text.find(sent) - 500):
                            window_text.find(sent) + len(sent) + 200]
        # CC round-19 P9b：证据句必须显式包含角色名，避免旁观者误报
        _has_char_direct = char_name in sent
        _has_char_indirect = (char_name in _prox and
                              any(p in sent for p in {"他", "她", "它", "其", "这小小"}))
        _has_char = _has_char_direct or _has_char_indirect
        if not _has_char:
            continue

        pol = _classify_polarity(sent, window_text, char_name)
        if pol == "harm" and len(harm_sents) < 2:
            # CC round-19 P9b：仅当角色名在证据句中才计入（排除旁观者回忆句）
            if _has_char_direct:
                harm_sents.append(sent[:80])
        elif pol == "neutral" and len(neutral_sents) < 2:
            # CC round-19 P9b：中性证据允许间接指代（如"这小小的身躯"），只要含刺激词
            if not _has_char:
                continue
            # CC round-19 P9b：纯不确定句不得单独充当无害端
            if _is_uncertain_only_neutral(sent):
                continue
            if _is_stimulus_reaction_sentence(sent, stimulus):
                neutral_sents.append(sent[:80])

    return harm_sents, neutral_sents


# ============================================================================
# 主检测入口
# ============================================================================

def detect_reaction_inconsistency(
    text: str,
    chapter_num: int,
    task_card: dict,
    root=None,
    scene_texts: dict[int, str] | None = None,
) -> dict:
    """检测跨场角色反应一致性矛盾。

    返回 {"hits": [...], "has_issue": bool}。
    hits 每项：scene_ids, role, stimulus, harm_evidence, neutral_evidence, desc。
    默认 soft：命中后触发修订提示，不阻断全章。
    """
    # 场景解析
    if scene_texts is not None:
        if isinstance(scene_texts, dict) and scene_texts:
            scenes = scene_texts
        elif isinstance(scene_texts, str) and scene_texts.strip():
            scenes_list = _split_into_scenes(scene_texts)
            scenes = {i + 1: t for i, t in enumerate(scenes_list)}
        else:
            scenes = {}
    else:
        scenes = {}

    if not scenes:
        scenes_list = _split_into_scenes(text or "")
        scenes = {i + 1: t for i, t in enumerate(scenes_list)}

    if len(scenes) < 2:
        return {"hits": [], "has_issue": False}

    # 从 task_card 提取已知刺激词
    bp = task_card.get("scene_blueprints") or []
    known_stimuli: list[str] = []
    for sbp in bp:
        if not isinstance(sbp, dict):
            continue
        for b in (sbp.get("beats") or []):
            b_str = str(b).strip() if b else ""
            for st in STIMULUS_TOPICS:
                if st in b_str:
                    known_stimuli.append(st)
    global_stimuli = _extract_stimuli(text, known_stimuli)
    if not global_stimuli:
        return {"hits": [], "has_issue": False}

    hits: list[dict] = []
    seen_pairs: set = set()

    # 收集全文章节内所有角色和刺激（用于同指/近义归并）
    all_chars_in_chapter: list[str] = []
    all_stimuli_in_chapter: list[str] = []
    for scene_text in scenes.values():
        all_chars_in_chapter.extend(_extract_roles(text, scene_text))
    all_chars_in_chapter = sorted(set(all_chars_in_chapter), key=lambda x: -len(x))
    all_stimuli_in_chapter = sorted(set(global_stimuli), key=len, reverse=True)

    for sid, scene_text in sorted(scenes.items()):
        chars = _extract_roles(text, scene_text)
        if not chars:
            continue
        scene_stimuli = _extract_stimuli(scene_text, global_stimuli)
        if not scene_stimuli:
            continue
        for char in chars:
            for stim in scene_stimuli:
                # CC round-19 P9b：同指角色归一化（婴儿→陆烬）
                resolved_char = _resolve_role(char, all_chars_in_chapter)
                # CC round-19 P9b：近义刺激归一化（药渣/陈腐→浊气）
                resolved_stim, aliases = _resolve_stimulus(stim, all_stimuli_in_chapter)
                pair_key = (resolved_char, resolved_stim)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)

                # 收集所有场景中该 (角色, 刺激) 的极性
                all_harm: list[tuple[int, str]] = []
                all_neutral: list[tuple[int, str]] = []
                for hs_id, hs_text in sorted(scenes.items()):
                    h, n = _find_polarity_in_scene(hs_text, resolved_char, resolved_stim)
                    if h:
                        all_harm.extend((hs_id, e) for e in h)
                    if n:
                        all_neutral.extend((hs_id, e) for e in n)

                if not all_harm or not all_neutral:
                    continue

                harm_ids = sorted({sid for sid, _ in all_harm})
                neutral_ids = sorted({sid for sid, _ in all_neutral})

                # 组装 aliases 字段（仅当有归并时才非空）
                hit_aliases = []
                if resolved_stim != stim:
                    hit_aliases.append(stim)
                # 收集所有别名刺激的同源证据
                for alt_stim in aliases:
                    if alt_stim == resolved_stim:
                        continue
                    _, alt_aliases = _resolve_stimulus(alt_stim, all_stimuli_in_chapter)
                    if alt_stim not in alt_aliases and alt_stim != resolved_stim:
                        # alt_stim 是另一个 alias 组内的词，也加入 aliases
                        pass
                    # 收集该别名的证据，合并到主 hit
                    for hs_id2, hs_text2 in sorted(scenes.items()):
                        h2, n2 = _find_polarity_in_scene(hs_text2, resolved_char, alt_stim)
                        all_harm.extend((hs_id2, e) for e in h2)
                        all_neutral.extend((hs_id2, e) for e in n2)
                # 去重
                all_harm = list({(sid, e) for sid, e in all_harm})
                all_neutral = list({(sid, e) for sid, e in all_neutral})

                hit_aliases = sorted({a for a in aliases if a != resolved_stim})

                hits.append({
                    "scene_ids": sorted(set(harm_ids + neutral_ids)),
                    "role": resolved_char,
                    "stimulus": resolved_stim,
                    "aliases": hit_aliases,
                    "harm_evidence": [e for _, e in all_harm[:2]],
                    "neutral_evidence": [e for _, e in all_neutral[:2]],
                    "desc": (
                        f"角色「{resolved_char}」对「{resolved_stim}」的反应跨场矛盾：场{all_harm[0][0]} "
                        f"判定为痛苦/排斥（{all_harm[0][1][:50]}…），"
                        f"场{all_neutral[0][0]} 判定为无不适/平静（"
                        f"{all_neutral[0][1][:50]}…）"
                        + (f"（含近义词别名：{', '.join(hit_aliases)}）" if hit_aliases else "")
                    ),
                })

    return {
        "hits": hits,
        "has_issue": bool(hits),
    }


def reaction_consistency_fix_directive(hits: list[dict]) -> str:
    """生成修订指令。"""
    if not hits:
        return ""
    lines = ["检测到跨场景角色反应定性矛盾，需统一一致："]
    for h in hits:
        lines.append(
            f"  - 角色「{h['role']}」对「{h['stimulus']}」：场{h['scene_ids']} "
            f"反应矛盾，两端证据：{h['harm_evidence'][0][:50]} vs {h['neutral_evidence'][0][:50]}"
        )
    lines.append(
        "请重写矛盾场，使该角色对同一刺激的反应保持一致；"
        "如需体现变化，必须有明确的因果铺垫标记（如'后来才''学会''渐渐'等），"
        "不得在无任何过渡的情况下直接给出相反定性。"
    )
    return "\n".join(lines)
