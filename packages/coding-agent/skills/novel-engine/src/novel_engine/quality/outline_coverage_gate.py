# -*- coding: utf-8 -*-
"""T4: 大纲任务覆盖校验门（纯函数，零LLM）。

task_card 产出后、写正文前，校验其 core_goal / chapter_events / scene_blueprints
是否承载本章大纲核心任务的关键要素。采用 jieba 分词关键词覆盖率判定，
不要求逐字匹配，允许大纲任务的语义合理具象化。

未覆盖时由调用方负责重生蓝图；本模块只返回 (passed, issues)。
"""
from __future__ import annotations

import re
from typing import Iterable


# jieba 功能词/抽象词黑名单：不参与覆盖判定
_STOP_WORDS = frozenset({
    # 虚词/功能词
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要",
    "去", "你", "会", "着", "没有", "看", "好", "自己", "这",
    # 常见抽象动词/副词（语义承载弱）
    "决定", "主持", "是否", "传闻", "一起", "进行", "开始", "完成",
    "做出", "产生", "形成", "带来", "引起", "出现", "继续", "保持",
    "改变", "发展", "可能", "应该", "可以", "必须", "能够", "需要",
    "以为", "感觉", "觉得", "认为", "知道", "明白", "了解", "理解",
})

# 核心实体词（专有名词/人名/地名/核心事件名词）：一个都没命中则直接 fail
# 这些是承载情节主干的词，宁可误杀也不漏放
_CORE_ENTITY_STOP = frozenset({
    # 虚词也从核心实体中排除
    "的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
    "都", "一", "一个", "上", "也", "很", "到", "说", "要",
    "去", "你", "会", "着", "没有", "看", "好", "自己", "这",
    "决定", "主持", "是否", "传闻", "一起", "进行", "开始", "完成",
    "做出", "产生", "形成", "带来", "引起", "出现", "继续", "保持",
    "改变", "发展", "可能", "应该", "可以", "必须", "能够", "需要",
    "以为", "感觉", "觉得", "认为", "知道", "明白", "了解", "理解",
})

# 单字过滤：保留2字以上的实词
_MIN_TERM_LEN = 2


def _jieba_terms(text: str) -> list[str]:
    """jieba 分词，过滤功能词和单字词，返回实词列表（保序去重）。"""
    try:
        import jieba
        words = list(jieba.cut(text))
    except Exception:
        # jieba 不可用时回退为正则分词
        words = re.findall(r"[一-鿿]{2,}|[A-Za-z]{2,}", text or "")
    seen: set[str] = set()
    out: list[str] = []
    for w in words:
        w = w.strip()
        if len(w) < _MIN_TERM_LEN:
            continue
        if w in _STOP_WORDS:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def extract_task_keywords(task_text: str, limit: int = 12) -> list[str]:
    """从大纲核心任务文本中提取关键词（最多 limit 个）。"""
    terms = _jieba_terms(task_text)
    # 优先保留靠前的词（更重要的语义）
    return terms[:limit]


def extract_core_entities(task_text: str, limit: int = 6) -> list[str]:
    """R3: 从大纲任务中提取核心实体词（人名/地名/核心事件名词）。

    过滤掉功能词和抽象动词，保留承载情节主干的专名。
    """
    all_terms = _jieba_terms(task_text)
    # 核心实体：不在通用停用词表中、长度>=2的词
    core = [t for t in all_terms if t not in _CORE_ENTITY_STOP and len(t) >= 2]
    return core[:limit]


def _term_hit(keyword: str, combined_text: str, card_terms: set[str]) -> bool:
    """检查关键词是否在蓝图中命中（精确/子串/词级）。"""
    if keyword in combined_text:
        return True
    for term in card_terms:
        if keyword in term or term in keyword:
            return True
    return False


