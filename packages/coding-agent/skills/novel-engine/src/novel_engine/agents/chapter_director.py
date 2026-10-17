"""
章节导演 Agent：生成任务卡 JSON。
职责：
1. 读取 bible + plot_graph + constraints + author_intent
2. 确定本章在 DAG 中的节点
3. 生成任务卡（目标、冲突、情绪曲线、场景蓝图、伏笔动作）
4. 输出结构化 JSON 供缩写生成环节使用
"""
# -*- coding: utf-8 -*-
import json
import logging
import re
import time
from pathlib import Path
from typing import Optional

from novel_engine.core.llm_client import LLMClient
from novel_engine.core.quality_policy import load_quality_policy, derive_scene_targets, is_blocking
from novel_engine.agents.world_simulator import WorldSimulator
from novel_engine.core.memory_manager import MemoryManager
from novel_engine.engine.db import StateDB

logger = logging.getLogger(__name__)

# T1: 大纲文件名常量，集中管理避免硬编码散落在代码各处
OUTLINE_FILENAME = '吸氧证道_V2_1_完整大纲.md'
# T1: 候选路径（从运行根出发），按优先级排序
_OUTLINE_CANDIDATE_REL = ['docs', 'planning']

# CC round-30：章内场景时间顺序校验用的时间词排序表（粗粒度，含回溯词标记）。
# 用于 detect_task_card_scene_time_inversion：场景 narrative_time 不得随 scene 序号倒置。
_SCENE_TIME_ORDER = [
    ("清晨", 1), ("早晨", 1), ("黎明", 1), ("拂晓", 1), ("破晓", 1), ("鸡鸣", 1),
    ("早上", 2), ("上午", 2),
    ("正午", 3), ("中午", 3),
    ("午后", 4), ("下午", 4),
    ("傍晚", 5), ("黄昏", 5),
    ("入夜", 6), ("晚间", 6), ("晚上", 6),
    ("深夜", 8), ("子夜", 8), ("三更", 8), ("四更", 8),
    ("夜", 7),
    ("五更", 9),
]
_SCENE_TIME_BACKREF = ("昨日", "昨夜", "前日", "前一天", "先前", "回忆", "闪回", "回想", "回述")
# 跨日前缀：命中时时间序整体 +10 档，保证"深夜→次日清晨"等自然跨日推进不判倒置。
_SCENE_TIME_DAY_SHIFT = re.compile(r"(次日|翌日|来日|明早|明天|第[一二两三四五六七八九十\d]+[天日]|[三四五][天日]后)")


_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _day_offset(s: str) -> int:
    """解析时间词里的跨日天数：次日/翌日=1 天，第三日=3 天，三天后=3 天。返回天数。"""
    if not s:
        return 0
    m = re.search(r"第([一二两三四五六七八九十\d]+)[天日]", s)
    if m:
        tok = m.group(1)
        if tok.isdigit():
            return int(tok)
        if tok in _CN_NUM:
            return _CN_NUM[tok]
        if tok.startswith("十"):
            return 10 + _CN_NUM.get(tok[1:], 0)
        return 0
    m = re.search(r"([一二两三四五六七八九十\d]+)[天日]后", s)
    if m:
        tok = m.group(1)
        if tok.isdigit():
            return int(tok)
        if tok in _CN_NUM:
            return _CN_NUM[tok]
        return 0
    if _SCENE_TIME_DAY_SHIFT.search(s):
        return 1
    return 0


def _scene_time_order(s: str) -> int:
    """返回时间词排序值；未知返回 0（不参与倒置判定）。

    跨日前缀（次日/翌日/第N日/N日后/明早/明天）按天数整体 +10*N 档偏移：
    "深夜"(8) → "次日清晨"(11) 是合法跨日推进；同日"午后"(4) → "上午"(2)
    仍判倒置；"第三日清晨"(21) 继续递增。
    """
    if not s:
        return 0
    day_shift = 10 * _day_offset(s)
    for kw, order in _SCENE_TIME_ORDER:
        if kw in s:
            return day_shift + order
    return 0


def detect_task_card_scene_time_inversion(scenes: list[dict]) -> list[str]:
    """检测任务卡内场景叙事时间的顺序倒置（如 ch141：1午后→2上午/傍晚→3午后→4黄昏）。

    时间模型为 (日期档, 时段档) 二元组：显式跨日词（次日/第N日/N天后）决定日期档；
    裸时段词（上午/午后/深夜等）承接前序场景的日期档（同一章内多日推进时，
    "次日清晨" 后接 "上午" 同指次日，不判倒置）。回溯/闪回场景重置链条。
    未知时段词（order=0）跳过。返回可读错误列表，空=无倒置。
    """
    errors: list[str] = []
    ordered = sorted(
        (s for s in scenes if isinstance(s, dict)),
        key=lambda s: int(s.get("sequence_index", s.get("scene_num", 0)) or 0),
    )
    prev_day = 0  # 前序场景日期档（0=当日）
    prev_order = 0
    prev_label = ""
    for s in ordered:
        nt = str(s.get("narrative_time", "") or "").strip()
        if not nt:
            continue
        # 回溯/闪回场景（回忆、昨夜等）不参与单调判定，重置链条
        if any(b in nt for b in _SCENE_TIME_BACKREF):
            prev_day = 0
            prev_order = 0
            prev_label = ""
            continue
        day = _day_offset(nt)
        if day == 0 and prev_day > 0:
            day = prev_day  # 裸时段词承接前序日期档（"次日清晨"→"上午"同指次日）
        order = _base_time_order(nt)
        if order == 0:
            # 未知时间词不断链：沿用前一已知序，防跨未知词漏检倒置
            continue
        if prev_order and (day < prev_day or (day == prev_day and order < prev_order)):
            # 深夜档(入夜/深夜/子夜/夜/五更)之后接清晨/上午类(清晨/黎明/拂晓/早上/上午)
            # 是隐含跨日推进（"当晚深夜 → 次日清晨"），不算倒置；更新日期档后放行。
            if prev_order >= 6 and order <= 2:
                day = prev_day + 1
            else:
                errors.append(
                    f"场景{int(s.get('scene_num', 0))} 叙事时间「{nt}」早于前序场景「{prev_label}」，"
                    f"章内时间顺序倒置（{prev_label} → {nt}），请按故事时序重排场景或修正 narrative_time"
                )
        prev_day = day
        prev_order = order
        prev_label = nt
    return errors


def _base_time_order(s: str) -> int:
    """仅时段档（无日期偏移）：清晨=1 … 深夜=8。未知返回 0。"""
    if not s:
        return 0
    for kw, order in _SCENE_TIME_ORDER:
        if kw in s:
            return order
    return 0


def parse_chapter_tasks(content: str) -> dict[int, str]:
    """T2: pure function to parse all chapter-task tables from outline content.
    Returns {chapter_num: task_text}. Later entries override earlier ones.
    R2: tolerates malformed rows missing leading '|', fullwidth pipes, extra whitespace.
    No IO side effects -- can be unit-tested offline.
    """
    # R2: normalize fullwidth pipes to halfwidth before parsing
    content = content.replace(chr(0xFF5C), '|').replace(chr(0x2223), '|')
    result: dict[int, str] = {}
    header_re = re.compile(
        r'^\s*\|\s*[章Chapterschapter]+\s*\|\s*核心任务\s*\|',
        re.IGNORECASE,
    )
    # R2: match both '| N | text |' and 'N | text |' (missing leading pipe)
    # Also tolerates fullwidth pipe '｜' and trailing optional pipe
    row_re = re.compile(
        r'^\s*[|｜]?\s*(\d+)\s*[|｜]\s*(.+?)\s*[|｜]?\s*$'
    )
    in_table = False
    for line in content.splitlines():
        stripped = line.rstrip()
        if header_re.match(stripped):
            in_table = True
            continue
        if in_table:
            # R2: table ends on empty line OR non-pipe, non-number-leading line
            if stripped == '':
                in_table = False
                continue
            # Accept rows that either start with | or start with a digit (malformed)
            if not (stripped.startswith('|') or re.match(r'^\s*\d+\s*[|｜]', stripped)):
                in_table = False
                continue
            m = row_re.match(stripped)
            if m:
                ch_num = int(m.group(1))
                task = m.group(2).strip()
                if task:
                    result[ch_num] = task
    return result


# P1 director 相位整章累计墙钟预算（秒）；超限时快速失败
DIRECTOR_PHASE_BUDGET_S = 600
# P1 连续 Server disconnected 判定为"体量墙"的阈值；达到此次数即快速失败
_CONNECT_WALL_THRESHOLD = 2


def _bp_beats(bp: dict) -> list[str]:
    b = bp.get("beats")
    if isinstance(b, list):
        return [str(x).strip() for x in b if str(x).strip()]
    return []


def _is_thin_scene(bp: dict, min_beats: int = 2, min_goal_chars: int = 16) -> bool:
    beats = _bp_beats(bp)
    goal = str(bp.get("goal", "") or "").strip()
    if len(beats) >= min_beats:
        return False
    return len(beats) == 0 or len(goal) < min_goal_chars


def _join_field(a: dict, b: dict, field: str, sep: str) -> str:
    va = str(a.get(field, "") or "").strip()
    vb = str(b.get(field, "") or "").strip()
    if va and vb and vb not in va:
        return va + sep + vb
    return va or vb or ""


def _merge_two_bp(a: dict, b: dict) -> dict:
    beats = _bp_beats(a)
    for x in _bp_beats(b):
        if x not in beats:
            beats.append(x)
    chars: list = []
    for c in (a.get("characters") or []) + (b.get("characters") or []):
        if c not in chars:
            chars.append(c)
    merged = {**a, **b}
    merged["beats"] = beats
    merged["characters"] = chars
    return merged


