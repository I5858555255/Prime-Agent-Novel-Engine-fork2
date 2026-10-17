# -*- coding: utf-8 -*-
"""CC round-8 P0-2：章内时间/因果一致性门（纯规则，零 LLM）。

两类问题：

1. 分场时序（sequence_index）：导演为每个场景声明故事时间序号。assembly 拼接必须
   严格按 sequence_index 升序；序号缺失/重复导致无法确定排序时，判任务卡规划失败，
   交导演绕过缓存重新规划（不在本章猜序）。

2. "提前演后果"（state pre-depiction）：某场景的 do_not_depict_before 声明了"只有
   更晚场景才成立的实体状态"。assembly 后用关键词共现规则检测该场景正文是否已呈现
   该状态；命中判疑似倒置。除导演自带的 do_not_depict_keywords 外，本模块内置一组
   极窄的种子规则（婴儿被发现前不得已在他人手中），覆盖真实出现过的倒置案例。

本模块全部为可离线单测的纯函数；命中后的定点重生/重排由 orchestrator 执行。
"""
from __future__ import annotations

# 内置种子规则：仅在"更晚场景的蓝图确实要去发现同一实体"时，才检查更早场景是否
# 已经让他人【持有】该实体。命中必须包含一个"持有/交接"词（怀里/抱着/抱起/递给…），
# 仅凭"村民/人群围观 + 婴儿同场"不算（婴儿降生后躺在焦土、村民远观并非被人持有），
# 以收窄误报。groups 为 AND 组（同组词全部出现才命中），至少两个词。
SEED_RULES: list[dict] = [
    {
        "entity": ["婴儿", "婴孩", "襁褓", "孩子"],
        "find": ["捡", "拾", "发现", "找到", "抱回", "拾起", "寻得", "抱来", "抱起"],
        "groups": [
            ["婴儿", "怀里"], ["婴孩", "怀里"], ["襁褓", "怀里"], ["孩子", "怀里"],
            ["婴儿", "怀中"], ["婴孩", "怀中"], ["襁褓", "怀中"], ["孩子", "怀中"],
            ["婴儿", "抱着"], ["婴孩", "抱着"], ["襁褓", "抱着"], ["孩子", "抱着"],
            ["婴儿", "抱住"], ["婴孩", "抱住"], ["孩子", "抱住"],
            ["婴儿", "抱起"], ["婴孩", "抱起"], ["孩子", "抱起"],
            ["婴儿", "抱紧"], ["婴孩", "抱紧"], ["孩子", "抱紧"],
            ["婴儿", "搂在"], ["孩子", "搂在"],
            ["婴儿", "递给"], ["婴孩", "递给"], ["孩子", "递给"],
            ["婴儿", "接过"], ["孩子", "接过"],
            ["婴儿", "抱回"], ["婴儿", "抱来"],
            ["婴儿", "手中"], ["孩子", "手中"],
        ],
    },
]