def validate_outline_coverage(
    task_card: dict,
    chapter_task: str,
    threshold: float = 0.35,
) -> tuple[bool, list[str]]:
    """校验 task_card 是否覆盖大纲核心任务。

    R3: 核心实体（人名/地名/核心事件名词）必须至少命中一个，否则直接 fail。
    其余关键词按 threshold 比例判定。

    Returns:
        (passed, issues): passed=True 表示覆盖达标；issues 列出未覆盖项。
    """
    if not chapter_task:
        # 无大纲任务时不阻断（兼容无细纲章节）
        return True, []

    all_keywords = extract_task_keywords(chapter_task)
    core_entities = extract_core_entities(chapter_task)
    if not all_keywords:
        return True, []

    # 收集 task_card 中所有可检查的文本
    checks: list[tuple[str, str]] = []

    cg = task_card.get("core_goal", "")
    if cg:
        checks.append(("core_goal", cg))

    for evt in (task_card.get("chapter_events") or []):
        summary = evt.get("one_line_summary", "")
        etype = evt.get("event_type", "")
        if summary:
            checks.append((f"chapter_event/{etype}", summary))

    for bp in (task_card.get("scene_blueprints") or []):
        goal = bp.get("goal", "")
        conflict = bp.get("conflict", "")
        if goal:
            checks.append((f"scene_bp_{bp.get('scene_num', '?')}/goal", goal))
        if conflict:
            checks.append((f"scene_bp_{bp.get('scene_num', '?')}/conflict", conflict))

    combined_text = " ".join(text for _, text in checks)
    card_terms = set(_jieba_terms(combined_text))

    # R3: 核心实体必命中检查
    if core_entities:
        core_hit = any(_term_hit(e, combined_text, card_terms) for e in core_entities)
        if not core_hit:
            return False, [
                f"outline_task_missing_core_entity: none of {core_entities[:4]} found in task_card"
            ]

    # 逐关键词检查覆盖率
    missing: list[str] = []
    for kw in all_keywords:
        if not _term_hit(kw, combined_text, card_terms):
            missing.append(kw)

    if missing:
        ratio = 1.0 - len(missing) / len(all_keywords)
        issues = [
            f"outline_task_missing_keyword: {kw!r} not found in task_card"
            for kw in missing
        ]
        return ratio >= threshold, issues

    return True, []


# ============================================================================
# CC round-13 R4：must_cover_beats 提取与覆盖检查（纯函数）
# ============================================================================

# ── 位点概念组：用于绑定阶段归一化，动作侧与场景侧任一命中同组即计位点分 ────
_POSITION_CONCEPT_GROUPS: dict[str, frozenset[str]] = {
    "WELL": frozenset({
        "井", "井边", "井口", "井台", "井水", "井底", "井里",
        "打水", "挑水", "取水", "提水", "村口打水",
    }),
}
# 展开所有位点词到扁平集合，便于快速查找所属组
_POS_WORDS_TO_GROUP: dict[str, str] = {}
for _group_name, _words in _POSITION_CONCEPT_GROUPS.items():
    for _w in _words:
        _POS_WORDS_TO_GROUP[_w] = _group_name

# ── 人名列表（从 task_card 动态收集；此处仅做初始基线，调用方会叠加） ───────
_PERSON_NAME_BASELINE = frozenset({
    "陆烬", "陈老根", "村民", "赵老四", "王大牛",
})

# ── 通用称谓/停用词：不参与绑定也不参与覆盖判定 ────────────────────────────
_FORESHORT_GENERIC = frozenset({
    "婴儿", "婴孩", "孩子", "娃", "他", "她", "自己",
    "抱起", "抱着", "襁褓", "村中", "村子", "独自", "观察",
})

# ── 反应词：只用于覆盖判定，不参与绑定打分 ─────────────────────────────────
_FORESHORT_REACTION_WORDS = frozenset({
    "侧头", "侧过", "避开", "屏息", "转头", "躲开", "躲", "本能", "异常",
    "异样", "停顿", "警觉", "紧张", "戒备", "后退", "贴近", "蜷缩",
    "颤动", "不安", "无反应",
})


def _filter_binding_terms(terms: list[str], person_names: frozenset) -> list[str]:
    """剔除人名、通用词、反应词后，返回用于绑定打分的剩余实词。"""
    return [
        t for t in terms
        if t not in person_names
        and t not in _FORESHORT_GENERIC
        and t not in _FORESHORT_REACTION_WORDS
    ]