# ── C3 R4：多场任务卡 must_cover 注入（软生成，不建硬门）───────────────────

def _inject_must_cover_opening_anchors(task_card: dict) -> dict:
    """C3 R4：为 scene_num>1 的场注入开头须有明确时间+空间/人物状态锚点的 must_cover beat。

    仅追加、不覆盖已有 must_cover_beats；ch5 额外追加显式确认 beat。
    纯函数、零 LLM、可离线单测。复用 _enforce_mandatory_beats 的 must_cover 机制，
    不建独立硬门。
    """
    if not isinstance(task_card, dict):
        return task_card
    bps = task_card.get("scene_blueprints") or []
    has_multi = any(
        isinstance(bp, dict) and int(bp.get("scene_num") or 0) > 1
        for bp in bps
    )
    if not has_multi:
        return task_card

    ch_num = int(task_card.get("chapter_num") or 0)
    existing = task_card.get("must_cover_beats") or []
    if not isinstance(existing, list):
        existing = []

    for bp in bps:
        if not isinstance(bp, dict):
            continue
        sn = int(bp.get("scene_num") or 0)
        if sn <= 1:
            continue
        loc = str(bp.get("location") or "").strip()
        anchor_text = f"场景{sn}开头须有明确时间词+地点锚点（如'{loc}'）或人物状态描写"
        existing.append({
            "scene_num": sn,
            "beat_text": anchor_text,
            "category": "opening_anchor",
        })

    # ch5 显式确认 beat：陈老根确认陆烬体弱、对浊气异常敏感，严禁气感内视吐纳
    if ch_num == 5:
        ch5_confirm = {
            "scene_num": None,
            "beat_text": "陈老根确认陆烬体弱多病、对浊气异常敏感；严禁出现气感/内视/吐纳（气感全书最早654章）",
            "category": "confirmation",
        }
        if not any(b.get("category") == "confirmation" for b in existing):
            existing.append(ch5_confirm)

    task_card["must_cover_beats"] = existing
    return task_card


def merge_thin_scene_blueprints(
    bps: list, chapter_target: int, min_beats: int = 2, min_goal_chars: int = 16
) -> list:
    """Deterministically merge under-specified transitional scenes into neighbours.

    Zero LLM calls and no padding: a scene with fewer than ``min_beats`` substantive
    beats (or no beats plus a very short goal) is folded into the next scene (or the
    previous one when it is last), preserving order. Scene numbers are reassigned and
    word_count_target redistributed per surviving scene.
    At least two scenes are retained. Only applied when there are more than five scenes.
    """
    from novel_engine.core.quality_policy import derive_scene_targets
    out = [dict(b) for b in (bps or [])]
    changed = True
    while changed and len(out) > 5:
        changed = False
        for i, bp in enumerate(out):
            if not _is_thin_scene(bp, min_beats, min_goal_chars):
                continue
            if i + 1 < len(out):
                out[i + 1] = _merge_two_bp(bp, out[i + 1])
                del out[i]
            else:
                out[i - 1] = _merge_two_bp(out[i - 1], bp)
                del out[i]
            changed = True
            break
    targets = derive_scene_targets(int(chapter_target), max(1, len(out)))
    n = max(1, len(out))
    for i, bp in enumerate(out):
        bp["scene_num"] = i + 1
        bp["word_count_target"] = targets[i] if i < len(targets) else int(chapter_target) // n
    return out


# CC round-13：源头治理占位角色名。无 canonical 名的群众一律归一为明确身份指代。
_PLACEHOLDER_NAME_RE = __import__("re").compile(
    r"(村民|路人|行人|村人|乡人|角色|人物|群众|那人|对方)\s*[A-Za-z0-9甲乙丙丁一二三四五六]*$")


def _normalize_placeholder_characters(task_card: dict) -> dict:
    if not isinstance(task_card, dict):
        return task_card
    for bp in task_card.get("scene_blueprints", []) or []:
        if not isinstance(bp, dict):
            continue
        its = bp.get("named_interactions")
        if not isinstance(its, list):
            continue
        for it in its:
            if not isinstance(it, dict):
                continue
            chars = it.get("characters")
            if not isinstance(chars, list) or not chars:
                continue
            ph_idx = [i for i, c in enumerate(chars)
                      if isinstance(c, str) and _PLACEHOLDER_NAME_RE.match(c.strip())]
            if not ph_idx:
                continue
            canon = [c for i, c in enumerate(chars) if i not in ph_idx]
            role = "两名村民" if len(ph_idx) >= 2 else "一名村民"
            merged = canon + [role]
            seen, uniq = set(), []
            for c in merged:
                if c not in seen:
                    seen.add(c)
                    uniq.append(c)
            it["characters"] = uniq[:2] if len(uniq) >= 2 else uniq
    # CC round-22 P0-2：源头清洗 scene_constraints
    try:
        from novel_engine.quality.constraint_compliance_gate import (
            sanitize_task_card_constraints as _sanitize_scene_constraints22)
        _sanitize_scene_constraints22(task_card)
        from novel_engine.quality.scene_progression_gate import (
            sanitize_task_card_progression as _sanitize_scene_progression23)
        _sanitize_scene_progression23(task_card)
    except Exception:
        pass
    # CC round-25 P0-3：确定性补齐 scene_craft_elements
    try:
        from novel_engine.agents.craft_elements import ensure_scene_craft_elements
        ensure_scene_craft_elements(task_card)
    except Exception:
        pass
    return task_card


# CC round-32：剥离 LLM 返回的 markdown 代码块包装（如 ```json ... ```）以便 json.loads 解析。
def _strip_markdown_json(text: str) -> str:
    if not isinstance(text, str):
        return text
    s = text.strip()
    # Match ```language\n{...}\n``` pattern
    m = re.match(r'^```[a-z]*\s*\n?(.*?)\n?\s*```\s*$', s, re.DOTALL)
    if m:
        return m.group(1).strip()
    return s


# CC round-10：逐章大纲任务强制渲染函数。当 chapter_outline_task 非空时生成
# 置顶最高优先级区块，确保三处 director prompt（骨架/craft/metadata）都能看到本章任务。
def _render_outline_mandate(context: dict) -> str:
    """返回本章大纲任务的强制渲染块；task 为空时返回空串。"""
    task = (context.get('chapter_outline_task') or '').strip()
    if not task:
        return ''
    adjacent = (context.get('adjacent_tasks') or '').strip()
    override = (context.get('outline_override_hint') or '').strip()
    lines = [
        '## 【本章最高优先级硬约束——大纲核心任务】',
        f'本章唯一必须发生的核心事件：{task}。',
        '此任务优先级高于近期章节惯性、卷大纲泛述、角色圣经默认行为。四个场景必须共同把这一核心事件演出来——',
        '不得重演前情，不得提前演相邻章事件。',
        '任务中的专有名词（人名/地名/物件）必须按大纲具名原样登场，禁止用"族老/里正/几位老人/村民们/那个人"等泛称替换或省略。',
        '至少一个场景直接正面演绎该核心事件（如：召集并主持村议、众人表决、拍板留下禁忌说法），其余场景围绕其起因/经过/余波展开。',
    ]
    if adjacent:
        lines.append(f'相邻章边界（上一章已发生/下一章才发生，本章不得重演或提前透支）：{adjacent}')
    if override:
        lines.append(f'优先级说明：{override}')
    lines.append('---')
    return '\n'.join(lines)