def _as_str_list(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    return []


# CC round-10：共现命中豁免。村民围绕"要不要把孩子抱走"的对话（引号内）以及
# 否定/将来/假设语气（不敢抱/想抱又缩手/若有人抱起）都不是"实体已被持有"的既成事实，
# 不能判倒置；只有非引号、无此类标记窗口内的肯定性共现才算命中。
_NEG_HYP_MARKERS = (
    # 明确否定（不用裸"不/没"，以免"不再犹豫/不由得/勇敢"等肯定语境被豁免）
    "不敢", "不能", "不可", "不得", "不想", "不肯", "不愿", "不许", "不会",
    "并未", "并没有", "没有", "还没", "尚未", "未曾", "毫不", "并不", "绝非", "绝非",
    "别", "莫", "勿", "休想",
    # 假设 / 将来 / 意图（未发生）
    "想要", "想把", "想将", "想上", "想伸", "心里想", "欲", "若", "若是", "倘", "倘若",
    "假如", "如果", "万一", "打算", "准备", "试图", "寻思", "盘算", "想要",
    # 反问（迟疑/退缩本身不取消已完成的动作，故不纳入，以免"不再犹豫还是抱了"漏报）
    "谁敢", "哪敢", "怎敢", "岂能", "怎能", "难道",
)
_QUOTE_OPENERS = {"“": "”", "‘": "’", "「": "」", "『": "』", '"': '"', "'": "'"}


def _mask_quotes(text: str) -> str:
    """把成对引号（含直引号）内的字符替换为空白，得到"仅叙事"文本。"""
    out: list[str] = []
    inside = False
    closer = ""
    for ch in text or "":
        if not inside and ch in _QUOTE_OPENERS:
            inside = True
            closer = _QUOTE_OPENERS[ch]
            out.append("　")
            continue
        if inside:
            out.append("　")
            if ch == closer:
                inside = False
                closer = ""
            continue
        out.append(ch)
    return "".join(out)


def _affirmative_cooccurrence(text: str, terms: list[str], window: int = 20) -> bool:
    """非引号、且无否定/假设/将来标记窗口内，组内词是否在邻近范围内肯定性共现。"""
    masked = _mask_quotes(text or "")
    others_all = terms

    def _segment_has_all(seg: str) -> bool:
        return all(t in seg for t in others_all)

    for term in terms:
        start = 0
        while True:
            pos = masked.find(term, start)
            if pos < 0:
                break
            seg = masked[max(0, pos - window): pos + len(term) + window]
            if _segment_has_all(seg) and not any(m in seg for m in _NEG_HYP_MARKERS):
                return True
            start = pos + len(term)
    return False



def _as_kw_groups(value) -> list[list[str]]:
    """do_not_depict_keywords: list[list[str]]，丢弃空组/单词组（单词组误报率过高）。"""
    groups: list[list[str]] = []
    if not isinstance(value, list):
        return groups
    for g in value:
        if isinstance(g, list):
            terms = [str(x).strip() for x in g if str(x).strip()]
            if len(set(terms)) >= 2:
                groups.append(sorted(set(terms)))
    return groups


def normalize_sequence(blueprints: list[dict]) -> tuple[dict, bool]:
    """返回 (scene_id -> sequence_index 映射, 是否可唯一排序)。

    sequence_index 缺失时回退为 scene_num；重复或非正数则不可唯一排序。
    """
    order: dict[int, int] = {}
    seq_seen: list[int] = []
    for i, bp in enumerate(blueprints or []):
        sid = int(bp.get("scene_num", i + 1) or (i + 1))
        raw = bp.get("sequence_index")
        try:
            seq = int(raw)
        except (TypeError, ValueError):
            seq = sid
        if seq <= 0:
            seq = sid
        order[sid] = seq
        seq_seen.append(seq)
    ok = len(seq_seen) == len(set(seq_seen))
    return order, ok


def build_specs(scenes: list, blueprints_by_id: dict) -> list[dict]:
    """把 SceneOutput 列表与蓝图合并为检测用规格（按 sequence_index 升序）。"""
    specs: list[dict] = []
    for s in scenes or []:
        sid = getattr(s, "scene_id", None)
        if sid is None:
            sid = s.get("scene_id", 0) if isinstance(s, dict) else 0
        sid = int(sid or 0)
        text = getattr(s, "scene_text", None)
        if text is None:
            text = s.get("scene_text", "") if isinstance(s, dict) else ""
        bp = blueprints_by_id.get(sid, {}) or {}
        try:
            seq = int(bp.get("_seq", bp.get("sequence_index", sid)) or sid)
        except (TypeError, ValueError):
            seq = sid
        goal = str(bp.get("goal", "") or "")
        beats = "；".join(_as_str_list(bp.get("beats")))
        specs.append({
            "scene_id": sid,
            "seq": seq,
            "text": str(text or ""),
            "states": _as_str_list(bp.get("do_not_depict_before")),
            "kw_groups": _as_kw_groups(bp.get("do_not_depict_keywords")),
            "bp_text": f"{goal}；{beats}",
        })
    specs.sort(key=lambda x: (x["seq"], x["scene_id"]))
    return specs


def _later_scene_discovers(spec: dict, entity_terms: list[str], find_verbs: list[str]) -> bool:
    """该（更晚）场景蓝图是否包含'发现/抱起某实体'的因果动作。"""
    bp = spec["bp_text"]
    has_entity = any(t in bp for t in entity_terms)
    has_find = any(v in bp for v in find_verbs)
    return has_entity and has_find


def _blueprint_authorized_holding(bp_text: str, rule: dict) -> bool:
    """CC round-22：该场景蓝图(goal+beats)是否在肯定语境已确立'实体被人持有'。

    用于把"按任务卡演出的怀抱/承接上一章末已抱起"与 writer 擅自提前演后果区分开：
    蓝图中实体词与任一持有词（怀里/抱着/抱起/递给…）在邻近窗口肯定共现即视为授权。
    """
    bp = bp_text or ""
    if not any(e in bp for e in rule["entity"]):
        return False
    carry_words = sorted({
        w for g in rule["groups"] for w in g if w not in rule["entity"]
    })
    for e in rule["entity"]:
        for cw in carry_words:
            if _affirmative_cooccurrence(bp, [e, cw]):
                return True
    return False


def detect_inversions(specs: list[dict], use_director_keywords: bool = False,
                      prior_acquired_entities: set[str] | None = None) -> list[dict]:
    """检测每个场景是否提前呈现了更晚场景才成立的实体状态。

    返回 [{scene_id, state, matched, source}]；source 为 director / seed。

    use_director_keywords：是否采信导演自报的 do_not_depict_keywords 共现组。
    真机实测导演常给"环境词共现"（如发现场自身必含 迷雾/陈老根），误报率高且无法
    通过重生消除，故 CC round-8 预案默认关闭该通道（False），只启用经过离线收窄的
    内置种子规则；导演的 do_not_depict_before 文字状态仍照常注入 writer prompt。

    prior_acquired_entities：本章开始前（上一章末）已被发现并获得的实体集合。
    命中此集合的 seed 规则直接跳过，避免"上一章已抱回婴儿、本章延续怀抱"误判倒置。
    """
    _prev_acquired: set[str] = set(prior_acquired_entities) if prior_acquired_entities else set()
    inversions: list[dict] = []
    ordered = sorted(specs or [], key=lambda x: (x["seq"], x["scene_id"]))

    for i, spec in enumerate(ordered):
        text = spec["text"] or ""
        later = ordered[i + 1:]

        # (1) 导演显式关键词组（默认关闭：模型自报关键词实测不可靠）
        if use_director_keywords:
            for group in spec.get("kw_groups", []):
                if all(term in text for term in group):
                    state_hit = "、".join(spec.get("states", [])[:2]) or "更晚场景才成立的状态"
                    inversions.append({
                        "scene_id": spec["scene_id"],
                        "state": state_hit,
                        "matched": group,
                        "source": "director",
                    })
                    break

        # (2) 内置种子：存在更晚场景要'发现'同一实体时，本场景不得已让他人持有
        bp_text = str(spec.get("bp_text", "") or "")
        for rule in SEED_RULES:
            # CC round-11 R1：若本规则任一实体已在上一章被获得，本章任何该实体的画面均非倒置
            if _prev_acquired:
                rule_entities = set(rule["entity"])
                if rule_entities & _prev_acquired:
                    continue
            discovers_later = any(
                _later_scene_discovers(j, rule["entity"], rule["find"]) for j in later)
            if not discovers_later:
                continue
            hit_group = next((g for g in rule["groups"]
                              if _affirmative_cooccurrence(text, g)), None)
            if hit_group is None:
                continue
            # CC round-22：持有状态只会被"提前"一次。若本场景蓝图已授权持有，或本章
            # 更早场景蓝图已确立持有（含承接上一章末已抱起的首场授权），此处只是延续，
            # 不判倒置；只有持有尚未被任何更早/本场景蓝图确立时，提前持有才算倒置。
            if _blueprint_authorized_holding(bp_text, rule):
                continue
            if any(_blueprint_authorized_holding(str(k.get("bp_text", "") or ""), rule)
                   for k in ordered if k["seq"] < spec["seq"]):
                continue
            inversions.append({
                "scene_id": spec["scene_id"],
                "state": "实体在被发现前已出现在他人手中（时序因果倒置）",
                "matched": hit_group,
                "source": "seed",
            })
            break

    # 同一场景只报一次（director 优先）
    dedup: dict[int, dict] = {}
    for inv in inversions:
        dedup.setdefault(inv["scene_id"], inv)
    return list(dedup.values())


# CC round-11 R1b：从上一章 end_state 提取已获得的实体关键词集合。
# 动词必须真正表示「获得/占有/承接」；严禁混入发现/看见/窥见/目击/察觉等纯认知词，
# 否则 ch1（仅村民发现/陈老根窥见）会误提取实体，导致 ch2 真获得章的倒置检测被抑制。
_PRIOR_ACQUIRED_VERBS = (
    "收养", "拾婴", "拾回", "抱回", "捡到", "找到", "获得", "领养",
    "收留", "决定收留", "抱离",
    # R1b：补齐真实数据中的常见变体
    "带回", "抱起", "带走", "接回",
    "抱回家", "抱回屋", "抱回去", "抱起带回",
)
# 明确排除的纯认知动词（不能当作获得）：
_EXCLUDED_COGNITIVE_VERBS = ("发现", "看见", "窥见", "目击", "察觉", "见到", "望见")
_PRIOR_ENTITY_HINTS = ("婴儿", "婴孩", "襁褓", "孩子", "弃婴", "陆烬", "他")


def extract_prior_acquired_entities(end_state: dict | None) -> set[str]:
    """从上一章 end_state 提取已在上一章完成发现的实体词。

    来源：completed_actions + narrative_position。命中任一获取动作+提示词即视为已获。
    离线可测，零 LLM。
    严格排除纯认知动词（发现/看见/窥见/目击/察觉），避免将 ch1 的发现场景误判为获得。
    """
    if not end_state or not isinstance(end_state, dict):
        return set()
    sources: list[str] = []
    for key in ("completed_actions", "narrative_position"):
        val = end_state.get(key)
        if isinstance(val, list):
            sources.extend(str(v) for v in val)
        elif isinstance(val, str) and val:
            sources.append(val)
    combined = " ".join(sources)
    # 若文本中包含纯认知动词且无真正获取动词，不提取（避免 ch1 误判）
    has_cognitive = any(v in combined for v in _EXCLUDED_COGNITIVE_VERBS)
    has_acquired = any(v in combined for v in _PRIOR_ACQUIRED_VERBS)
    if has_cognitive and not has_acquired:
        return set()
    acquired: set[str] = set()
    for verb in _PRIOR_ACQUIRED_VERBS:
        if verb in combined:
            for hint in _PRIOR_ENTITY_HINTS:
                if hint in combined:
                    acquired.add(hint)
            break
    return acquired


def continuity_fix_directive(inversion: dict, spec: dict | None = None) -> str:
    """生成定点重生指令：明确禁止提前呈现该状态，要求按本场景时点重写。"""
    state = inversion.get("state", "更晚场景才成立的状态")
    matched = "、".join(inversion.get("matched", []) or [])
    nt = ""
    if spec:
        nt = str((spec.get("narrative_time") if isinstance(spec, dict) else "") or "")
    time_clause = f"本场景的故事时间为「{nt}」，" if nt else ""
    return (
        f"{time_clause}存在章内时序因果倒置：{state}（命中词：{matched}）。"
        "该状态此刻尚未发生，本场景严禁呈现、暗示或预设其已经成立"
        "（不得写其已被人抱起/抱在怀中/被村民围观等后果画面）。"
        "请严格站在本场景的时间点重写：只能写此刻真正发生的动作与角色对结果的未知/等待，"
        "把该状态留给更晚的场景去演出；不得遗漏本场景必须涵盖的 beats，正文字数仍须达标。"
    )