def _map_none_scenes_to_scenes(
    events: list[dict], blueprints: list[dict],
) -> dict[int, list[str]]:
    """把 chapter_events 中 scene_num=None 的事件映射到最匹配的 blueprint 场景。

    映射规则：对每个 None-event，与所有 blueprint 的 goal+conflict+已绑定 event 文本
    做实词重叠打分（位点概念组优先），取最高分场景；若最高分 ≤ 1 则忽略（不绑）。
    返回 {scene_num: [event_text, ...]} 的映射表。
    """
    if not events or not blueprints:
        return {}
    mapped: dict[int, list[str]] = {}
    # 预计算每个 blueprint 的可用术语集合（含已绑定 event 文本）
    bp_term_sets: list[tuple[int, set[str], int]] = []  # (sid, terms, orig_index)
    for idx, bp in enumerate(blueprints):
        if not isinstance(bp, dict):
            continue
        sid = bp.get("scene_num")
        text = " ".join([str(bp.get("goal") or ""), str(bp.get("conflict") or "")])
        terms = set(_filter_binding_terms(_jieba_terms(text), _PERSON_NAME_BASELINE))
        # 同时记录文本原文用于子串位点匹配
        bp_term_sets.append((sid, terms, idx, text))

    for evt in events:
        if not isinstance(evt, dict):
            continue
        summary = (evt.get("one_line_summary") or "").strip()
        if not summary:
            continue
        if evt.get("scene_num") is not None:
            continue
        evt_terms = set(_filter_binding_terms(_jieba_terms(summary), _PERSON_NAME_BASELINE))
        if not evt_terms:
            continue
        best_sid = None
        best_score = 0
        best_idx = 999
        for sid, bp_terms, orig_idx, bp_text in bp_term_sets:
            # 位点概念组：用子串匹配（容忍分词回退）
            pos_group_hits = sum(
                1 for t in _POS_WORDS_TO_GROUP
                if t in summary and t in bp_text
            )
            term_overlap = len(evt_terms & bp_terms)
            score = pos_group_hits * 10 + term_overlap
            if score > best_score or (score == best_score and orig_idx < best_idx):
                best_score = score
                best_sid = sid
                best_idx = orig_idx
        if best_sid is not None and best_score >= 2:
            mapped.setdefault(best_sid, []).append(summary)
    return mapped


def extract_must_cover_beats(task_card: dict) -> list[dict]:
    """从 task_card 提取 must_cover_beats 清单。

    来源优先级：
    1. task_card 显式字段 must_cover_beats（若导演已写入）
    2. scene_blueprints 中 scene_num 对应的 goal/conflict 作为 beat
    3. chapter_events 中 one_line_summary 作为 beat（scene_num=None 的事件自动映射）
    4. foreshadow_actions（action 文本绑定到最相关场景；无可靠绑定则 scene_num=None）

    返回 list[dict]，每项含 scene_num / beat_text / category。
    """
    if not isinstance(task_card, dict):
        return []
    # R17-1：显式 must_cover_beats 可能陈旧（导演缓存卡同时含正确的 foreshadow_actions），
    # 绝不能因显式列表非空就丢弃 foreshadow_actions 要求的伏笔。始终做对齐并集：
    #   - 非 foreshadow 的显式 beat 原样保留；
    #   - foreshadow_actions 每一条都必须出现在返回列表中（按 foreshadow_id 去重）。
    explicit = task_card.get("must_cover_beats")
    explicit_foreshadow_ids: set[str] = set()
    if isinstance(explicit, list) and explicit:
        for b in explicit:
            if isinstance(b, dict) and b.get("category") == "foreshadow":
                explicit_foreshadow_ids.add(b.get("foreshadow_id") or "")
        beats: list[dict] = [b for b in explicit if isinstance(b, dict)]
    else:
        beats = []

    blueprints = [bp for bp in (task_card.get("scene_blueprints") or []) if isinstance(bp, dict)]
    events = [evt for evt in (task_card.get("chapter_events") or []) if isinstance(evt, dict)]

    # CC round-14 R14c：把 scene_num=None 的事件映射到场景
    _event_map = _map_none_scenes_to_scenes(events, blueprints)
    if _event_map:
        import logging
        _log = logging.getLogger(__name__)
        for _sid, _texts in _event_map.items():
            _log.info(f"ch? mapped {len(_texts)} chapter_events → scene {_sid}")

    # scene_blueprints 中的 goal/conflict（仅当显式列表为空时追加，避免重复）
    if not explicit:
        for bp in blueprints:
            sid = bp.get("scene_num")
            goal = (bp.get("goal") or "").strip()
            conflict = (bp.get("conflict") or "").strip()
            if goal:
                beats.append({"scene_num": sid, "beat_text": goal, "category": "goal"})
            if conflict:
                beats.append({"scene_num": sid, "beat_text": conflict, "category": "conflict"})
        # chapter_events 中的 one_line_summary（已映射 scene_num，或保留 None）
        for evt in events:
            summary = (evt.get("one_line_summary") or "").strip()
            if not summary:
                continue
            # 优先使用映射后的 scene_num
            sid = evt.get("scene_num")
            if sid is None and _event_map:
                # 尝试从映射表找
                for _msid, _mtxts in _event_map.items():
                    if summary in _mtxts or any(summary[:20] in t or t[:20] in summary for t in _mtxts):
                        sid = _msid
                        break
            beats.append({
                "scene_num": sid,
                "beat_text": summary,
                "category": "event",
            })
    # foreshadow_actions：绑定到具体场景（始终执行，R17-1 关键路径）
    for fa in (task_card.get("foreshadow_actions") or []):
        if not isinstance(fa, dict):
            continue
        action = (fa.get("action") or "").strip()
        fs_id = fa.get("foreshadow_id", "")
        if not action:
            continue
        bound_sid = _bind_foreshadow_to_scene(
            action, blueprints,
            event_text_by_scene=_event_map,
            person_names=_PERSON_NAME_BASELINE,
        )
        if fs_id and fs_id not in explicit_foreshadow_ids:
            beats.append({
                "scene_num": bound_sid,
                "beat_text": f"[F{fs_id}] {action}",
                "category": "foreshadow",
                "foreshadow_id": fs_id,
            })
    return beats


