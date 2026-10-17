"""Structured scene contract (Q3/Q8). Pure narrative + independent hook, measured word counts."""
import json, re
from dataclasses import dataclass, field


# CC round-32：蓝图瘦身。concrete_events/named_interactions/info_reveal_points/
# scene_craft_elements 已被专用约束块（density_block/craft_block）完整注入 prompt，
# 在蓝图中重复打印只会拉长 prompt。实测（agnès-3.0-flash，ch148 场景2）：
# 全量蓝图 prompt 3651 字符 → scene_text 1343 字；瘦身后 2570 字符 → 2160 字（+61%）。
# 弱模型在超长 prompt 下输出被压缩，冗余重复是"写短"的根因之一。
_SLIM_DROP_FIELDS = (
    "concrete_events", "named_interactions", "info_reveal_points", "scene_craft_elements",
)


def slim_scene_blueprint(bp: dict) -> dict:
    """返回不含被专用约束块覆盖的大字段的蓝图副本（不动原对象）。"""
    out = dict(bp)
    for k in _SLIM_DROP_FIELDS:
        out.pop(k, None)
    return out


@dataclass
class SceneOutput:
    scene_id: int
    scene_text: str
    hook: str
    beats: list = field(default_factory=list)
    # CC P0 反摆烂：模型须先逐条列 beat 覆盖点再写正文；structured 标记是否来自严格 JSON
    # （仅对 structured 真实模型强制 beats_covered 校验，mock/纯散文回退路径不强制）。
    beats_covered: list = field(default_factory=list)
    structured: bool = False
    finish_reason: str = ""
    # CC round-25 P1(a) 近空场景 HTTP 层诊断埋点（零行为影响，仅观测）：
    # latency_ms 单次场景请求往返毫秒；http_signal 区分"完整往返但content短"与
    # "连接层异常/截断"，供判断近空究竟是采样退化还是限流/连接竞争。
    latency_ms: int = 0
    http_signal: str = ""


def _clean(text: str) -> str:
    t = text.strip().strip("`").strip()
    m = re.search(r"\{[\s\S]*\}", t)
    return m.group(0) if m else t


def _from_prose(text: str) -> dict:
    return {"scene_id": 0, "scene_text": text.strip(), "hook": "", "beats": []}


def extract_beats_fallback(scene_text: str, limit: int = 3) -> list[str]:
    import re
    sents = [s.strip() for s in re.split(r"[。！？]", scene_text) if len(s.strip()) > 12]
    scored = sorted(sents, key=lambda s: (len(set(re.findall(r"[\u4e00-\u9fa5]{2,}", s))), len(s)), reverse=True)
    return scored[:limit]


_SCENE_FENCE_RE = re.compile(r"^\s*```(?:json|scene)?\s*(.*?)\s*```\s*$", re.S)


def _unwrap_scene_envelope(raw: str) -> str:
    """Strip a ```json / ```scene fence and isolate the outermost {...} object."""
    t = (raw or "").strip()
    m = _SCENE_FENCE_RE.match(t)
    if m:
        t = m.group(1).strip()
    m2 = re.search(r"\{[\s\S]*\}", t)
    return m2.group(0) if m2 else t