class ChapterDirector:
    """章节导演：将剧情规划转化为可执行的任务卡。"""

    # CC round-32：通用兜底 empty system；三个 _call_* 方法各自注入专用精简 system prompt，
    # 避免骨架阶段被原1930字整卡规则诱导膨胀导致 length 截断。
    SYSTEM_PROMPT = ""

    # 骨架专用 system：只要求正好4场、每字段字数上限，严禁 craft/density/opening/夜章等重规则。
    _SKELETON_SYSTEM = (
        "你是章节导演，任务是生成本章的场景骨架。\n"
        "输出必须正好包含4个场景，scene_num=1,2,3,4，不多不少。\n"
        "每个字段严格守字数上限，不得展开描写。\n"
        "- narrative_time：<6字\n"
        "- location：<10字\n"
        "- characters：短ID数组，2-3个\n"
        "- goal：<20字\n"
        "- conflict：<20字\n"
        "- emotion：<8字\n"
        "- beats：3条，每条<14字\n"
        "示例（单场，其余3场同理）：\n"
        '{"scene_num": 1, "narrative_time": "清晨", "location": "村口古树", "characters": ["林远"], '
        '"goal": "打探邻村魔物踪迹", "conflict": "村民讳莫如深不肯说", "emotion": "疑惑隐忧", '
        '"beats": ["出发探路", "询问村民被拒", "察觉异常跟踪"]}\n'
        "严格照示例极简程度输出JSON，不要解释。"
    )

    # Craft专用 system：只讲7个craft字段如何填，不重复骨架规则。字段字数严格压限。
    _CRAFT_SYSTEM = (
        '你是章节导演，任务是在已有4场骨架上补充craft字段。\n'
        '保持场景数量不变（scene_num=1,2,3,4），仅追加以下7个字段：\n'
        '- concrete_events：固定2条，每条{"event":"≤12字","observable_action":"≤12字"}，必须是可观察的外部事件（人物动作/对话/物件变化），禁止纯心理/氛围类\n'
        '- named_interactions：只保留有实质互动的（无则[]），{"characters":["角色名","角色名"],"interaction_type":"争执/对话/冲突","brief":"≤20字"}，每场至少1个具名对手戏角色，禁止无对手独角戏\n'
        '- info_reveal_points：2-3条{"type":"伏笔埋设","content":"≤12字","anchor_terms":["名词1","名词2"]}\n'
        '- protagonist_interiority：≤20字\n'
        '- scene_constraints：有强制约束时列出，无则[]\n'
        '- scene_progression_contract：{"new_state_or_entity":["≤2个具体新状态或新信息"],"irreversible_change":"≤12字，必须是可以验证的改变（不是空泛的"关系变化"）"}\n'
        '- scene_craft_elements：{"info_reversal_point":{"present":false,"content":""}}\n'
        '【发展场反饱和规则】每个非高潮场景必须：①有具体外部事件（非内心/氛围）；②有具名对手角色发生实际对话或冲突；③有一个不可逆的新状态/新信息推进章钩子；④禁止同场或跨场重复同一微循环（如"夜啼-安抚-喂食"反复铺陈）；⑤静态氛围/心理独白占比设上限，必须让位给外部推进。\n'
        '严格照示例风格输出，每个字段尽量精简，不要解释。'
    )

    # Metadata专用 system：只讲章级字段。
    _METADATA_SYSTEM = (
        '你是章节导演，任务是在已有4场骨架上生成章级元数据。\n'
        '仅输出以下字段，每个值尽量精简：\n'
        '- protagonist_agency_level：normal|injured|infant_low|imprisoned\n'
        '- core_goal：<30字\n'
        '- conflicts：{"internal":"内心冲突<20字","external":"外部冲突<20字"}\n'
        '- emotion_curve：{"start":"起始","middle":"中间","climax":"高潮","end":"结尾钩子"}，每值<8字\n'
        '- chapter_hook：<30字\n'
        '- foreshadow_actions：[{"foreshadow_id":"F001","action":"具体指令<20字","intensity":"隐晦提示|明显异样|接近揭露前兆"}]\n'
        '- chapter_events：[{"event_type":"类型","one_line_summary":"一句话20-40字","participants":["人物"],"location":"地点","narrative_time":"时间","consequence_state":"结果状态<20字"}]\n'
        '- state_changes：[{"type":"character_realm","target":"目标ID","new_value":"新值","chapter":章节号}]\n'
        '- foreshadow_execution：[{"foreshadow_id":"F001","executed":true/false,"note":"如何执行<20字"}]\n'
        '- end_state：{"narrative_position":"最后一帧画面<30字","location":"末场地点","completed_actions":["已演完动作"],"pending_actions":["下一章接续动作<2个"],"time_marker":"夜/当日"}\n'
        '- timeline_anchor：{"chapter_start_marker":"开场时间","max_time_progression":"最大时间跨度<20字","forbidden_markers":["禁止越界词<3个"]}\n'
        '严格照上述格式输出JSON，不得增删字段。'
    )

    def __init__(self, project_root: str | Path = None, llm_client: Optional[LLMClient] = None):
        self.root = Path(project_root or Path(__file__).parent.parent)
        self.llm = llm_client or LLMClient.from_config(self.root / "config" / "runtime_config.json")
        self.simulator = WorldSimulator(self.root)
        self.memory = MemoryManager(self.root)

        # Instantiate StateDB
        db_dir = self.root / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        self.db = StateDB(db_path=str(db_dir / "state.db"), project_root=self.root)

        # Parse outlines and author intents by volume
        self._outline_sections = self._parse_outline()
        # T1: 记录大纲是否成功加载
        self._outline_loaded = hasattr(self, '_outline_loaded') and self._outline_loaded
        self._intent_sections = self._parse_author_intent()

        # Bible cache: loaded once at init, reused for all chapters
        self._bible_cache = {}
        self._load_bible_cache()
        # P1 连接墙计数 + 墙钟计时
        self._connect_wall_count = 0
        self._phase_start_ts: float = 0.0

    def _parse_outline(self) -> dict:
        """解析完整大纲，按卷拆分。T1：多候选路径定位，缺失时显式报错。"""
        # T1: 按候选路径查找大纲文件
        outline_path: Path | None = None
        _tried: list[str] = []
        for _rel in _OUTLINE_CANDIDATE_REL:
            _candidate = self.root / _rel / OUTLINE_FILENAME
            _tried.append(str(_candidate))
            if _candidate.exists():
                outline_path = _candidate
                break
        # T1: 找不到时显式报错，不再静默返回 {}
        if outline_path is None:
            logger.error(
                f'outline file not found (tried: {" | ".join(_tried)})'
            )
            self._outline_loaded = False
            return {}
        self._outline_loaded = True

        content = outline_path.read_text(encoding="utf-8")

        volume_markers = [
            ("V01", "## 四、第一卷《昆仑遗子》"),
            ("V02", "## 五、第二卷《大乾人间》"),
            ("V03", "## 六、第三卷《仙门初渡》"),
            ("V04", "## 七、第四卷《万山寻仙》"),
            ("V05", "## 八、第五卷《人间藏仙》"),
            ("V06", "## 九、第六卷《阴阳双界》"),
            ("V07", "## 十、第七卷《天门之后》"),
            ("V08", "## 十一、第八卷《仙庭旧墟》"),
            ("V09", "## 十二、第九卷《伐天人皇》"),
            ("V10", "## 十三、第十卷《人道定仙天》"),
            ("END", "## 十四、修炼境界与道具对照表")
        ]

        sections = {}
        for i in range(len(volume_markers) - 1):
            vid, marker = volume_markers[i]

            start_idx = content.find(marker)
            if start_idx == -1:
                continue

            # Find next marker that exists
            next_marker = ""
            for j in range(i + 1, len(volume_markers)):
                m_next = volume_markers[j][1]
                if content.find(m_next) != -1:
                    next_marker = m_next
                    break

            if not next_marker:
                sections[vid] = content[start_idx:]
            else:
                end_idx = content.find(next_marker, start_idx)
                sections[vid] = content[start_idx:end_idx]

        return sections

    def _parse_author_intent(self) -> dict:
        """解析作者意图，按卷/阶段拆分。"""
        intent_path = self.root / "bible" / "author_intent.md"
        if not intent_path.exists():
            return {}

        content = intent_path.read_text(encoding="utf-8")

        markers = [
            ("V01", "## 第一阶段：昆仑遗子"),
            ("V02", "## 第二阶段：大乾人间"),
            ("V03", "## 第三阶段：仙门初渡"),
            ("V04", "## 第四阶段：万山寻仙"),
            ("V05", "## 第五阶段：人间藏仙"),
            ("V06", "## 第六阶段：阴阳双界"),
            ("V07", "## 第七阶段：天门之后"),
            ("V08", "## 第八阶段：仙庭旧墟"),
            ("V09", "## 第九阶段：伐天人皇"),
            ("V10", "## 第十阶段：人道定仙天"),
            ("END", "------")
        ]

        sections = {}
        for i in range(len(markers) - 1):
            vid, marker = markers[i]
            start_idx = content.find(marker)
            if start_idx == -1:
                continue

            # Find next marker that exists
            next_marker = ""
            for j in range(i + 1, len(markers)):
                m_next = markers[j][1]
                if content.find(m_next) != -1:
                    next_marker = m_next
                    break

            if not next_marker:
                sections[vid] = content[start_idx:]
            else:
                end_idx = content.find(next_marker, start_idx)
                sections[vid] = content[start_idx:end_idx]

        return sections

    def _get_volume_id(self, chapter_num: int, volumes: dict) -> str:
        """根据章节号获取卷 ID。"""
        for vol in volumes.get("volumes", []):
            range_start, range_end = vol.get("chapter_range", [0, 0])
            if range_start <= chapter_num <= range_end:
                return vol.get("id", "V01")
        return "V01"

    def _load_bible_cache(self):
        """一次性加载 bible 文件并缓存（永不改变）。"""
        bible_files = {
            'world': 'bible/world_bible.md',
            'character': 'bible/character_bible.md',
            'style': 'bible/style_bible.md',
            'author_intent': 'bible/author_intent.md',
            'ending': 'bible/ending_bible.md',
        }
        for key, rel_path in bible_files.items():
            full_path = self.root / rel_path
            if full_path.exists():
                self._bible_cache[key] = full_path.read_text(encoding="utf-8")
            else:
                self._bible_cache[key] = ""

    def _load_mid_outline_entry(self, chapter_num: int) -> dict | None:
        """加载本章的中纲条目（config/planning/mid_outline_*.json）。

        门禁（逐条级，阶段1 起）：
        - entry.status 存在时以其为准：approved 可用；pending_human/needs_regenerate
          不可用（挂起单章，不阻塞批次，调用方退回松散参考逻辑）。
        - entry.status 缺失（旧文件）时退回文件级 meta.status：approved 才可用；
          draft 一律不可用（须经 audit_mid_outline.py 校验、写回逐条 status）。
        """
        try:
            planning_dir = self.root / "config" / "planning"
            if not planning_dir.exists():
                return None
            for p in sorted(planning_dir.glob("mid_outline_*.json")):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    continue
                if not isinstance(data, dict):
                    continue
                meta = data.get("meta") or {}
                entries = data.get("entries") or {}
                entry = entries.get(str(chapter_num))
                if not isinstance(entry, dict):
                    continue
                if not str(entry.get("core_event", "") or "").strip():
                    continue
                entry_status = str(entry.get("status", "") or "").strip().lower()
                if entry_status:
                    # 逐条门禁（阶段1）：仅 approved 可用
                    if entry_status != "approved":
                        continue
                else:
                    # 旧数据兼容：退回整文件级门禁
                    if str(meta.get("status", "")).strip().lower() != "approved":
                        continue
                return entry
            return None
        except Exception:
            return None

    def _build_mid_outline_hint(self, entry: dict | None) -> str:
        """把中纲条目格式化为注入 prompt 的强约束文本（无条目返回空串）。"""
        if not entry:
            return ""
        parts = [f"【本章中纲·章级意图（已校验，强约束）】{str(entry.get('core_event', ''))}"]
        chars = entry.get("characters") or []
        if chars:
            parts.append(f"涉及角色：{', '.join(str(c) for c in chars)}")
        note = entry.get("boundary_note")
        if note:
            parts.append(f"边界注意：{note}")
        return "\n".join(parts)

    @staticmethod
    def _extract_forbidden_list(intent_text: str) -> list:
        """从作者意图段的 forbidden 块提取禁止事项列表（YAML 风格 '- "..."' 行）。"""
        if not intent_text:
            return []
        forbidden = []
        collecting = False
        for line in intent_text.splitlines():
            stripped = line.strip()
            if stripped.startswith('forbidden:'):
                collecting = True
                continue
            if collecting:
                if stripped.startswith('- '):
                    forbidden.append(stripped[2:].strip().strip('"').strip("'"))
                elif stripped and not stripped.startswith('#'):
                    collecting = False  # 下一个键（note/theme 等）即列表结束
        return forbidden


    # CC 2026-10-06：传授既成 vs 提及/认知 的边界例句（随 forbidden 列表注入，
    # 从生成源头降低"教我呼吸的法子"式传授既成句发生率；ch59/ch60 实证来源）。
    _TEACH_BOUNDARY_HINT = (
        "- 呼吸法边界（传授既成 vs 提及/认知）：可以说『陆烬知道根伯会些调息的"
        "法子』（认知/提及，允许）；不可以说『根伯教我呼吸的法子』『教我吐纳』"
        "『传我呼吸法』（传授既成——方法已交付陆烬，A 路线禁止）。"
        "陆烬不得出现静坐吐纳/引气/气流流转/暖意灌注/气感等体感描写。\n"
    )

    def _load_json(self, path: Path) -> dict:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _load_text(self, path: Path) -> str:
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def get_context_for_chapter(self, chapter_num: int) -> dict:
        """构建本章生成所需的完整上下文。"""
        # 使用缓存的 bible 文件（避免重复 I/O）
        bible_files = {
            "world": self._bible_cache.get("world", ""),
            "character": self._bible_cache.get("character", ""),
            "style": self._bible_cache.get("style", ""),
            "author_intent": self._bible_cache.get("author_intent", ""),
            "ending": self._bible_cache.get("ending", ""),
        }

        # 加载规划数据
        volumes = self._load_json(self.root / "config" / "planning" / "volumes.json")
        plot_graph = self._load_json(self.root / "config" / "planning" / "plot_graph.json")

        vid = self._get_volume_id(chapter_num, volumes)
        volume_outline = self._outline_sections.get(vid, "")
        author_intent = self._intent_sections.get(vid, "")
        if not author_intent:
            author_intent = bible_files.get("author_intent", "")[:2000]

        # 查找本章对应的 plot 节点
        relevant_nodes = []
        for node in plot_graph.get("nodes", []):
            target = node.get("chapter_target", 0)
            if abs(target - chapter_num) <= 20:  # 前后20章内的相关节点
                relevant_nodes.append(node)

        # 加载约束
        constraints_summary = self.simulator.get_constraints_summary(chapter_num)

        # 加载伏笔
        foreshadow_registry = self._load_json(self.root / "config" / "foreshadow" / "registry.json")
        relevant_foreshadows = []
        for fs in foreshadow_registry.get("foreshadows", []):
            plant_ch = fs.get("plant_chapter", 0)
            resolve_ch = fs.get("resolve_chapter", 0)
            if plant_ch <= chapter_num <= resolve_ch:
                relevant_foreshadows.append(fs)
        # 世界状态
        world_state = self.simulator.build_world_state_for_chapter(chapter_num)

        # RAG 历史检索：精确 + 混合语义补充（防隐含关系/动机漏检）
        recent_summaries = self.memory.get_recent_summaries(chapter_num, count=5)
        keyword_history = self.memory.retrieve_by_keywords(
            [f"chapter_{chapter_num}"], limit=5,
        )
        # 混合召回补充：若精确命中不足，尝试语义子串召回
        if len(keyword_history) < 3:
            try:
                extra = self.memory.hybrid_retrieve(f"chapter_{chapter_num}", limit=5)
                seen = {str(h.get("chapter")) for h in keyword_history}
                for h in extra:
                    if str(h.get("chapter")) not in seen:
                        keyword_history.append(h)
                    if len(keyword_history) >= 5:
                        break
            except Exception:
                pass

        # 质量记忆
        quality_memory = self._load_json(self.root / "memory" / "quality_memory.json")

        # Query active foreshadows from StateDB + 混合语义补充
        active_foreshadows = self.db.query_active_foreshadows(chapter_num)
        try:
            # 补充：对人物/关系做一次轻量语义检索，丰富上下文（不改变原有时序过滤）
            hybrid_hits = self.db.hybrid_search(f"chapter {chapter_num}", limit=4)
            if hybrid_hits:
                # 仅作为调试信息，不直接合并到 active_foreshadows，避免时序污染
                logger.debug(f"Hybrid search hits for ch{chapter_num}: {len(hybrid_hits)}")
        except Exception:
            pass

        return {
            "chapter_num": chapter_num,
            "bible": bible_files,
            "volumes": volumes,
            "relevant_plot_nodes": relevant_nodes,
            "constraints": constraints_summary,
            "foreshadows": relevant_foreshadows,
            "active_foreshadows": active_foreshadows,
            "world_state": world_state,
            "quality_memory": quality_memory,
            "recent_summaries": recent_summaries,
            "keyword_history": keyword_history,
            "volume_outline": volume_outline,
            "author_intent": author_intent,
        }

    def _try_template_task_card(self, chapter_num: int, dynamic_context: dict) -> dict | None:
        """
        尝试使用模板任务卡：当 plot_graph 中存在精确匹配的节点时，
        直接返回结构化任务卡，跳过 LLM 调用以节省成本。
        返回 None 表示需要走 LLM 路径。
        """
        plot_graph = dynamic_context.get("plot_graph", {})
        foreshadow_registry = dynamic_context.get("foreshadow_registry", {})

        matching_nodes = [
            n for n in plot_graph.get("nodes", [])
            if n.get("chapter_target") == chapter_num
        ]
        if not matching_nodes:
            return None

        node = matching_nodes[0]
        node_type = node.get("type", "main_plot")
        if node_type not in ("main_plot", "character_development"):
            return None

        template_cache_dir = self.root / "cache" / "task_cards"
        template_cache_dir.mkdir(parents=True, exist_ok=True)
        template_file = template_cache_dir / f"chapter_{chapter_num}.json"

        if template_file.exists():
            try:
                cached = json.loads(template_file.read_text(encoding="utf-8"))
                if cached.get("_template") and cached.get("chapter_num") == chapter_num:
                    logger.info(f"Using cached template task card for chapter {chapter_num}")
                    cached["_from_template"] = True
                    return cached
            except (json.JSONDecodeError, OSError):
                pass

        foreshadow_ids = node.get("foreshadow_links", [])
        relevant_foreshadows = [
            fs for fs in foreshadow_registry.get("foreshadows", [])
            if fs.get("id") in foreshadow_ids
        ]

        # 单一策略源：场景字数目标来自 quality_policy（单场景卡独占整章目标）
        _policy = load_quality_policy(self.root)
        _scene_targets = derive_scene_targets(_policy["chapter_target_chars"], 1)
        task_card = {
            "chapter_num": chapter_num,
            "core_goal": node.get("description", ""),
            "conflicts": {
                "internal": node.get("emotional_tone", ""),
                "external": node.get("description", ""),
            },
            "emotion_curve": {
                "start": node.get("emotional_tone", "neutral"),
                "middle": "developing",
                "climax": "peak",
                "end": "hook",
            },
            "scene_blueprints": [{
                "scene_num": 1,
                "location": "unknown",
                "characters": node.get("required_characters", []),
                "goal": node.get("description", ""),
                "conflict": node.get("description", ""),
                "emotion": node.get("emotional_tone", ""),
                "word_count_target": _scene_targets[0],
            }],
            "foreshadow_actions": [
                {
                    "foreshadow_id": fs.get("id", ""),
                    "action": fs.get("clue_plan", [""])[0] if fs.get("clue_plan") else "",
                    "intensity": "隐晦提示",
                }
                for fs in relevant_foreshadows
            ],
            "chapter_hook": node.get("description", "") + "...",
            "forbidden_checks": [
                "禁止出现英文词汇",
                "禁止OOC",
                "禁止场景顺序与任务卡不符",
            ],
            "_template": True,
            "_from_template": True,
        }

        try:
            template_file.write_text(
                json.dumps(task_card, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

        logger.info(f"Generated template task card for chapter {chapter_num}")
        return task_card

    def _build_ending_anchor_hint(self, chapter_num: int, foreshadow_registry: dict) -> str:
        """临近伏笔回收节点的终局锚点提示（基于 registry resolve_chapter）。

        返回空串表示本章不在任何伏笔回收窗口内；否则提示 director 在骨架中
        预留呼应，避免长跑中终局线漂移。远期锚点：仅当 0 <= resolve-now <= 30。
        """
        if not foreshadow_registry or not isinstance(foreshadow_registry, dict):
            return ""
        near: list[str] = []
        for fs in foreshadow_registry.get("foreshadows") or []:
            if not isinstance(fs, dict):
                continue
            try:
                rc = int(fs.get("resolve_chapter", 0) or 0)
            except (TypeError, ValueError):
                continue
            if rc <= 0:
                continue
            if 0 <= rc - chapter_num <= 30:
                if str(fs.get("status", "")).strip().lower() == "resolved":
                    continue
                near.append(f"{fs.get('id') or '?'}（预定第{rc}章回收）")
        if not near:
            return ""
        return (
            f"【终局锚点·临近回收】当前第{chapter_num}章，30 章内接近以下伏笔的"
            f"预定回收节点：{'；'.join(near)}。若本章剧情触及这些伏笔，应在骨架中"
            f"预留呼应/推进，确保不越级揭露、不拖过期。"
        )

    def _build_shared_context(self, chapter_num: int, dynamic_context=None):
        """Build context dict and prior_events_block shared across all 3 calls."""
        bible_files = {
            'world': self._bible_cache.get('world', ''),
            'character': self._bible_cache.get('character', ''),
            'style': self._bible_cache.get('style', ''),
            'author_intent': self._bible_cache.get('author_intent', ''),
            'ending': self._bible_cache.get('ending', ''),
        }
        if dynamic_context is not None:
            volumes = dynamic_context.get('volumes', {})
            plot_graph = dynamic_context.get('plot_graph', {})
            foreshadow_registry = dynamic_context.get('foreshadow_registry', {})
        else:
            volumes = self._load_json(self.root / 'config' / 'planning' / 'volumes.json')
            plot_graph = self._load_json(self.root / 'config' / 'planning' / 'plot_graph.json')
            foreshadow_registry = self._load_json(self.root / 'config' / 'foreshadow' / 'registry.json')

        vid = self._get_volume_id(chapter_num, volumes)
        volume_outline = self._outline_sections.get(vid, '')
        author_intent = self._intent_sections.get(vid, '')
        if not author_intent:
            author_intent = bible_files.get('author_intent', '')[:2000]
        relevant_nodes = [
            n for n in plot_graph.get('nodes', [])
            if abs(n.get('chapter_target', 0) - chapter_num) <= 20
        ]
        constraints_summary = self.simulator.get_constraints_summary(chapter_num)
        relevant_foreshadows = [
            fs for fs in foreshadow_registry.get('foreshadows', [])
            if fs.get('plant_chapter', 0) <= chapter_num <= fs.get('resolve_chapter', 0)
        ]
        active_foreshadows = self.db.query_active_foreshadows(chapter_num)
        world_state = self.simulator.build_world_state_for_chapter(chapter_num)
        recent_summaries = self.memory.get_recent_summaries(chapter_num, count=2)
        keyword_history = self.memory.retrieve_by_keywords([f'chapter_{chapter_num}'], limit=3)
        quality_memory = self._load_json(self.root / 'memory' / 'quality_memory.json')
        _policy = load_quality_policy(self.root)
        hard_constraints = []
        for issue in quality_memory.get('overused_elements', []):
            if is_blocking(_policy, issue.get('category', issue.get('severity', ''))):
                hard_constraints.append(
                    f"- 禁止出现{issue.get('type', '重复元素')}类问题：{issue.get('description', '')}"
                )
        dynamic_constraints = '\n'.join(hard_constraints) if hard_constraints else '无新增动态约束'

        # Prior events from previous chapter journal
        previous_summary = '（无前章）'
        _prev_end_anchor = ''
        try:
            prev_journal = self.root / 'chapters' / 'draft' / f'chapter_{chapter_num - 1}_partial.jsonl'
            if prev_journal.exists():
                prev_scenes, last_scene_text = [], ''
                for _line in prev_journal.read_text(encoding='utf-8').splitlines():
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _rec = json.loads(_line)
                    except Exception:
                        continue
                    _text = (_rec.get('scene_text') or '').strip().replace('\n', ' ')
                    if _text:
                        prev_scenes.append(f"场景{_rec.get('scene_id', '?')}：{_text}…")
                    last_scene_text = _text
                if prev_scenes:
                    _sum = '；'.join(prev_scenes)
                    if len(_sum) > 900:
                        _sum = _sum[:900] + '…'
                    previous_summary = _sum
                if last_scene_text:
                    _anchor = last_scene_text.strip()[:120]
                    if _anchor:
                        _prev_end_anchor = '上一章结尾：' + _anchor
        except Exception as _pe:
            logger.warning(f'previous_summary load failed: {_pe}')
        try:
            from novel_engine.pipeline.event_ledger import recent_event_block, end_state_anchor_block
            _ledger_block = recent_event_block(self.root, chapter_num)
            _end_anchor_block = end_state_anchor_block(self.root, chapter_num)
        except Exception:
            _ledger_block, _end_anchor_block = '', ''
        _prior_parts = []
        if _end_anchor_block:
            _prior_parts.append(_end_anchor_block)
        if _ledger_block:
            _prior_parts.append(_ledger_block)
        if previous_summary != '（无前章）':
            _prior_parts.append(
                '## 上一章已写场景（同样禁止换视角重演，只能承接其结果）\n'
                + previous_summary + '\n' + _prev_end_anchor
            )
        prior_events_block = '\n\n'.join(_prior_parts)

        # T3: 解析细纲逐章任务，作为本章硬约束
        _chapter_outline_task = ''
        _adjacent_tasks = ''
        if self._outline_loaded and volume_outline:
            _ch_tasks = parse_chapter_tasks(volume_outline)
            _chapter_outline_task = _ch_tasks.get(chapter_num, '')
            # 相邻章任务（用于防重演/防透支）
            _adj_tasks_parts = []
            for _delta in (-1, 1):
                _adj_ch = chapter_num + _delta
                if _adj_ch in _ch_tasks:
                    _adj_tasks_parts.append(f'第{_adj_ch}章：{_ch_tasks[_adj_ch]}')
            _adjacent_tasks = '; '.join(_adj_tasks_parts)
        # T3: 与 plot_graph 节点协同：里程碑章以 plot_graph 为强约束，非里程碑以细纲为准
        _milestone_node = next((n for n in relevant_nodes if n.get('chapter_target') == chapter_num), None)
        # 中纲（章级意图）层：无节点章优先用 approved 中纲作强约束
        mid_entry = self._load_mid_outline_entry(chapter_num)
        mid_outline_hint = self._build_mid_outline_hint(mid_entry)
        _outline_override_hint = ''
        if _milestone_node and _chapter_outline_task:
            _mg = _milestone_node.get('description', '')
            if _mg and _mg != _chapter_outline_task:
                logger.warning(f'ch{chapter_num}: plot_graph task [{_mg[:40]}] conflicts with outline task [{_chapter_outline_task[:40]}]; outline wins')
                _outline_override_hint = '（大纲细纲优先于plot_graph节点）'

        context = {
            'chapter_num': chapter_num,
            'bible': bible_files,
            'volumes': volumes,
            'relevant_plot_nodes': relevant_nodes,
            'constraints': constraints_summary,
            'foreshadows': relevant_foreshadows,
            'active_foreshadows': active_foreshadows,
            'world_state': world_state,
            'quality_memory': quality_memory,
            'dynamic_constraints': dynamic_constraints,
            'recent_summaries': recent_summaries,
            'keyword_history': keyword_history,
            'volume_outline': volume_outline,
            'author_intent': author_intent,
            # A 路线：当前卷 forbidden 列表（独立提取，避免长文截断切掉禁止项）
            'forbidden_list': self._extract_forbidden_list(author_intent),
            # T3: 细纲逐章任务（最高优先级硬约束）
            'chapter_outline_task': _chapter_outline_task,
            'adjacent_tasks': _adjacent_tasks,
            'outline_override_hint': _outline_override_hint,
            # 设定贯通：终局锚点临近回收提示（bible/ending_bible.md 配套）
            'ending_anchor_hint': self._build_ending_anchor_hint(chapter_num, foreshadow_registry),
            # 中纲（章级意图）层：无节点章的强约束（approved 才注入，无则退回松散参考）
            'mid_outline_entry': mid_entry,
            'mid_outline_hint': mid_outline_hint,
        }
        return context, prior_events_block

    def _check_budget(self, chapter_num: int) -> None:
        """P1: raise if wall-clock budget exceeded."""
        if self._phase_start_ts <= 0:
            return
        elapsed = time.monotonic() - self._phase_start_ts
        if elapsed > DIRECTOR_PHASE_BUDGET_S:
            raise RuntimeError(
                f'director phase wall-clock budget exceeded ({elapsed:.0f}s > {DIRECTOR_PHASE_BUDGET_S}s), chapter={chapter_num}'
            )

    def _detect_connect_wall(self, chapter_num: int, e: Exception) -> None:
        """P1: detect deterministic disconnect wall; raise after threshold."""
        if any(kw in str(e) for kw in ('Server disconnected', 'RemoteProtocolError', 'Connection closed')):
            self._connect_wall_count += 1
            logger.warning(
                f'director connect-wall hit ({self._connect_wall_count}/{_CONNECT_WALL_THRESHOLD}): {str(e)[:100]}'
            )
            if self._connect_wall_count >= _CONNECT_WALL_THRESHOLD:
                raise RuntimeError(
                    f'director connect-wall detected ({self._connect_wall_count} consecutive disconnects); '
                    f'chapter={chapter_num}, switching to reduced-weight path'
                )
        else:
            self._connect_wall_count = 0

    def _merge_task_cards(self, skeleton: list, meta: dict, chapter_num: int) -> dict:
        card = dict(meta)
        card['scene_blueprints'] = skeleton
        return card

    def _call_with_meta(self, prompt: str, max_tokens: int, system_prompt: str = None) -> tuple:
        """Call LLM and return (content_str, finish_reason, usage_dict)."""
        sys_content = system_prompt if system_prompt is not None else self.SYSTEM_PROMPT
        raw = self.llm.chat_completion(
            [{"role": "system", "content": sys_content},
             {"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        # raw is dict with 'content', 'finish_reason', '_usage'
        finish_reason = raw.get('finish_reason', 'unknown')
        usage = raw.get('_usage', {})
        content = raw.get('content', '')
        content = _strip_markdown_json(content)
        try:
            parsed = json.loads(content) if isinstance(content, str) else content
            return parsed, finish_reason, usage
        except (json.JSONDecodeError, TypeError):
            return content, finish_reason, usage

    def _call_scene_skeleton(self, chapter_num: int, context: dict, prior_events_block: str,
                              extra_feedback: list[str] | None = None) -> list:
        """Call 1: generate 4-scene skeleton. max_tokens=1500. Enforces exactly 4 scenes."""
        self._check_budget(chapter_num)
        # Light skeleton: only basic fields to fit 4 scenes within 1500 tokens.
        prompt = (
            f'请为第 {chapter_num} 章生成4个场景的骨架。必须正好4个场景，不得多不得少。\n\n'
            f'## 上下文\n'
            f"- 章节号：{chapter_num}\n"
            f"- 相关剧情节点：{json.dumps(context['relevant_plot_nodes'], ensure_ascii=False, indent=2)[:1200]}\n"
            f"- 本章中纲（章级意图）：{context.get('mid_outline_hint') or '（本范围暂无 approved 中纲条目，按相关节点+卷大纲+细纲自由发挥）'}\n"
            f"- 活跃约束：{context['constraints'][:600]}\n"
            f"- 世界观：{context['bible']['world'][:300]}\n"
            f"- 人物：{context['bible']['character'][:300]}\n"
            f"- 文风：{context['bible']['style'][:200]}\n"
            f"- 本卷大纲：{context.get('volume_outline', '')[:800]}\n"
            f"- 本卷禁止事项：{json.dumps(context.get('forbidden_list') or [], ensure_ascii=False)}\n"
            f"- 终局锚点：{context['bible'].get('ending', '')[:600]}\n"
            f"{context.get('ending_anchor_hint') or ''}\n"
            f"- 动态硬约束：{context.get('dynamic_constraints', '无')}\n"
            f"{prior_events_block}\n\n"
            f"{_render_outline_mandate(context)}\n\n"
            '## 输出（只含 scene_blueprints，每个场景需严格守字数上限）：\n'
            '{\n'
            '  "scene_blueprints": [\n'
            '    {"scene_num": 1, "narrative_time": "故事时间(<6字)", "location": "地点(<10字)",'
            ' "characters": ["人物ID"], "goal": "场景目标(<20字)", "conflict": "场景冲突(<20字)",'
            ' "emotion": "场景情绪(<8字)", "beats": ["情节点1(<14字)","情节点2","情节点3"]},'
            ' ...（必须正好4个场景，scene_num为1,2,3,4，每个字段守上述字数上限，不得展开描写）\n'
            '  ]\n'
            '}'
        )
        feedback_count = 0
        length_reduce_done = False
        while True:
            self._check_budget(chapter_num)  # P1: 每次迭代都查预算
            result, finish_reason, usage = self._call_with_meta(
                prompt, max_tokens=1500, system_prompt=self._SKELETON_SYSTEM)
            if isinstance(result, dict):
                bps = result.get('scene_blueprints', [])
            else:
                try:
                    bps = json.loads(result).get('scene_blueprints', [])
                except Exception:
                    bps = []
            logger.info(
                f'Call 1 (skeleton) ch{chapter_num}: finish_reason={finish_reason}, '
                f'completion_tokens={usage.get("completion_tokens", "?")}, scenes={len(bps)}'
            )
            if len(bps) == 4:
                return bps
            if finish_reason == 'length':
                if length_reduce_done:
                    raise RuntimeError(
                        f'Call 1 ch{chapter_num}: finish=length after reduction, 1500 tokens still truncates. '
                        f'Produced {len(bps)} scenes.'
                    )
                length_reduce_done = True
                logger.warning(f'Call 1 ch{chapter_num}: finish=length, re-sending with lighter prompt')
                prompt = (
                    f'请为第 {chapter_num} 章生成4个场景骨架。每个场景仅 scene_num/location/goal/conflict/emotion/beats。'
                    f'必须正好4个场景 scene_num=1,2,3,4。上下文：{json.dumps(context["relevant_plot_nodes"], ensure_ascii=False)[:600]}。'
                    f'约束：{context["constraints"][:400]}。大纲：{context.get("volume_outline", "")[:400]}。{prior_events_block}'
                    f'{_render_outline_mandate(context)}'
                )
                continue
            if feedback_count >= 1:
                raise RuntimeError(
                    f'Call 1 produced {len(bps)} scenes (expected 4) after feedback retry, '
                    f'finish_reason={finish_reason}. Chapter={chapter_num}'
                )
            feedback_count += 1
            advice = '不足，请补足到4个场景' if len(bps) < 4 else '过多，请合并到4个场景'
            logger.warning(f'Call 1 ch{chapter_num}: {len(bps)} scenes, {advice}, retrying')
            prompt += f'\n\n修正要求：必须正好4个场景，当前{len(bps)}个。{advice}。'

    def _call_scene_craft(self, chapter_num: int, context: dict, prior_events_block: str,
                           skeleton: list, time_fix_instructions: list[str] | None = None) -> list:
        """Call 2: add craft fields to existing scenes. max_tokens=2000. Matches by scene_num.

        time_fix_instructions: 上一版场景叙事时间倒置或超出章级跨度的具体错误
        （detect_task_card_scene_time_inversion / 章内跨日检查的输出），注入 prompt 作为确定性
        修正指令——无差别重发大概率再次触发同类错误，必须给出针对性要求。
        """
        self._check_budget(chapter_num)
        scenes_json = json.dumps(skeleton, ensure_ascii=False, indent=2)
        _time_fix_blk = ""
        if time_fix_instructions:
            _time_fix_blk = (
                "\n## 时间修正要求（必须遵守）\n"
                "上一版场景的 narrative_time 存在时间顺序倒置或超出章级跨度的问题，以下各条必须修正"
                "（可改对应场景的 narrative_time，或调整场景顺序；保持4场不变）：\n"
                + "\n".join(f"- {t}" for t in time_fix_instructions)
                + "\n修正后场景的叙事时间必须随 scene_num 单调推进（回溯/闪回除外），"
                  "且全部落在章级时间跨度内（不得跨日，除非 timeline_anchor 显式允许）。\n"
                + "若某场景在故事时间上早于前序场景且确属回忆/闪回/倒叙，必须在其 "
                  "narrative_time 前加“回忆/先前”等前缀（如“回忆·午后”），以便校验按"
                  "回溯处理；若不属于回溯，必须重排场景顺序使叙事时间随 scene_num 单调"
                  "递增。禁止仅修改 narrative_time 文本而不改变场景顺序关系。\n"
            )
        prompt = (
            f'基于以下场景骨架，补充每个场景的重字段。必须保持场景数量不变。\n\n'
            f'## 场景骨架\n{scenes_json}\n\n'
            f'## 本卷禁止事项（场景内容不得违反）：{json.dumps(context.get("forbidden_list") or [], ensure_ascii=False)}\n'
            f'{self._TEACH_BOUNDARY_HINT}'
            f'{_render_outline_mandate(context)}\n\n'
            f'{_time_fix_blk}\n'
            '## 输出 JSON（只含 scene_blueprints 数组，追加以下字段到每个场景）：\n'
            '{\n'
            '  "scene_blueprints": [\n'
            '    {"scene_num": 1, "concrete_events": [{"event":"谁做了什么(≤12字)","observable_action":"可观察(≤12字)"}],'
            ' "named_interactions": [{"characters":["角色名"],"interaction_type":"争执/对话/冲突","brief":"实质互动(≤20字)"}],'
            ' "info_reveal_points": [{"type":"伏笔埋设","content":"内容(≤12字)","anchor_terms":["名词1","名词2"]}],'
            ' "protagonist_interiority": "内心(≤20字)", "scene_constraints": [],'
            ' "scene_progression_contract": {"new_state_or_entity":["新变化"],"irreversible_change":"不可逆(≤12字)"},"'
            ' "scene_craft_elements": {"info_reversal_point":{"present":false,"content":""}}'
            '\n    }, ...（保持原场景数量不变）\n'
            '  ]\n'
            '}'
        )
        feedback_count = 0
        length_reduce_done = False
        while True:
            self._check_budget(chapter_num)
            result, finish_reason, usage = self._call_with_meta(
                prompt, max_tokens=2000, system_prompt=self._CRAFT_SYSTEM)
            if isinstance(result, dict):
                craft_bps = result.get('scene_blueprints', [])
            else:
                try:
                    craft_bps = json.loads(result).get('scene_blueprints', [])
                except Exception:
                    craft_bps = []
            logger.info(
                f'Call 2 (craft) ch{chapter_num}: finish_reason={finish_reason}, '
                f'completion_tokens={usage.get("completion_tokens", "?")}, scenes={len(craft_bps)}'
            )
            if len(craft_bps) == 4:
                break
            if finish_reason == 'length':
                if length_reduce_done:
                    raise RuntimeError(
                        f'Call 2 ch{chapter_num}: finish=length after reduction, 2000 tokens still truncates. '
                        f'Produced {len(craft_bps)} scenes.'
                    )
                length_reduce_done = True
                logger.warning(f'Call 2 ch{chapter_num}: finish=length, re-sending with tighter prompt')
                # TIGHTEN: concrete_events fixed 2, named_interactions only substantive, empty = []
                tight_prompt = (
                    f'基于以下场景骨架，补充craft字段。必须保持4个场景不变。\n'
                    f'每场骨架：{json.dumps(skeleton, ensure_ascii=False)[:2000]}\n'
                    f'{_render_outline_mandate(context)}\n'
                    f'要求：concrete_events固定2条(每条event≤12字、action≤12字)；'
                    f'named_interactions只保留有实质互动的(无则[])；info_reveal_points 2-3条(content≤12字)；'
                    f'protagonist_interiority≤20字；scene_constraints[]；'
                    f'scene_progression_contract(new_state≤2个变化,irreversible≤12字)；'
                    f'scene_craft_elements固定格式。\n'
                    f'严格照此输出JSON，不得展开。'
                )
                prompt = tight_prompt
                continue
            if feedback_count >= 1:
                raise RuntimeError(
                    f'Call 2 produced {len(craft_bps)} scenes (expected 4) after feedback retry, '
                    f'finish_reason={finish_reason}. Chapter={chapter_num}'
                )
            feedback_count += 1
            advice = '不足，请补足到4个场景' if len(craft_bps) < 4 else '过多，请合并到4个场景'
            logger.warning(f'Call 2 ch{chapter_num}: {len(craft_bps)} scenes, {advice}, retrying')
            prompt += f'\n\n修正要求：必须正好4个场景，当前{len(craft_bps)}个。{advice}。'

        # Match by scene_num, not index
        craft_map = {bp.get('scene_num'): bp for bp in craft_bps if isinstance(bp, dict)}
        updated = []
        for sc in skeleton:
            sn = sc.get('scene_num')
            if sn in craft_map:
                sc.update(craft_map[sn])
            updated.append(sc)
        return updated

    def _call_chapter_metadata(self, chapter_num: int, context: dict, prior_events_block: str,
                                skeleton: list) -> dict:
        """Call 3: generate chapter-level metadata. max_tokens=1200."""
        self._check_budget(chapter_num)
        scenes_preview = json.dumps(
            [{'scene_num': s.get('scene_num'), 'goal': s.get('goal', '')[:60],
              'location': s.get('location', '')[:40]} for s in skeleton],
            ensure_ascii=False, indent=2)
        prompt = (
            f'基于以下{len(skeleton)}个场景，生成章级元数据。\n\n'
            f'## 场景概要\n{scenes_preview}\n\n'
            '## 上下文\n'
            f"- 章节号：{chapter_num}\n"
            f"- 活跃约束：{context['constraints'][:400]}\n"
            f"- 本卷作者意图：{context.get('author_intent', '')[:1000]}\n"
            f"- 本卷禁止事项：{json.dumps(context.get('forbidden_list') or [], ensure_ascii=False)}\n"
            f'{self._TEACH_BOUNDARY_HINT}'
            f'- 动态硬约束：{context.get("dynamic_constraints", "无")}\n'
            f'{prior_events_block}\n\n'
            f'{_render_outline_mandate(context)}\n\n'
            '## 输出 JSON（只含以下字段）：\n'
            '{\n'
            f'  "chapter_num": {chapter_num},\n'
            '  "protagonist_agency_level": "normal|injured|infant_low|imprisoned",\n'
            '  "core_goal": "本章核心目标",\n'
            '  "conflicts": {"internal": "内心冲突", "external": "外部冲突"},\n'
            '  "emotion_curve": {"start": "起始", "middle": "中间", "climax": "高潮", "end": "结尾钩子"},\n'
            '  "chapter_hook": "章末钩子描述",\n'
            '  "foreshadow_actions": [{"foreshadow_id": "F001", "action": "具体指令", "intensity": "隐晦提示/明显异样/接近揭露前兆"}],\n'
            '  "chapter_events": [{"event_type":"类型","one_line_summary":"一句话20-40字","participants":["人物"],"location":"地点","narrative_time":"时间","consequence_state":"结果状态"}],\n'
            '  "state_changes": [{"type":"character_realm","target":"目标ID","new_value":"新值","chapter":' + str(chapter_num) + '}],\n'
            '  "foreshadow_execution": [{"foreshadow_id": "F001", "executed": true, "note": "如何执行"}],\n'
            '  "end_state": {"narrative_position": "最后一帧画面", "location": "末场地点", "completed_actions": ["已演完动作"], "pending_actions": ["下一章接续动作"], "time_marker": "夜/当日"},\n'
            '  "timeline_anchor": {"chapter_start_marker": "开场时间", "max_time_progression": "最大时间跨度", "forbidden_markers": ["禁止越界词"]}\n'
            '}'
        )
        result, finish_reason, usage = self._call_with_meta(prompt, max_tokens=1200, system_prompt=self._METADATA_SYSTEM)
        logger.info(
            f'Call 3 (metadata) ch{chapter_num}: finish_reason={finish_reason}, '
            f'tokens={usage.get("completion_tokens", "?")}'
        )
        if isinstance(result, dict):
            # CC round-33：防御性提取——mock/离线路径可能返回完整任务卡而非纯元数据，
            # 只保留章级字段，剥离场景级别内容。
            return {k: v for k, v in result.items()
                    if k not in ('scene_blueprints', 'synopsis', 'forbidden_checks')}
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                return {k: v for k, v in parsed.items()
                        if k not in ('scene_blueprints', 'synopsis', 'forbidden_checks')}
            return parsed
        except Exception:
            raise RuntimeError(f'Call 3 metadata parse failed, finish_reason={finish_reason}')

    def generate_task_card_cached(self, chapter_num: int, dynamic_context: dict,
                                   feedback: list[str] | None = None) -> dict:
        """
        生成任务卡（使用预计算的固定上下文）。
        dynamic_context 包含 volumes, plot_graph, foreshadow_registry 等不变数据。
        feedback: 上一轮 validate 返回的问题列表，透传至 Call1 skeleton prompt 要求规避。
        """
        # CC round-33：feedback 非空时必须走 LLM 重生成，不得返回模板卡。
        if feedback:
            template_card = None
        else:
            template_card = self._try_template_task_card(chapter_num, dynamic_context)
        if template_card is not None:
            # 模板卡可能只有1个场景（如 validate_task_card 要求最少2个），
            # 校验失败时回退到 LLM 生成。
            template_errors = self.validate_task_card(template_card, chapter_num)
            if not template_errors:
                return template_card
            logger.info(f"Template task card invalid ({template_errors[0]}), falling back to LLM")

        # P0 拆分：用 _build_shared_context 提取上下文，三次小调用合并
        context, prior_events_block = self._build_shared_context(chapter_num, dynamic_context)
        self._phase_start_ts = time.monotonic()
        self._connect_wall_count = 0

        logger.info(f'Director call 1/3 (skeleton) for chapter {chapter_num}')
        skeleton = self._call_scene_skeleton(chapter_num, context, prior_events_block,
                                              extra_feedback=feedback)

        logger.info(f'Director call 2/3 (craft) for chapter {chapter_num}')
        skeleton = self._call_scene_craft(chapter_num, context, prior_events_block, skeleton)

        logger.info(f'Director call 3/3 (metadata) for chapter {chapter_num}')
        meta = self._call_chapter_metadata(chapter_num, context, prior_events_block, skeleton)

        task_card = self._merge_task_cards(skeleton, meta, chapter_num)
        task_card = _normalize_placeholder_characters(task_card)
        # C3 R4：多场任务卡注入 must_cover opening_anchor beats（软生成）
        task_card = _inject_must_cover_opening_anchors(task_card)

        # 最终强校验：缺字段/场数不符时优先针对性重试，再不过则 raise
        strict_errors = self.validate_task_card(task_card, chapter_num, strict=True)
        if strict_errors:
            logger.warning(f'Final validation errors for ch{chapter_num}: {strict_errors[:3]}')
            # 分流补发：章级缺失→重发Call3，场景craft缺失→重发Call2，时间倒置→带修正指令重发Call2
            chapter_missing = [e for e in strict_errors if '缺少必填' in e]
            craft_missing = [e for e in strict_errors if '缺少 craft' in e]
            time_inv = [e for e in strict_errors
                        if ('叙事时间' in e and '早于' in e)
                        or '超出章级跨度' in e or '章内不得跨日' in e]
            retry_source = None
            if craft_missing and len(skeleton) == 4:
                retry_source = 'call2'
                logger.info(f'Re-sending call 2 (craft) for ch{chapter_num}: {craft_missing[:2]}')
                try:
                    skeleton = self._call_scene_craft(chapter_num, context, prior_events_block, skeleton)
                    task_card = self._merge_task_cards(skeleton, meta, chapter_num)
                    task_card = _normalize_placeholder_characters(task_card)
                except Exception as e2:
                    logger.error(f'Retry call 2 failed: {e2}')
                    retry_source = None
            elif chapter_missing and len(skeleton) == 4:
                retry_source = 'call3'
                logger.info(f'Re-sending call 3 (metadata) for ch{chapter_num}: {chapter_missing[:2]}')
                try:
                    meta2 = self._call_chapter_metadata(chapter_num, context, prior_events_block, skeleton)
                    task_card = self._merge_task_cards(skeleton, meta2, chapter_num)
                    task_card = _normalize_placeholder_characters(task_card)
                except Exception as e2:
                    logger.error(f'Retry call 3 failed: {e2}')
                    retry_source = None
            elif time_inv and len(skeleton) == 4:
                # CC round-34：时间倒置是语义错误，无差别重发大概率再次倒置；
                # 必须注入确定性修正指令（具体倒置场景+时序要求）后重发 Call2。
                retry_source = 'call2_timefix'
                logger.info(f'Re-sending call 2 (craft) with time-order fix for ch{chapter_num}: {time_inv[:3]}')
                try:
                    skeleton = self._call_scene_craft(
                        chapter_num, context, prior_events_block, skeleton,
                        time_fix_instructions=time_inv)
                    task_card = self._merge_task_cards(skeleton, meta, chapter_num)
                    task_card = _normalize_placeholder_characters(task_card)
                except Exception as e2:
                    logger.error(f'Retry call 2 (timefix) failed: {e2}')
                    retry_source = None
            if retry_source:
                strict_errors = self.validate_task_card(task_card, chapter_num, strict=True)
            if strict_errors:
                raise RuntimeError(
                    f'Chapter {chapter_num} task card failed strict validation after retry: {strict_errors}'
                )

        # 校验通过后才写模板缓存
        try:
            template_file = self.root / 'cache' / 'task_cards' / f'chapter_{chapter_num}.json'
            template_file.parent.mkdir(parents=True, exist_ok=True)
            template_file.write_text(
                json.dumps(task_card, ensure_ascii=False, indent=2), encoding='utf-8'
            )
        except OSError:
            pass

        logger.info(f'Generated split task card for chapter {chapter_num} ({len(skeleton)} scenes)')
        return task_card


    def generate_task_card(self, chapter_num: int) -> dict:
        """Same 3-call strict pipeline as generate_task_card_cached (non-cached entry)."""
        context, prior_events_block = self._build_shared_context(chapter_num)
        self._phase_start_ts = time.monotonic()
        self._connect_wall_count = 0

        logger.info(f'Director call 1/3 (skeleton) for chapter {chapter_num}')
        skeleton = self._call_scene_skeleton(chapter_num, context, prior_events_block)

        logger.info(f'Director call 2/3 (craft) for chapter {chapter_num}')
        skeleton = self._call_scene_craft(chapter_num, context, prior_events_block, skeleton)

        logger.info(f'Director call 3/3 (metadata) for chapter {chapter_num}')
        meta = self._call_chapter_metadata(chapter_num, context, prior_events_block, skeleton)

        task_card = self._merge_task_cards(skeleton, meta, chapter_num)
        task_card = _normalize_placeholder_characters(task_card)
        # C3 R4：多场任务卡注入 must_cover opening_anchor beats（软生成）
        task_card = _inject_must_cover_opening_anchors(task_card)

        # 与 cached 路径一致：strict 终检 + 分流补发 + 通过才缓存
        strict_errors = self.validate_task_card(task_card, chapter_num, strict=True)
        if strict_errors:
            logger.warning(f'Final validation errors for ch{chapter_num}: {strict_errors[:3]}')
            chapter_missing = [e for e in strict_errors if '缺少必填' in e]
            craft_missing = [e for e in strict_errors if '缺少 craft' in e]
            time_inv = [e for e in strict_errors
                        if ('叙事时间' in e and '早于' in e)
                        or '超出章级跨度' in e or '章内不得跨日' in e]
            retry_source = None
            if craft_missing and len(skeleton) == 4:
                retry_source = 'call2'
                logger.info(f'Re-sending call 2 (craft) for ch{chapter_num}')
                try:
                    skeleton = self._call_scene_craft(chapter_num, context, prior_events_block, skeleton)
                    task_card = self._merge_task_cards(skeleton, meta, chapter_num)
                    task_card = _normalize_placeholder_characters(task_card)
                except Exception as e2:
                    logger.error(f'Retry call 2 failed: {e2}')
                    retry_source = None
            elif chapter_missing and len(skeleton) == 4:
                retry_source = 'call3'
                logger.info(f'Re-sending call 3 (metadata) for ch{chapter_num}')
                try:
                    meta2 = self._call_chapter_metadata(chapter_num, context, prior_events_block, skeleton)
                    task_card = self._merge_task_cards(skeleton, meta2, chapter_num)
                    task_card = _normalize_placeholder_characters(task_card)
                except Exception as e2:
                    logger.error(f'Retry call 3 failed: {e2}')
                    retry_source = None
            elif time_inv and len(skeleton) == 4:
                retry_source = 'call2_timefix'
                logger.info(f'Re-sending call 2 (craft) with time-order fix for ch{chapter_num}: {time_inv[:3]}')
                try:
                    skeleton = self._call_scene_craft(
                        chapter_num, context, prior_events_block, skeleton,
                        time_fix_instructions=time_inv)
                    task_card = self._merge_task_cards(skeleton, meta, chapter_num)
                    task_card = _normalize_placeholder_characters(task_card)
                except Exception as e2:
                    logger.error(f'Retry call 2 (timefix) failed: {e2}')
                    retry_source = None
            if retry_source:
                strict_errors = self.validate_task_card(task_card, chapter_num, strict=True)
            if strict_errors:
                raise RuntimeError(
                    f'Chapter {chapter_num} task card failed strict validation after retry: {strict_errors}'
                )

        # 校验通过后才写模板缓存
        try:
            template_file = self.root / 'cache' / 'task_cards' / f'chapter_{chapter_num}.json'
            template_file.parent.mkdir(parents=True, exist_ok=True)
            template_file.write_text(
                json.dumps(task_card, ensure_ascii=False, indent=2), encoding='utf-8'
            )
        except OSError:
            pass

        logger.info(f'Generated split task card for chapter {chapter_num} ({len(skeleton)} scenes)')
        return task_card


    def validate_task_card(self, task_card: dict, chapter_num: int, strict: bool = False) -> list[str]:
        """验证任务卡的完整性。strict=True 时强制4场景+必查章级字段。"""
        errors = []

        required_fields = ["core_goal", "conflicts", "emotion_curve", "scene_blueprints", "chapter_hook"]
        if strict:
            required_fields.extend(["timeline_anchor", "end_state", "chapter_events"])
        for field in required_fields:
            if field not in task_card:
                errors.append(f"缺少必填字段: {field}")

        scenes = task_card.get("scene_blueprints", [])
        # 生产路径强制4场（冲突密集可5）；非strict允许2-6
        if strict:
            if len(scenes) != 4:
                errors.append(f"场景数量必须为4，当前 {len(scenes)} 个")
        else:
            if len(scenes) < 2:
                errors.append(f"场景数量不足（最少2个），当前 {len(scenes)} 个")
            elif len(scenes) > 6:
                errors.append(f"场景数量过多（最多6个），当前 {len(scenes)} 个")

        # 检查场景编号是否连续
        scene_nums = [s.get("scene_num") for s in scenes]
        expected_nums = list(range(1, len(scenes) + 1))
        if scene_nums != expected_nums:
            errors.append(f"场景编号不连续: {scene_nums}，期望 {expected_nums}")

        # CC round-30：章内场景叙事时间顺序倒置检测（ch141 场景1午后→2上午/傍晚→3午后→4黄昏）
        errors.extend(detect_task_card_scene_time_inversion(scenes))

        # CC round-30/31：章内跨日检查。放宽规则（ch151 双模型复现后修正）：
        #   - span 自身允许跨日（如"约24小时/深夜至次日黄昏"）→ 全放行；
        #   - 首个带时间场景即跨日 → 放行（span 起点语义模糊，导演可能从次日清晨起章）；
        #   - 前序场景为深夜类(order>=6) 后跨日 → 放行（"深夜→次日清晨"隐含跨日推进）；
        #   - 其余（当日白昼场景之后突现跨日，如 ch141 场景4"次日晨"）→ 拦。
        span = str((task_card.get("timeline_anchor") or {}).get("max_time_progression", "") or "")
        span_allows_overnight = bool(span and re.search(r"(次日|翌日|跨日|24小时|一天一夜|一夜|深夜)", span))
        if span and "年" not in span and not span_allows_overnight:
            ordered = sorted(
                (s for s in scenes if isinstance(s, dict)),
                key=lambda s: int(s.get("sequence_index", s.get("scene_num", 0)) or 0),
            )
            last_order = 0
            for s in ordered:
                nt = str(s.get("narrative_time", "") or "").strip()
                if not nt:
                    continue
                if any(b in nt for b in _SCENE_TIME_BACKREF):
                    last_order = 0
                    continue
                if re.search(r"(次日|翌日|来日|第[一二两三四五六七八九十\d]+[天日]|三[天日]后|五[天日]后)", nt):
                    if last_order and last_order < 6:
                        errors.append(
                            f"场景{int(s.get('scene_num', 0))} 叙事时间「{nt}」超出章级跨度「{span}」（章内不得跨日）"
                        )
                o = _base_time_order(nt)
                if o:
                    last_order = o

        # 检查 forbidden 项
        author_intent = self._bible_cache.get("author_intent", "")
        current_intent_section = ""
        for section in author_intent.split("## "):
            if f"第{chapter_num}章" in section or (
                "chapter_range" in section and
                any(str(chapter_num) in line for line in section.split("\n")[:5])
            ):
                current_intent_section = section
                break

        forbidden_items = re.findall(r"- \"([^\"]+)\"", current_intent_section)
        core_goal = task_card.get("core_goal", "")
        for item in forbidden_items:
            if item in core_goal:
                errors.append(f"core_goal 包含 forbidden 项: {item}")

        # 检查场景字段完整性 + craft 字段（strict 模式）
        craft_fields = ["concrete_events", "named_interactions", "info_reveal_points",
                        "protagonist_interiority", "scene_constraints",
                        "scene_progression_contract", "scene_craft_elements"]
        for i, scene in enumerate(scenes):
            scene_required = ["scene_num", "location", "characters", "goal", "conflict", "emotion"]
            for field in scene_required:
                if field not in scene:
                    errors.append(f"场景 {i+1} 缺少字段: {field}")
            if strict:
                for cf in craft_fields:
                    if cf not in scene:
                        errors.append(f"场景 {i+1} 缺少 craft 字段: {cf}")

        # 检查伏笔动作的章节范围
        foreshadow_actions = task_card.get("foreshadow_actions", [])
        for fa in foreshadow_actions:
            fs_id = fa.get("foreshadow_id", "")
            if not fs_id:
                errors.append("伏笔动作缺少 foreshadow_id")

        return errors