def _bind_foreshadow_to_scene(
    action: str,
    blueprints: list[dict],
    event_text_by_scene: dict[int, list[str]] | None = None,
    person_names: frozenset | None = None,
) -> int | None:
    """将伏笔 action 文本绑定到最相关的场景。

    绑定算法（CC round-14 R14c 修复）：
    1. 对 action 和每个场景文本，先剔除人名/通用词/反应词，得到"绑定用词"
    2. 检查位点概念组（WELL 等）命中：动作侧与场景侧同组命中计高位点分（×10）
    3. 剩余实词做集合重叠计低位分
    4. 取最高分场景；要求显著高于次高分（margin ≥ 3）且总分 ≥ 2，否则返回 None
    """
    if not blueprints:
        return None
    if person_names is None:
        person_names = _PERSON_NAME_BASELINE
    action_terms = _filter_binding_terms(_jieba_terms(action), person_names)
    if not action_terms:
        return None
    # 位点概念组匹配：在 action 文本中搜索概念组内成员（子串匹配，容忍分词回退）
    action_group_set: set[str] = set()
    for t in _POS_WORDS_TO_GROUP:
        if t in action:
            action_group_set.add(_POS_WORDS_TO_GROUP[t])
    action_extra = [t for t in action_terms if not any(t in action for t in _POS_WORDS_TO_GROUP)]

    scored: list[tuple[int, float, int]] = []  # (sid, score, margin_ref_index)
    for idx, bp in enumerate(blueprints):
        if not isinstance(bp, dict):
            continue
        sid = bp.get("scene_num")
        goal = (bp.get("goal") or "").strip()
        conflict = (bp.get("conflict") or "").strip()
        # 合并蓝图文本 + 已绑定事件文本
        extra_texts = []
        if event_text_by_scene and sid is not None:
            extra_texts = event_text_by_scene.get(sid, [])
        context = " ".join([goal, conflict] + extra_texts)
        bp_terms = _filter_binding_terms(_jieba_terms(context), person_names)
        # 位点概念组匹配：在 context 文本中搜索概念组内成员
        bp_group_set: set[str] = set()
        for t in _POS_WORDS_TO_GROUP:
            if t in context:
                bp_group_set.add(_POS_WORDS_TO_GROUP[t])
        bp_extra = [t for t in bp_terms if not any(t in context for t in _POS_WORDS_TO_GROUP)]

        # 位点概念组命中（高权重）：两组有同组词即计1分
        pos_hits = len(action_group_set & bp_group_set)
        # 剩余实词重叠（低权重）
        extra_hits = len(set(action_extra) & set(bp_extra))
        score = pos_hits * 10 + extra_hits
        scored.append((sid or idx, score, idx))

    if not scored:
        return None
    scored.sort(key=lambda x: (-x[1], x[2]))
    best_sid, best_score, _ = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0
    margin = best_score - second_score
    # 需要显著胜出且过最低阈值
    if best_score >= 2 and margin >= 3:
        return best_sid
    return None