def _loads_scene_object(raw: str) -> dict:
    """Parse the scene envelope into a validated dict.

    Accepts a bare or fenced JSON object; tolerates the loose output some models emit
    (literal newlines / unescaped quotes inside strings) via json_repair. Fails closed:
    raises ValueError unless the result is an object with a non-empty scene_text.
    """
    cand = _unwrap_scene_envelope(raw)
    data = None
    try:
        data = json.loads(cand)
    except (json.JSONDecodeError, ValueError):
        try:
            import json_repair  # already a project dependency used by the director
            data = json_repair.loads(cand)
        except Exception as exc:  # pragma: no cover - repair backend failure
            raise ValueError(f"scene JSON unparseable: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("scene JSON did not yield an object")
    st = data.get("scene_text")
    if not isinstance(st, str) or not st.strip():
        raise ValueError("scene JSON missing non-empty scene_text")
    return data


def parse_scene(raw: str, model_used: str, strict_models: list) -> "SceneOutput":
    strict = model_used in (strict_models or [])
    structured = False
    if strict:
        # Raises ValueError on non-JSON/prose: the writer retries once, then fails closed.
        data = _loads_scene_object(raw)
        structured = True
    else:
        # Lenient models: still recognise a (possibly fenced/loose) JSON envelope;
        # only genuinely object-less output is treated as plain prose.
        try:
            data = _loads_scene_object(raw)
            structured = True
        except ValueError:
            data = _from_prose(_unwrap_scene_envelope(raw))
    if "【" in data.get("scene_text", "") or "】" in data.get("scene_text", ""):
        raise ValueError("scene_text contains scaffolding markers")
    beats_covered = []
    for x in (data.get("beats_covered") or []):
        sx = str(x).strip()
        if sx:
            beats_covered.append(sx)
    return SceneOutput(int(data.get("scene_id", 0) or 0), data.get("scene_text", "").strip(),
                       data.get("hook", "").strip(), list(data.get("beats", []) or []),
                       beats_covered=beats_covered, structured=structured)


def validate_beats_covered(covered, expected_count) -> tuple[bool, str | None]:
    """CC P0: structured scenes must enumerate every task-card beat before writing.

    Returns (ok, issue). A non-positive / unparsable expected_count means the caller
    has no beat requirement (mock / prose fallback) and the check is skipped.
    """
    try:
        need = int(expected_count or 0)
    except (TypeError, ValueError):
        need = 0
    if need <= 0:
        return (True, None)
    have = len([x for x in (covered or []) if str(x).strip()])
    if have >= need:
        return (True, None)
    return (False, f"beats_covered_insufficient: {have} < {need}")

# --- pkg2e: scene boundary enforcement (deterministic, zero LLM calls) ---

OVERLAP_NGRAM_K: int = 12
OVERLAP_RATIO: float = 0.15
_NEAR_EMPTY_PLACEHOLDERS: list[str] = [
    "待补充", "此处省略", "（略）", "TODO", "【待写", "【待补充",
    "此处留白", "（此处略）", "暂缺", "...",
]


def _chinese_ngrams(text: str, k: int = OVERLAP_NGRAM_K) -> set[str]:
    """Return set of k-char Chinese n-grams (ignoring whitespace/punctuation)."""
    chars = [c for c in text if "\u4e00" <= c <= "\u9fff"]
    return set("".join(chars[i:i+k]) for i in range(len(chars) - k + 1))


def _truncate_at_sentence_boundary(text: str, max_chars: int) -> str:
    """Truncate text at or before max_chars, backtracking to nearest sentence end."""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    # Find nearest sentence boundary (。！？…\n) going backwards
    for sep in ("。", "！", "？", "…", "\n"):
        idx = cut.rfind(sep)
        if idx >= max_chars // 2:  # don't truncate too aggressively
            return cut[:idx + 1]
    return cut.rstrip()[:max_chars]


def _summarize_anchor(blueprint: dict, max_chars: int = 250) -> str:
    """Build a 200-250 char anchor from a preceding scene's goal + beats."""
    parts = []
    goal = blueprint.get("goal", "")
    if goal:
        parts.append(f"场景{blueprint.get('scene_num', '?')}目标：{goal}")
    beats = blueprint.get("beats", [])
    for b in beats[:3]:
        if isinstance(b, str) and b:
            parts.append(b)
    anchor = "；".join(p for p in parts if p)
    return _truncate_at_sentence_boundary(anchor, max_chars)


def build_scene_prompt(
    task_card: dict,
    scene_blueprint: dict,
    all_blueprints: list[dict],
    negative_examples: list[str] | None = None,
    fix_directive: str | None = None,
) -> str:
    """Build deterministic per-scene prompt with strict boundary enforcement.

    Args:
        task_card: Full chapter task card (must have scene_blueprints, core_goal, chapter_hook).
        scene_blueprint: The blueprint for this specific scene.
        all_blueprints: All scene blueprints for the chapter (for forbidden list / anchor).
        negative_examples: Optional list of text snippets to exclude (from prior violation).
        fix_directive: Optional targeted reviewer instruction when regenerating a docked scene.

    Returns:
        Complete user prompt string for this scene.
    """
    chapter_num = task_card.get("chapter_num", 0)
    scene_num = int(scene_blueprint.get("scene_num", 0) or 0)
    is_last_scene = (scene_num == max(int(bp.get("scene_num", 0) or 0) for bp in all_blueprints))
    is_first_scene = (scene_num == min(int(bp.get("scene_num", 0) or 0) for bp in all_blueprints))

    # word_count_target 偶发为 list/float/str（模型畸形输出），统一归一化为正整数
    _wc_raw = scene_blueprint.get("word_count_target", 2000)
    if isinstance(_wc_raw, (list, tuple)):
        _wc_raw = _wc_raw[0] if _wc_raw else 2000
    try:
        _scene_tgt = max(500, int(float(_wc_raw)))
    except (TypeError, ValueError):
        _scene_tgt = 2000
    _scene_min = max(int(_scene_tgt * 0.7), 1200)
    _scene_paras = max(4, round(_scene_tgt / 350))
    _beat_count = len([b for b in scene_blueprint.get("beats", []) if b])

    # Previous scene anchor (deterministic task-card preview, not actual text)
    sorted_bps = sorted(all_blueprints, key=lambda b: int(b.get("scene_num", 0) or 0))
    prev_idx = None
    for i, bp in enumerate(sorted_bps):
        if int(bp.get("scene_num", 0) or 0) == scene_num:
            prev_idx = i
            break
    anchor_text = ""
    if prev_idx is not None and prev_idx > 0:
        prev_bp = sorted_bps[prev_idx - 1]
        anchor_text = _summarize_anchor(prev_bp, max_chars=250)
        anchor_text = (
            f"\n\n## 前情锚点（已发生内容提要，仅供语气/信息承接，严禁复述、扩写或提前演出）\n{anchor_text}"
        )
    elif is_first_scene:
        anchor_text = (
            "\n\n## 前情锚点\n本章为开篇，无前场内容。"
        )

    # Forbidden events: other scenes' goals as one-liners
    forbidden_lines = []
    for bp in sorted_bps:
        bp_num = int(bp.get("scene_num", 0) or 0)
        if bp_num != scene_num:
            goal = bp.get("goal", "")
            if goal:
                forbidden_lines.append(f"- 不得提前发生场景{bp_num}的核心目标：{goal}")
    forbidden_block = "\n".join(forbidden_lines) if forbidden_lines else ""

    # CC round-8 P0-1：章内时序因果锁（故事时间序号 + 禁止提前演后果的实体状态）
    try:
        _seq = int(scene_blueprint.get("sequence_index", scene_num) or scene_num)
    except (TypeError, ValueError):
        _seq = int(scene_num or 0)
    _nt = str(scene_blueprint.get("narrative_time", "") or "").strip()
    _forbidden_states = [
        str(s).strip() for s in (scene_blueprint.get("do_not_depict_before") or [])
        if str(s).strip()
    ]
    temporal_lines: list[str] = []
    if _nt:
        temporal_lines.append(f"- 本场景故事时间：{_nt}（故事时间序号 {_seq}，必须承接序号 {max(1, _seq - 1)} 之后，严禁时间倒流）")
    for _st in _forbidden_states:
        temporal_lines.append(f"- 以下状态此刻【尚未成立】，严禁呈现、暗示或预设其已发生：{_st}")
    temporal_block = (
        "\n## 时序因果锁（CC round-8，违反将判场景失败并定点重生）\n"
        + "\n".join(temporal_lines) + "\n"
    ) if temporal_lines else ""

    # CC round-12 B：writer 末尾强约束（一次压两类收束倾向：提前结束/空稿 + 夜场越界到天明）。
    _closing_lines = [
        "输出要求：必须完整写满本场景全部事件要点（beats），不允许提前结束、不允许只输出简短内容或仅给思考。",
    ]
    _latest = str(scene_blueprint.get("latest_permissible_moment", "") or "").strip()
    _nightish = any(w in _nt for w in ("夜", "子时", "丑时", "寅时", "三更", "四更", "五更初"))
    _forbid_img = [str(x).strip() for x in (scene_blueprint.get("explicit_forbidden_imagery") or []) if str(x).strip()]
    if _nightish or _latest:
        _stay = _latest or ("深夜（" + _nt + "）" if _nt else "当夜时间锚内")
        _img = "、".join(_forbid_img) if _forbid_img else "清晨、天亮、天明、黎明、拂晓、破晓、晨光、晨曦、日出、鱼肚白、鸡鸣"
        _closing_lines.append(
            f"本场景发生在{_nt or '当夜'}，结尾必须停留在{_stay}附近，不得描写或暗示天色转亮、时间推进到清晨；"
            f"叙事严禁出现{_img}等时间推进标记（角色在对话里说“等天亮再说”这类将来时愿望可以，但叙述本身不得演到天明）。"
        )
    closing_instruction = str(scene_blueprint.get("closing_instruction", "") or "").strip()
    if closing_instruction:
        _closing_lines.append(closing_instruction)
    closing_block = "\n## 结尾与收束硬约束（CC round-12，违反将判时间锚越界并切除/定点重生）\n" + "\n".join(_closing_lines) + "\n"

    # Chapter hook: only visible to last scene
    hook_block = ""
    if is_last_scene:
        hook = task_card.get("chapter_hook", "")
        if hook:
            hook_block = f"\n- 章末钩子（必须在本场景正文内自然收尾到该钩子，不得另起段落追加）：{hook}"

    # Negative examples block
    neg_block = ""
    if negative_examples:
        neg_lines = "\n".join(f"- 严禁出现：{ex}" for ex in negative_examples[:3])
        neg_block = f"\n## 负例（上一次错误生成的内容，严禁复现）\n{neg_lines}\n"
    # Reviewer-driven targeted rework block (scene-attributed fix only)
    fix_block = ""
    if fix_directive and str(fix_directive).strip():
        fix_block = (
            "\n## 审查返工指令（本场景上一版被扣分，必须针对性重写）\n"
            f"{str(fix_directive).strip()}\n"
            "- 用新的具体情节、动作、感官细节来解决上述问题；不得与其它场景重复同一事件或重复渲染同一氛围；\n"
            "  不得遗漏本场景 beats 必须涵盖的事件；重写后正文字数仍须达到本场景字数下限。\n"
        )

    # CC29：director 开场密度门（SOFT 或 fail-open）注入的针对性硬约束，按场号命中本场。
    _density_constraints = (task_card.get("_density_scene_constraints") or {})
    _dc39 = _density_constraints.get(str(scene_num)) or _density_constraints.get(scene_num)
    if _dc39:
        fix_block += (
            "\n## 开场事件密度硬约束（任务卡规划阶段密度不足，写作必须补齐，零容忍纯内省）\n"
            f"- {str(_dc39).strip()}\n"
            "- 该要求要在 scene_text 里落成可见动作/对话/环境新现象，不能用内心独白替代。\n"
        )

    # CC round-10 P0-1：内容密度契约（具体事件 / 具名互动 / 伏笔揭示 / 转世者评判性内在观察）
    _density_block = ""
    try:
        from novel_engine.quality.density_gate import normalize_scene_density
        _dens = normalize_scene_density(scene_blueprint)
        _dl: list[str] = []
        for _ev in _dens["concrete_events"]:
            if isinstance(_ev, dict):
                _e = _ev.get("event", ""); _a = _ev.get("observable_action", "")
                _dl.append(f"- 【事件】{_e}" + (f"；须写出可观察动作：{_a}" if _a and _a != _e else ""))
            else:
                _dl.append(f"- 【事件】{_ev}")
        for _it in _dens["named_interactions"]:
            if isinstance(_it, dict):
                _names = "、".join(str(c) for c in _it.get("characters", []))
                _dl.append(f"- 【具名互动】{_names} 之间须有一次实质互动（{_it.get('interaction_type','互动')}）：{_it.get('brief','')}")
        for _rv in _dens["info_reveal_points"]:
            if isinstance(_rv, dict):
                _dl.append(f"- 【信息/伏笔（{_rv.get('type','伏笔')}）】{_rv.get('content','')}")
        # CC round-22 P0-1：内在活动条款按主角当前能动性条件化
        from novel_engine.quality.pov_interiority_gate import is_restricted as _is_low_agency22, DEFAULT_FORBIDDEN_ABSTRACT as _FORB_ABS22
        _agency22 = str(task_card.get("protagonist_agency_level", "") or "").strip().lower()
        if _is_low_agency22(_agency22):
            _dl.append(
                "- 【内在活动·低能动性硬限制】主角此刻为婴儿/重伤/被囚等低能动性状态，不具备成人思考："
                "内心活动只允许碎片化感官印象、生理反应、模糊情绪（暖/冷/怕/安心），单处不超过50字；"
                "严禁出现 " + "、".join(_FORB_ABS22) + " 等抽象概念或前世身份术语，严禁连贯成人论证式独白"
            )
        else:
            _dl.append("- 【内在能动性】至少1处主角（或转世意识）对眼前人/事的具体评判、戒备、决断或前世对照（不是单纯感官描写）；内在活动不推进故事时间、不出现修炼体系词汇")
        _density_block = (
            "\n## 内容密度契约（CC round-10，违反将判“氛围堆砌、事件不足”并定点重生）\n"
            + "\n".join(_dl)
            + "\n以上每条都必须在 scene_text 里落成具体的动作、对话或明确的信息句，不能只在氛围描写里带过。\n"
        )
    except Exception:  # noqa: BLE001
        _density_block = ""

    # CC round-22 P0-1：低能动性章在 prompt 中再加一条显著硬限制
    _interiority_block = ""
    try:
        from novel_engine.quality.pov_interiority_gate import is_restricted as _ila22, DEFAULT_FORBIDDEN_ABSTRACT as _fab22
        _ag22 = str(task_card.get("protagonist_agency_level", "") or "").strip().lower()
        if _ila22(_ag22):
            _interiority_block = (
                "\n## 主角内心独白硬限制（CC round-22，违反将被确定性门截断或定点重生）\n"
                f"本章主角能动性等级为 {_ag22}（婴儿/重伤/被囚类，无成人思考能力）。心理活动只能是不超过50字的"
                "碎片化感官印象/生理反应/模糊情绪（暖、冷、怕、安心）；严禁成人式抽象、连贯论证独白，"
                "严禁出现 " + "、".join(_fab22) + " 等抽象概念或前世身份术语。\n"
            )
    except Exception:
        _interiority_block = ""

    # CC round-25 P0-3：场末钩子/反差意象的"技法类型"要求（只给类型与抽象效果，非范文，不新增检测门）
    try:
        from novel_engine.agents.craft_elements import craft_block as _craft_block25
        _craft_block25_s = _craft_block25(scene_blueprint)
    except Exception:  # noqa: BLE001
        _craft_block25_s = ""

    prompt = f"""请生成第 {chapter_num} 章第 {scene_num} 场景的正文。
{anchor_text}
## 本场景蓝图
{json.dumps(slim_scene_blueprint(scene_blueprint), ensure_ascii=False, indent=2)}

## 本场景必须涵盖的事件点（beats）
{chr(10).join(f'- {b}' for b in scene_blueprint.get('beats', []) if b)}
（beats 仅为简短情节点标签，每条不超过20字；所有具体叙事、描写与对话必须完整写进 scene_text，严禁把正文写进 beats）
（强制反摆烂）动笔写 scene_text 之前，必须先在 beats_covered 字段里把上方每个 beat 各用一句完整的话列出“本场景将写到的内容”，共须 {_beat_count} 条、一条对应一个 beat；确认无遗漏后再据此扩写 scene_text，严禁不列点或只写一两句正文就提前收尾。
{_density_block}{_interiority_block}{_craft_block25_s}
{hook_block}
## 本场景禁止涉及的事件点
{forbidden_block}
- 不得提前发生后续场景事件
{temporal_block}- 不得写结局/章末钩子（除非是本章最后一个场景）
- 不得复述前情
{neg_block}{fix_block}
## 写作指令
请严格按 scene_blueprint 中的 goal、conflict、emotion 写作。
场景地点：{scene_blueprint.get('location', '未知')}
出场人物：{', '.join(scene_blueprint.get('characters', []))}
节奏控制：场景内部要有张力起伏（冲突酝酿→爆发→余波），避免平铺直叙；对话与动作交替推进。
创新亮点：多用生动具体的细节和新鲜比喻，可安排小节内的意外转折，避免套路化表达。
目标字数：{_scene_tgt}字；scene_text 正文硬性不少于 {_scene_min} 个中文字（约 {_scene_paras} 个自然段）。
- 必须把本场景每个 beat 逐一演足（每拍约150-250字，动作、环境、感官细节、对话与即时心理交替展开），严禁概述、跳步或一笔带过
- scene_text 只允许成稿正文本身：严禁包含 JSON 键名/引号结构、换行缩进的代码样式或 ``` 围栏
## 写作纪律（CC round-29，硬性，违反将判注水/术语超规格并返工）
- 同一意象/感官细节/氛围词全章最多出现 1 次，禁止反复渲染同一氛围（如"逆流""粘稠""垂落"只准写一次）
- 单句不超过 40 字，超过必须用逗号断句成短句；段落间避免流水式连读
- 术语克制：主角/配角未知的事物一律用"异动、标记、纹路、旧物"等模糊词；禁止使用"符箓、阵纹、法阵、功法、灵力"等超规格修炼术语（除非任务卡明确允许）
- 每场景叙事重心必须落在动作/对白/新发现上；纯心理独白与环境描写合计占比不超过 30%，且心理描写须推进判断或决断，不做氛围注水
{closing_block}
## 结构化输出契约（强制）
只输出严格JSON {{"scene_id","scene_text","hook","beats","beats_covered"}}；scene_text 为纯叙事，禁任何【】括号指令；hook 为完整自然语句，可为空
- scene_id 取本场景 scene_num（{scene_num}）；beats 为本场景情节点字符串数组
- beats_covered 为必填字符串数组，必须逐条覆盖本场景全部 beat，条数不得少于 {_beat_count} 条；不足 {_beat_count} 条直接判格式失败并重写，严禁只回一两句
- hook 为独立字段，不得拼入 scene_text，不得加任何括号/标记包装（如【】/（）/章末钩子字样）
- 最终强制（CC round-25）：必须直接输出包含完整场景正文的 JSON，scene_text 必须写满全部 beats 到目标字数；禁止任何前导说明、前缀或思考文字，禁止提前结束，禁止空 content 或只回一两句。"""
    return prompt


def validate_scene_text(
    text: str, scene_target: int, must_beats: list[str] | None = None
) -> tuple[bool, list[str]]:
    """Deterministic scene text validation.

    Returns (passed, issues). Issues are strings describing problems.
    """
    issues: list[str] = []
    length_min = max(int(scene_target * 0.4), 300)
    if len(text) < length_min:
        issues.append(f"length_too_short: {len(text)} < {length_min}")

    # Truncation / no sentence ending (typical of finish_reason=length): the prose
    # must close on a sentence boundary, not stop mid-sentence or on an em-dash.
    # A closing quote (Chinese or ASCII half-width, which flash sometimes emits) is
    # legal when the character just inside it is a sentence terminator, e.g. 。”/。"
    _SENT_END = set("。！？…”.’」』）)!?~」』”’")
    _CLOSE_QUOTE = set("\"'”’")
    _tail = text.rstrip()
    if _tail:
        _probe = _tail[:-1] if (_tail[-1] in _CLOSE_QUOTE and len(_tail) >= 2) else _tail
        if _probe[-1] not in _SENT_END:
            issues.append("truncated_or_no_sentence_ending")

    # Placeholder / degradation detection
    for ph in _NEAR_EMPTY_PLACEHOLDERS:
        if ph in text:
            issues.append(f"placeholder_detected: {ph}")
            break

    # High frequency repeat: same 8+ char substring appears >= 3 times
    for substr_len in (8, 10, 12):
        from collections import Counter
        freq: Counter = Counter()
        for i in range(len(text) - substr_len + 1):
            sub = text[i:i + substr_len]
            if len(set(sub)) >= substr_len * 0.5:  # not all same char
                freq[sub] += 1
        for sub, count in freq.items():
            if count >= 3:
                issues.append(f"high_freq_repeat: {sub!r} x{count}")
                break
        if issues and any("high_freq_repeat" in i for i in issues):
            break

    # Continuous same char run >= 6
    if re.search(r"(.)\1{5,}", text):
        issues.append("continuous_same_char")

    # CC round-9（真机反例）：场景正文必须是纯叙事，flash 偶尔先写一段“概述预演”再用
    # “## 正文”之类的 markdown/分节标题重演后续场景，造成跨场景重复。命中即判结构缺陷重生。
    if (re.search(r"(?m)^\s*#{1,6}\s*\S", text)
            or "## 正文" in text or "＃＃正文" in text
            or re.search(r"(?m)^\s*正文\s*[:：]?\s*$", text)):
        issues.append("markdown_scaffolding")

    return (len(issues) == 0, issues)


def detect_scene_overlap(scenes: list) -> dict[int, list[str]]:
    """Detect cross-scene overlap using k-gram intersection ratio.

    Returns dict mapping scene_id -> list of overlap descriptions.
    """
    result: dict[int, list[str]] = {}
    normalized = []
    for s in scenes:
        sid = getattr(s, "scene_id", None)
        if sid is None:
            sid = s.get("scene_id", 0) if isinstance(s, dict) else 0
        text = getattr(s, "scene_text", None)
        if text is None:
            text = s.get("scene_text", "") if isinstance(s, dict) else ""
        ngrams = _chinese_ngrams(text, OVERLAP_NGRAM_K)
        normalized.append((sid, text, ngrams))

    for i in range(len(normalized)):
        for j in range(i + 1, len(normalized)):
            sid_i, text_i, ngrams_i = normalized[i]
            sid_j, text_j, ngrams_j = normalized[j]
            if not ngrams_i or not ngrams_j:
                continue
            intersection = ngrams_i & ngrams_j
            min_size = min(len(ngrams_i), len(ngrams_j))
            if min_size == 0:
                continue
            ratio = len(intersection) / min_size
            if ratio > OVERLAP_RATIO:
                # Find representative long common n-grams
                common = sorted(intersection, key=len, reverse=True)[:3]
                desc = f"scene_{sid_i} vs scene_{sid_j}: {ratio:.2%} overlap, samples: {', '.join(common[:2])}"
                result.setdefault(sid_i, []).append(desc)
                result.setdefault(sid_j, []).append(desc)
    return result