def check_must_cover_beats(novel_text: str, beats: list[dict]) -> tuple[bool, list[str]]:
    """检查 novel_text 是否覆盖必须 beat。

    每个 beat 使用 jieba 关键词覆盖率判定；
    无 beats 时直接返回 True。
    返回 (passed, missing_descriptions)。
    """
    if not beats:
        return True, []
    missing: list[str] = []
    for beat in beats:
        beat_text = (beat.get("beat_text") or "").strip()
        if not beat_text:
            continue
        try:
            import jieba
            terms = [w for w in jieba.cut(beat_text) if len(w.strip()) >= 2]
        except Exception:
            import re as _re
            terms = [w for w in _re.findall(r"[一-鿿]{2,}", beat_text)]
        if not terms:
            continue
        hit = any(t in (novel_text or "") for t in terms)
        if not hit:
            missing.append(f"beat[{beat.get('category','?')}] scene={beat.get('scene_num')}: {beat_text[:60]}")
    return len(missing) == 0, missing


# ============================================================================
# CC round-14 R2：按场景 beat 覆盖检查 + 强制补丁指令生成
# ============================================================================

def check_scene_must_cover_beats(scene_text: str, beats: list[dict],
                                  scene_id: int) -> tuple[bool, list[str]]:
    """检查单个场景文本是否覆盖指定 scene_id 的必填 beat。

    beats 应为 extract_must_cover_beats 输出的列表（含 scene_num 字段）。
    只匹配 scene_num == scene_id 的 beat；无匹配 beat 视为通过。
    返回 (passed, missing_descriptions)。
    """
    if not beats or not scene_text:
        return True, []
    scene_beats = [b for b in beats if b.get("scene_num") == scene_id]
    if not scene_beats:
        return True, []
    missing: list[str] = []
    for beat in scene_beats:
        beat_text = (beat.get("beat_text") or "").strip()
        if not beat_text:
            continue
        try:
            import jieba
            terms = [w for w in jieba.cut(beat_text) if len(w.strip()) >= 2]
        except Exception:
            import re as _re
            terms = [w for w in _re.findall(r"[一-鿿]{2,}", beat_text)]
        if not terms:
            continue
        hit = any(t in scene_text for t in terms)
        if not hit:
            missing.append(f"beat[{beat.get('category','?')}] scene={scene_id}: {beat_text[:60]}")
    return len(missing) == 0, missing


def build_mandatory_beat_directive(missing_beats: list[str]) -> str:
    """为缺失的必填 beat 生成定点补丁指令。

    明确标注为"强制补写"，允许扩写至 +30%（突破常规 ±15% 长度带），
    严禁删减既有剧情。
    """
    if not missing_beats:
        return ""
    lines = ["【强制必填 beat 补写（长度带放宽至 +30%）】",
             "下列 beat 在当前场景中完全缺失，必须在本场景内具体写入："]
    for desc in missing_beats:
        lines.append(f"  - {desc}")
    lines.append("补写方式：在场景适当位置加入具体动作/对话/描写，使 beat 关键词在正文中出现，"
                 "不得仅用形容词概括，不得改变既定情节走向，不得删除任何既有剧情。"
                 "允许字数较原场景最多增加 30%。")
    return "\n".join(lines)


# ============================================================================
# CC round-15 R15-1：伏笔覆盖判定收紧——异常对象+婴儿反应双条件
# ============================================================================

# 通用称谓/人名：不参与绑定也不参与覆盖判定
_FORESHORT_GENERIC = frozenset({
    "陆烬", "陈老根", "村民", "赵老四", "王大牛",
    "婴儿", "婴孩", "孩子", "娃", "他", "她", "自己",
    "抱起", "抱着", "襁褓", "独自", "观察",
})

# ── 异常对象词（必须是具体异象，而非井/打水等地点词） ────────────────────────
# 要求 beat 中出现的异常物词必须在此集合内才算"异常对象命中"
# 仅"井/井边/打水"等地点词不算，必须出现浊气/瘴气/异样气息等异常物
_ABNORMAL_OBJECT_TERMS = frozenset({
    "浊气", "瘴气", "异样气息", "异常气息", "诡异气息",
    "气感", "异象", "腥气", "腐气", "黑气", "青气",
    "焦痕", "灼痕", "异光", "异样",
})

# ── 婴儿主体反应词：仅限婴儿/襁褓中的陆烬发出的身体反应 ────────────────────
# 不含成人行为（如"停顿/警觉/紧张/躲闪/转身"等）
_INFANT_REACTION_WORDS = frozenset({
    "侧头", "侧过", "屏息", "转头", "躲开", "本能", "缩",
    "蜷缩", "颤动", "惊", "异常无反应", "无反应",
})


def _check_foreshadow_coverage(scene_text: str, beat_text: str) -> tuple[bool, str]:
    """检查一条伏笔 action 是否已覆盖（R15-1收紧版）。

    R15-1 规则：
    1. 异常对象命中：beat 中的异常物词（浊气/瘴气/异样气息等）必须在 scene_text
       中以子串形式出现。仅"井/打水"等地点词不算异常对象。
    2. 婴儿反应命中：scene_text 中必须在婴儿主体（陆烬/婴儿/襁褓/婴孩）附近
       （±50字窗口）出现婴儿反应词（侧头/屏息/本能/缩/躲开等）。
       成年角色的避开/转身/停顿/警觉等不算婴儿反应。
    3. 二者同时满足才判覆盖；否则判 miss。

    返回 (covered: bool, reason: str)。
    """
    # 剥离 beat_text 前缀如 "[F001] "，只保留中文 action 内容
    clean_beat = re.sub(r'^\[[Ff]\d+\]\s*', '', beat_text or '').strip()
    try:
        import jieba
        terms = [w.strip() for w in jieba.cut(clean_beat) if len(w.strip()) >= 2]
    except Exception:
        terms = [w for w in re.findall(r"[一-鿿]{2,}", clean_beat)]
    if not terms:
        return False, "no_terms"

    # 从 beat 文本提取异常对象词和婴儿反应词（来自 beat，不限定 scene）
    abnormal_hits = [t for t in terms if t in _ABNORMAL_OBJECT_TERMS]
    infant_reaction_from_beat = [t for t in terms if t in _INFANT_REACTION_WORDS]

    # 条件1：beat 中必须含有异常对象词，且 scene_text 中存在
    # 2026-10-01 放宽：隐晦提示类伏笔（beat 不含异常物件词，如"讲述传说+陈老根路过"）
    # 不适用 R15-1 婴儿篇收紧规则——退回关键词弱校验：beat 分词后任一核心词在场景中出现
    # 即视为覆盖（防止 no_abnormal_object_in_beat 把普通隐晦伏笔打成硬块）。
    if not abnormal_hits:
        implicit_hits = [t for t in terms if t in (scene_text or "")]
        if implicit_hits:
            return True, f"implicit_foreshadow keywords={implicit_hits[:3]}"
        return False, "no_abnormal_object_in_beat"
    has_abnormal = any(ao in (scene_text or "") for ao in abnormal_hits)
    if not has_abnormal:
        return False, f"abnormal_object({abnormal_hits[:2]}) not in scene_text"

    # 条件2 前置：R15-1 婴儿篇收紧仅适用于 beat 明确要求婴儿反应的伏笔。
    # 普通章伏笔（beat 不含婴儿反应词，如"旗号金纹与古字异象产生视觉共振"）
    # 异常对象命中即判覆盖，防止婴儿篇双条件规则外溢到非婴儿场景（ch426 根因）。
    if not infant_reaction_from_beat:
        return True, f"abnormal={abnormal_hits[:2]} non-infant-beat keyword pass"

    # 条件2：scene_text 中婴儿主体附近有婴儿反应词
    # 婴儿主体候选词
    _INFANT_SUBJECTS = frozenset({"陆烬", "婴儿", "婴孩", "襁褓", "娃", "他"})
    window = 60
    infant_nearby = False
    st = scene_text or ""
    for subject in _INFANT_SUBJECTS:
        idx = 0
        while True:
            pos = st.find(subject, idx)
            if pos == -1:
                break
            # 检查窗口内是否有婴儿反应词
            chunk = st[pos:pos + window]
            if any(irw in chunk for irw in _INFANT_REACTION_WORDS):
                infant_nearby = True
                break
            idx = pos + 1
        if infant_nearby:
            break

    if infant_nearby:
        return True, f"abnormal={abnormal_hits[:2]} infant_reaction_near_subject=True"
    return False, f"abnormal_present but no infant_reaction_near_subject"


def check_scene_must_cover_beats(scene_text: str, beats: list[dict],
                                  scene_id: int) -> tuple[bool, list[str]]:
    """检查单个场景文本是否覆盖指定 scene_id 的必填 beat。

    beats 应为 extract_must_cover_beats 输出的列表（含 scene_num 字段）。
    只匹配 scene_num == scene_id 的 beat；无匹配 beat 视为通过。
    对 category=foreshadow 的 beat 使用双词命中规则（位点+反应）。
    返回 (passed, missing_descriptions)。
    """
    if not beats or not scene_text:
        return True, []
    scene_beats = [b for b in beats if b.get("scene_num") == scene_id]
    if not scene_beats:
        return True, []
    missing: list[str] = []
    for beat in scene_beats:
        beat_text = (beat.get("beat_text") or "").strip()
        if not beat_text:
            continue
        category = beat.get("category", "")
        if category == "foreshadow":
            covered, reason = _check_foreshadow_coverage(scene_text, beat_text)
            if not covered:
                missing.append(f"beat[{category}] scene={scene_id}: {beat_text[:60]} ({reason})")
        else:
            try:
                import jieba
                terms = [w for w in jieba.cut(beat_text) if len(w.strip()) >= 2]
            except Exception:
                import re as _re2
                terms = [w for w in _re2.findall(r"[一-鿿]{2,}", beat_text)]
            if not terms:
                continue
            hit = any(t in scene_text for t in terms)
            if not hit:
                missing.append(f"beat[{category}] scene={scene_id}: {beat_text[:60]}")
    return len(missing) == 0, missing


# ============================================================================
# 设定贯通：伏笔-终局回收远期校验（纯函数，零 LLM）
# ============================================================================

def validate_foreshadow_closure(
    foreshadow_registry: dict | None,
    current_chapter: int,
    resolved_ids: set[str] | None = None,
    importance_threshold: float = 0.7,
) -> tuple[bool, list[str]]:
    """远期检查：importance>=threshold 的伏笔，回收节点已过且未回收 → 硬拦截。

    依据 ending_bible.md"关键伏笔必须回收"清单与 config/foreshadow/registry.json
    （含 plant_chapter/resolve_chapter/importance/status）。"已过"定义为
    resolve_chapter < current_chapter；"未回收"定义为 status!='resolved' 且
    不在 resolved_ids 中。resolve_chapter == current_chapter 视为"正在回收"，不拦。

    仅在存在已过节点条目时产生 hard issues——ch1-50 阶段 resolve_chapter 均
    远在 1800+，生产路径零触发（远期锚点），测试用合成 fixture 覆盖。

    Args:
        foreshadow_registry: config/foreshadow/registry.json 解析结果（含
            "foreshadows" 列表）；None/空时直接通过。
        current_chapter: 当前生成章号。
        resolved_ids: 已回收伏笔 id 集合（可选；来自 StateDB status='resolved' 或
            调用方登记）。与 registry 条目自身 status 字段任一路径命中即视为已回收。
        importance_threshold: 仅检查 importance>=该值的伏笔（默认 0.7，
            与编写指南"importance>=0.7 必须回收"一致）。

    Returns:
        (passed, issues)：passed=False 时 issues 为可读归因列表，格式：
            "foreshadow_closure_overdue: Fxxx resolve@N overdue at chM (importance=X)"
    """
    if not foreshadow_registry or not isinstance(foreshadow_registry, dict):
        return True, []
    fs_list = foreshadow_registry.get("foreshadows") or []
    if not fs_list:
        return True, []

    overdue: list[str] = []
    for fs in fs_list:
        if not isinstance(fs, dict):
            continue
        fid = str(fs.get("id") or "")
        try:
            imp = float(fs.get("importance", 0.5))
        except (TypeError, ValueError):
            imp = 0.5
        try:
            rc = int(fs.get("resolve_chapter", 0) or 0)
        except (TypeError, ValueError):
            continue
        if imp < importance_threshold:
            continue
        if rc <= 0 or rc >= current_chapter:
            continue  # 无回收节点或尚未到期
        if resolved_ids and fid in resolved_ids:
            continue
        if str(fs.get("status", "")).strip().lower() == "resolved":
            continue
        overdue.append(
            f"foreshadow_closure_overdue: {fid} resolve@{rc} overdue at "
            f"ch{current_chapter} (importance={imp})"
        )
    if not overdue:
        return True, []
    return False, overdue
