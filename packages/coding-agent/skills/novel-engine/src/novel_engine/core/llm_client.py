"""
LLM 客户端：封装 OpenAI 兼容接口的大模型调用（默认 SiliconFlow / Qwen）。
支持真实 API 和离线 Mock 两种模式。
"""
import json
import math
import random
import re
import threading
import time
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from novel_engine.core.llm_provider import ProviderConfig

import httpx

from .rate_limiter import RateLimitError
from .errors import TransientLLMError, PermanentLLMError, ConfigFatalError
from .call_metrics import record_call

logger = logging.getLogger(__name__)

# Module-level call log for cost tracking (single source of truth).
# Guarded by _CALL_LOG_LOCK since scene generation is multi-threaded.
_call_log: list[dict] = []
_CALL_LOG_LOCK = threading.Lock()

DEFAULT_API_BASE = "https://api.siliconflow.cn"
DEFAULT_MODEL = "Qwen/Qwen3.5-4B"

# CC round-11 Q1：HTTP 200 但有效内容为空/退化是“可重试瞬时退化”，在最靠近网络的
# LLMClient 层做跨温度即时重试（不退避，非限流），与 429/5xx/超时的异常重试分离。
# “完全不输出”多为过早停止采样路径，升温比降温更能绕开，故序列升温而非降温。
DEGEN_RETRY_TEMPERATURES = (0.85, 1.0)
DEGEN_MIN_CJK = 20
DEGEN_EXHAUSTED_FLAG = "_degenerate_exhausted"


def count_cjk_chars(text: str) -> int:
    return sum(1 for ch in (text or "") if ord(ch) > 0x2E7F)


# 短正文退化只对场景写作类 phase 判定：reviewer/director/polish 等阶段的合法输出本就可很短，
# 不能因 CJK<20 误重试；scene 阶段任何合理正文都远超该下限。
SCENE_PHASE_MARKERS = ("scene", "write", "writer")


def classify_content(raw_content: str, reasoning_content: str = "", phase: str = "") -> str:
    """把一次 200 响应按“可用正文”分类。VALID 之外均为可重试瞬时退化。

    - HARD_EMPTY：正文空且无思考输出（真空），全 phase 可重试。
    - REASONING_ONLY：只吐思考、无正文，等同空处理，全 phase 可重试。
    - DEGENERATE_SHORT：有字符但 CJK 远低于场景正文下限；仅场景写作 phase 判退化，
      其余短输出（评分/JSON/缩写）按 VALID 返回，避免误伤与拖慢非写作阶段。
    - MALFORMED_JSON 属解析层判定（writer 负责），本层不看 JSON 结构。
    """
    stripped = (raw_content or "").strip()
    reasoning = (reasoning_content or "").strip()
    if not stripped and not reasoning:
        return "HARD_EMPTY"
    if not stripped and reasoning:
        return "REASONING_ONLY"
    is_scene_phase = any(m in str(phase or "").lower() for m in SCENE_PHASE_MARKERS)
    if is_scene_phase and count_cjk_chars(stripped) < DEGEN_MIN_CJK:
        return "DEGENERATE_SHORT"
    return "VALID"

@dataclass(frozen=True)
class TimeoutSpec:
    """不可变超时配置：流式/空闲/总超时 + 连接/写/池各分段。"""
    stream: bool = True
    idle_s: int = 100
    total_s: int = 650
    connect_s: int = 15
    write_s: int = 30
    pool_s: int = 30

    def effective_total(self, max_tokens: Optional[int]) -> int:
        """返回实际总超时：min(phase_total_s, ceil(max_tokens/3) + 60)。"""
        cap = self.total_s
        if max_tokens is not None and max_tokens > 0:
            cap = min(cap, math.ceil(max_tokens / 3) + 60)
        return int(cap)



class MockLLMClient:
    """离线 Mock LLM：返回确定性模板内容，用于测试流水线。"""

    def __init__(self):
        self.call_count = 0

    @staticmethod
    def _extract_chapter_num(messages: list[dict]) -> int:
        """从消息中提取章节号。"""
        import re
        combined = "\n".join(m.get("content", "") for m in messages)
        match = re.search(r'第\s*(\d+)\s*章', combined)
        if match:
            return int(match.group(1))
        return 0

    def chat_completion(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retry_on_error: bool = True,
        max_retries: int = 3,
        extra_body: Optional[dict] = None,
        timeout_spec: Optional[TimeoutSpec] = None,
    ) -> dict:
        self.call_count += 1
        user_content = messages[-1]["content"] if messages else ""

        # Check ALL message content, not just user_content, since system_prompt may carry the signal
        combined = "\n".join(m.get("content", "") for m in messages)

        # Extract chapter number from prompt
        chapter_num = self._extract_chapter_num(messages)

        # Review scoring has the most specific signature — check first
        if "审查要求" in combined or ("评分" in combined and "维度" in combined):
            return {"role": "assistant", "content": _mock_review(self.call_count)}
        # Match explicit instruction phrases in the USER prompt only. System prompts and
        # embedded context (task cards, volume outline) legitimately contain "任务卡"/
        # "scene_blueprints"/"缩写"/"正文", so only the driving instruction is matched.
        # Task card generation (old single-call + new 3-call split director)
        if "生成任务卡" in user_content or "生成4个场景的骨架" in user_content \
                or "补充每个场景的重字段" in user_content or "生成章级元数据" in user_content:
            return {"role": "assistant", "content": _mock_full_task_card(chapter_num)}
        # Synopsis generation
        if "生成剧情缩写" in user_content or "生成缩写" in user_content or "state_changes" in user_content:
            return {"role": "assistant", "content": _mock_synopsis(chapter_num)}
        # Polish — no-op in mock: return the input chapter text unchanged
        if "润色" in user_content:
            marker = "## 待润色文本"
            idx = user_content.find(marker)
            if idx != -1:
                return {"role": "assistant", "content": user_content[idx + len(marker):].strip()}
            return {"role": "assistant", "content": _mock_novel_scene(chapter_num)}
        # Novel writing
        if "正文" in user_content:
            # 目标字数来自场景蓝图，mock 按其扩充正文，使字数分析真实可用
            import re as _re
            _m = _re.search(r"目标字数[:：]\s*(\d+)", user_content)
            target = int(_m.group(1)) if _m else 800
            _sm = (_re.search(r"第\s*(\d+)\s*场景", user_content)
                   or _re.search(r"场景\s*(\d+)", user_content)
                   or _re.search(r"\"scene_num\"\s*:\s*(\d+)", user_content))
            scene_num = int(_sm.group(1)) if _sm else 0
            return {"role": "assistant", "content": _mock_novel_scene(chapter_num, target, scene_num)}
        else:
            return {"role": "assistant", "content": "这是 Mock LLM 的默认回复。"}


def _mock_task_card(seed: int) -> str:
    return json.dumps({
        "chapter_num": seed,
        "core_goal": f"第{seed}章核心目标：推进主线剧情",
        "conflicts": {
            "internal": "主角内心迷茫，不知未来道路",
            "external": "外部强敌环伺，生存压力巨大"
        },
        "emotion_curve": {
            "start": "压抑",
            "middle": "冲突",
            "climax": "高潮",
            "end": "余韵"
        },
        "scene_blueprints": [
            {
                "scene_num": 1,
                "location": "雾隐村",
                "characters": ["陆烬", "陈老根"],
                "goal": "展现荒村的诡异和生活细节",
                "conflict": "陈老根行为反常",
                "emotion": "疑惑",
                "word_count_target": 1000,
                "beats": ["晨雾里陆烬巡视村巷，记下几处反常的死寂", "陈老根对古玉的来历语焉不详，神色躲闪", "陆烬暗下决心夜里去荒坡一探究竟"],
            },
            {
                "scene_num": 2,
                "location": "昆仑禁区外围",
                "characters": ["陆烬"],
                "goal": "主角探索禁区并吸收暴走活化氧",
                "conflict": "禁区凶险",
                "emotion": "紧张",
                "word_count_target": 1200,
                "beats": ["陆烬潜入昆仑禁区外围，察觉活化氧暴走", "他冒险引动异变汲取一缕活化氧入体", "禁区深处传来低吼，他被迫收功撤离"],
            },
            {
                "scene_num": 3,
                "location": "铁风武馆",
                "characters": ["陆烬", "馆主"],
                "goal": "拜师学艺，初窥武道门径",
                "conflict": "武馆弟子的排挤",
                "emotion": "不屈",
                "word_count_target": 1500,
                "beats": ["陆烬到铁风武馆投帖拜师，遭弟子嘲弄", "他当众硬接三招不跪，馆主破例收他", "排挤他的大弟子暗放狠话埋下冲突"],
            },
            {
                "scene_num": 4,
                "location": "藏经阁",
                "characters": ["陆烬"],
                "goal": "在藏经阁找到古玉相关记载",
                "conflict": "禁地守卫森严",
                "emotion": "谨慎求索",
                "word_count_target": 1300,
                "beats": ["陆烬夜探藏经阁", "发现古玉图录残页", "被巡逻守卫察觉后躲藏撤离"],
            }
        ],
        "foreshadow_actions": [
            {
                "foreshadow_id": "F001",
                "action": "主角抚摸古玉，古玉发出微弱的红光",
                "intensity": "隐晦提示"
            }
        ],
        "chapter_hook": "远处天空划过一道璀璨的剑光，预示着风暴将至。",
        "forbidden_checks": [
            "确认未违反 author_intent 中的 forbidden 项"
        ]
    }, ensure_ascii=False)


def _mock_full_task_card(seed: int) -> str:
    """Complete task card for mock director (3-call split): adds chapter-level metadata
    and per-scene craft fields required by strict validation."""
    card = json.loads(_mock_task_card(seed))
    # Chapter-level metadata (Call3)
    card.update({
        "timeline_anchor": {
            "chapter_start_marker": "清晨",
            "max_time_progression": "当日",
            "forbidden_markers": ["跨年", "转场"],
        },
        "end_state": {
            "narrative_position": "陆烬握紧古玉，望向远山",
            "location": "藏经阁外",
            "completed_actions": ["夜探藏经阁", "发现古玉图录残页"],
            "pending_actions": ["明日继续调查古玉来历"],
            "time_marker": "夜",
        },
        "chapter_events": [
            {
                "event_type": "探索",
                "one_line_summary": "陆烬夜探藏经阁发现古玉相关记载",
                "participants": ["陆烬"],
                "location": "藏经阁",
                "narrative_time": "夜晚",
                "consequence_state": "获得关键线索",
            }
        ],
    })
    # Per-scene craft fields (Call2)
    craft_defaults = {
        "concrete_events": [{"event": "动作发生", "observable_action": "可见行为"}],
        "named_interactions": [],
        "info_reveal_points": [{"type": "伏笔埋设", "content": "隐约暗示", "anchor_terms": ["线索"]}],
        "protagonist_interiority": "内心独白",
        "scene_constraints": [],
        "scene_progression_contract": {
            "new_state_or_entity": ["状态变化"],
            "irreversible_change": "不可逆转",
        },
        "scene_craft_elements": {"info_reversal_point": {"present": False, "content": ""}},
    }
    for bp in card.get("scene_blueprints", []):
        bp.update(craft_defaults)
    return json.dumps(card, ensure_ascii=False)


def _mock_synopsis(seed: int) -> str:
    return json.dumps({
        "chapter_num": seed,
        "synopsis": f"第{seed}章缩写：韩玄在宗门中继续成长，经历考验与挑战，逐步揭开身世之谜。",
        "state_changes": [
            {"type": "character_realm", "target": "C001", "new_value": "炼气四层", "chapter": seed},
            {"type": "relationship_update", "target": "R002", "new_value": "好感度提升", "chapter": seed}
        ],
        "foreshadow_execution": [
            {
                "foreshadow_id": "F001",
                "executed": True,
                "note": "陆烬抚摸古玉并感受到其异样"
            }
        ]
    }, ensure_ascii=False)


_CN_DIGITS = "零一二三四五六七八九"


def _cn_num(n: int) -> str:
    if n <= 10:
        return ("十" if n == 10 else _CN_DIGITS[n])
    if n < 20:
        return "十" + _CN_DIGITS[n - 10]
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


# ≥20 的动作短语：单章正文句数不超过 ~18，故每条在同一正文里至多出现一次，
# 从根本上避免任何 8 字片段因周期复现而触发 high_freq_repeat。
_MOCK_VERBS = [
    "屏息贴住冰凉石壁", "拨开会沾衣的乱草", "侧身让出半尺空隙", "抬眼掠过参差檐角",
    "侧耳辨清远处更点", "抬手压住袖底古玉", "放轻踏过松动青砖", "记住那道异常气息",
    "隐入断墙投下的影", "绕开积着浅水的坑", "借竹影遮住半张脸", "把退路在心底排过",
    "嗅到一线铁锈气味", "辨出风里夹带的腥", "等巡灯人缓步走远", "指尖试过石面潮气",
    "看清窗纸后人数目", "把呼吸压得极细极缓", "记下屋脊异常摆向", "沿阴影再挪半步",
    "收住几乎出口的声", "以余光锁住那道门", "稳住微颤的手腕", "估算离天亮的时辰",
]
_MOCK_OBJECTS = [
    "半枚残铃", "一线微光", "几片湿苔", "一截断香", "几粒浮尘", "远处灯火",
    "檐角铜环", "墙根碎瓦", "袖中符纸", "阶前积水", "天边寒星", "巷口薄雾",
]
_MOCK_ENDS = ["。", "，神色不变。", "，未肯大意。"]
_MOCK_OPENS = ["夜过{cn}，他在{pl}{vb}，唯见{ob}", "至{cn}，{pl}一侧他{vb}，但觉{ob}", "{cn}时分，临{pl}他{vb}，偶得{ob}"]
_MOCK_PLACES = ["东廊", "西岭", "南渡", "北坳", "前庭", "后巷", "山隘", "水栅", "碑林", "栈桥", "废井", "孤亭"]


_CN_DIGITS = "零一二三四五六七八九"


def _cn_num(n: int) -> str:
    if n <= 10:
        return ("十" if n == 10 else _CN_DIGITS[n])
    if n < 20:
        return "十" + _CN_DIGITS[n - 10]
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


# ≥20 的动作短语：单章正文句数不超过 ~18，故每条在同一正文里至多出现一次，
# 从根本上避免任何 8 字片段因周期复现而触发 high_freq_repeat。
_MOCK_VERBS = [
    "屏息贴住冰凉石壁", "拨开会沾衣的乱草", "侧身让出半尺空隙", "抬眼掠过参差檐角",
    "侧耳辨清远处更点", "抬手压住袖底古玉", "放轻踏过松动青砖", "记住那道异常气息",
    "隐入断墙投下的影", "绕开积着浅水的坑", "借竹影遮住半张脸", "把退路在心底排过",
    "嗅到一线铁锈气味", "辨出风里夹带的腥", "等巡灯人缓步走远", "指尖试过石面潮气",
    "看清窗纸后人数目", "把呼吸压得极细极缓", "记下屋脊异常摆向", "沿阴影再挪半步",
    "收住几乎出口的声", "以余光锁住那道门", "稳住微颤的手腕", "估算离天亮的时辰",
]
_MOCK_OBJECTS = [
    "半枚残铃", "一线微光", "几片湿苔", "一截断香", "几粒浮尘", "远处灯火",
    "檐角铜环", "墙根碎瓦", "袖中符纸", "阶前积水", "天边寒星", "巷口薄雾",
    "石上裂痕", "梁间蛛网", "门后旧戟", "井沿冰纹", "案头残烛", "篱外荒榛",
    "塔尖残影", "渡头朽索", "碑脚青苔", "瓦当冷露", "林间惊羽", "匣底暗纹",
]
_MOCK_ENDS = ["。", "，神色不变。", "，未肯大意。"]
_MOCK_OPENS = ["夜过{cn}，他在{pl}{vb}，唯见{ob}", "至{cn}，{pl}一侧他{vb}，但觉{ob}", "{cn}时分，临{pl}他{vb}，偶得{ob}"]
_MOCK_PLACES = ["东廊", "西岭", "南渡", "北坳", "前庭", "后巷", "山隘", "水栅", "碑林", "栈桥", "废井", "孤亭",
                "曲径", "断崖", "沙咀", "松门", "竹坞", "芦湾", "土城", "石台"]


_CN_DIGITS = "零一二三四五六七八九"


def _cn_num(n: int) -> str:
    if n <= 10:
        return ("十" if n == 10 else _CN_DIGITS[n])
    if n < 20:
        return "十" + _CN_DIGITS[n - 10]
    if n < 100:
        return _CN_DIGITS[n // 10] + "十" + (_CN_DIGITS[n % 10] if n % 10 else "")
    return str(n)


# ≥20 的动作短语：单章正文句数不超过 ~18，故每条在同一正文里至多出现一次，
# 从根本上避免任何 8 字片段因周期复现而触发 high_freq_repeat。
_MOCK_VERBS = [
    "屏息贴住冰凉石壁", "拨开会沾衣的乱草", "侧身让出半尺空隙", "抬眼掠过参差檐角",
    "侧耳辨清远处更点", "抬手压住袖底古玉", "放轻踏过松动青砖", "记住那道异常气息",
    "隐入断墙投下的影", "绕开积着浅水的坑", "借竹影遮住半张脸", "把退路在心底排过",
    "嗅到一线铁锈气味", "辨出风里夹带的腥", "等巡灯人缓步走远", "指尖试过石面潮气",
    "看清窗纸后人数目", "把呼吸压得极细极缓", "记下屋脊异常摆向", "沿阴影再挪半步",
    "收住几乎出口的声", "以余光锁住那道门", "稳住微颤的手腕", "估算离天亮的时辰",
]
_MOCK_OBJECTS = [
    "半枚残铃", "一线微光", "几片湿苔", "一截断香", "几粒浮尘", "远处灯火",
    "檐角铜环", "墙根碎瓦", "袖中符纸", "阶前积水", "天边寒星", "巷口薄雾",
    "石上裂痕", "梁间蛛网", "门后旧戟", "井沿冰纹", "案头残烛", "篱外荒榛",
    "塔尖残影", "渡头朽索", "碑脚青苔", "瓦当冷露", "林间惊羽", "匣底暗纹",
]
_MOCK_ENDS = [
    "。", "，目光沉了沉。", "，脚下未停。", "，心里有了数。", "，只作不见。",
    "，气息更稳。", "，不退反进。", "，把话咽回。", "，偏头避开。", "，指尖微收。",
    "，静待下文。", "，不露惊色。", "，暗记方位。", "，又近一步。", "，寒意略减。",
    "，耳侧微动。", "，守着分寸。", "，把杀意藏好。", "，继续向前。", "，眼底清明。",
]
_MOCK_OPENS = ["夜过{cn}，他在{pl}{vb}，唯见{ob}", "至{cn}，{pl}一侧他{vb}，但觉{ob}", "{cn}时分，临{pl}他{vb}，偶得{ob}"]
_MOCK_PLACES = ["东廊", "西岭", "南渡", "北坳", "前庭", "后巷", "山隘", "水栅", "碑林", "栈桥", "废井", "孤亭",
                "曲径", "断崖", "沙咀", "松门", "竹坞", "芦湾", "土城", "石台"]


def _uniq_cjk_fill(target: int, offset: int = 0, scene: int = 0) -> str:
    """确定性、跨场景不重叠且无高复现的中文正文。

    每句以唯一中文序号起头（任何跨句 8 字滑窗都含不同序号），动作短语池≥句数，
    每条动作在一段正文里至多出现一次；地名/名物/句式多槽位组合。
    """
    out: list[str] = []
    k = 0
    while sum(len(x) for x in out) < target:
        idx = k + scene * 7 + 1
        cn = _cn_num(((k + scene * 3) % 60) + 1)
        op = _MOCK_OPENS[(k + scene) % len(_MOCK_OPENS)]
        pl = _MOCK_PLACES[(k * 2 + scene * 5) % len(_MOCK_PLACES)]
        vb = _MOCK_VERBS[(k + scene * 13) % len(_MOCK_VERBS)]
        ob = _MOCK_OBJECTS[(k * 3 + scene * 7) % len(_MOCK_OBJECTS)]
        end = _MOCK_ENDS[k % len(_MOCK_ENDS)]
        out.append("第" + _cn_num(idx) + "程，" + op.format(cn=cn, pl=pl, vb=vb, ob=ob) + end)
        k += 1
        if k > 400:
            break
    return "".join(out)


def _mock_novel_scene(seed: int, target: int = 800, scene_num: int = 0) -> str:
    """确定性结构化场景：合法 JSON，正文长度达标、低重复、句末完整；beats 足量覆盖。"""
    opening = (
        f"第{seed}章第{scene_num or 1}场。夜色如墨，浓雾自荒原尽头漫卷而来。"
        if scene_num else
        f"第{seed}章。夜色如墨，浓雾自荒原尽头漫卷而来，韩玄立在断崖边，目光穿过层层迷雾。"
    )
    body = _uniq_cjk_fill(max(0, int(target) - len(opening)), scene=int(scene_num or 0))
    scene_text = (opening + body)[: max(len(opening) + 20, int(target))]
    if scene_text[-1] not in "。！？":
        scene_text = scene_text.rstrip("，、；：") + "。"
    covered = [str(i) for i in range(6)]
    return json.dumps({
        "scene_id": int(scene_num or 0),
        "scene_text": scene_text,
        "hook": "暗处一道目光骤然投来，危机未散。",
        "beats": covered,
        "beats_covered": covered,
    }, ensure_ascii=False)

def _mock_review(seed: int) -> str:
    base_score = min(85 + (seed % 10), 96)
    return json.dumps({
        "chapter_num": seed,
        "scores": {
            "plot_consistency": 23,
            "character_consistency": 18,
            "foreshadow_execution": 17,
            "style_match": 13,
            "pacing": 9,
            "innovation": 9
        },
        "total_score": base_score,
        "verdict": "pass" if base_score >= 85 else "fix",
        "issues": [],
        "praise": "整体质量良好，情节连贯，人物塑造生动。",
        "fix_scope": ""
    }, ensure_ascii=False)


class LLMClient:
    """OpenAI-compatible LLM 客户端。"""

    def __init__(
        self,
        api_base: str = DEFAULT_API_BASE,
        model: str = DEFAULT_MODEL,
        api_key: Optional[str] = None,
        temperature: float = 0.85,
        max_tokens: int = 4096,
        timeout: int = 120,
        use_mock: bool = False,
    ):
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = api_key or ""
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.use_mock = use_mock
        # httpx.Client is not thread-safe for concurrent requests; give each
        # thread its own client via threading.local.
        self._local = threading.local()
        self._mock = MockLLMClient() if use_mock else None
        # CC30：可选退化事件观察者；observer(deg_cls) 在每次收到退化 200
        # （REASONING_ONLY/DEGENERATE_SHORT）跨温度重试前触发，供上层退化窗口计数。
        self.degenerate_observer = None

    def _fire_degenerate(self, deg_cls: str):
        ob = getattr(self, "degenerate_observer", None)
        if ob is None:
            return
        try:
            ob(deg_cls)
        except Exception:
            pass

    @staticmethod
    def _resolve_api_key(api_key_env: str, section_cfg: dict) -> str:
        import os
        from pathlib import Path
        key = (os.environ.get(api_key_env)
               or os.environ.get("ZLEAP_API_KEY")
               or section_cfg.get("api_key") or "")
        if not key or "redacted" in str(key).lower():
            _env_path = Path(__file__).parent.parent.parent / ".env"
            if _env_path.exists():
                for _line in _env_path.read_text(encoding="utf-8").splitlines():
                    if _line.startswith(api_key_env + "="):
                        key = _line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        return key

    @classmethod
    def from_config(cls, config_path: str | Path, section: str = "llm",
                    api_key_env: str = "ZLEAP_MODEL_API_KEY") -> "LLMClient":
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"配置文件不存在：{path}，使用默认配置")
            return cls()
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        llm_cfg = cfg.get(section, {})
        api_key = cls._resolve_api_key(api_key_env, llm_cfg)
        return cls(
            api_base=llm_cfg.get("api_base", DEFAULT_API_BASE),
            model=llm_cfg.get("model", DEFAULT_MODEL),
            api_key=api_key,
            temperature=llm_cfg.get("temperature", 0.85),
            max_tokens=llm_cfg.get("max_tokens", 4096),
            timeout=llm_cfg.get("timeout_seconds", 120),
            use_mock=llm_cfg.get("use_mock", False),
        )

    @classmethod
    def from_config_dict(cls, cfg: dict, section: str = "llm",
                        api_key_env: str = "ZLEAP_MODEL_API_KEY") -> "LLMClient":
        llm_cfg = cfg.get(section, {})
        api_key = cls._resolve_api_key(api_key_env, llm_cfg)
        return cls(
            api_base=llm_cfg.get("api_base", DEFAULT_API_BASE),
            model=llm_cfg.get("model", DEFAULT_MODEL),
            api_key=api_key,
            temperature=llm_cfg.get("temperature", 0.85),
            max_tokens=llm_cfg.get("max_tokens", 4096),
            timeout=llm_cfg.get("timeout_seconds", 120),
            use_mock=llm_cfg.get("use_mock", False),
        )

    def _get_client(self, timeout: Optional[int] = None) -> httpx.Client:
        # 若指定了覆盖超时，则为该次调用创建临时 client，不缓存
        if timeout is not None and timeout != self.timeout:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            return httpx.Client(
                base_url=self.api_base,
                headers=headers,
                timeout=timeout,
            )
        client = getattr(self._local, "client", None)
        if client is None:
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            client = httpx.Client(
                base_url=self.api_base,
                headers=headers,
                timeout=self.timeout,
            )
            self._local.client = client
        return client

    def chat_completion(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        retry_on_error: bool = True,
        max_retries: int = 3,
        extra_body: Optional[dict] = None,
        timeout: Optional[int] = None,
        timeout_spec: Optional[TimeoutSpec] = None,
    ) -> dict:
        if self.use_mock or self._mock:
            return self._mock.chat_completion(messages, temperature, max_tokens, retry_on_error, max_retries, extra_body, timeout_spec)

        base_temp = temperature if temperature is not None else self.temperature

        # CC round-11 Q1：空200/纯reasoning/极短正文属“可重试瞬时退化”，在本层跨温度
        # 即时重试（无退避、加 cache-bust nonce），与 429/5xx/超时的异常重试互不叠加。
        result = None
        deg_cls = "VALID"
        temps = [base_temp] + list(DEGEN_RETRY_TEMPERATURES)
        for d_i, attempt_temp in enumerate(temps):
            result = self._one_chat_attempt(
                messages, attempt_temp, max_tokens, retry_on_error,
                max_retries, extra_body, timeout, timeout_spec,
                cache_bust=(d_i > 0),
            )
            deg_cls = classify_content(
                result.get("content", "") if isinstance(result, dict) else str(result),
                (result or {}).get("reasoning_content", "") if isinstance(result, dict) else "",
                phase=getattr(self, "phase", "") or "",
            )
            if deg_cls == "VALID":
                return result
            if not retry_on_error:
                break
            logger.warning(
                f"Degenerate 200 from {self.model} phase={getattr(self, 'phase', '?')} "
                f"({deg_cls}); cross-temperature retry {d_i + 1}/{len(temps) - 1} "
                f"temp {base_temp}->{attempt_temp}"
            )
            self._fire_degenerate(deg_cls)
        # 跨温度预算耗尽：不抛异常，打标记返回最后一个（空/退化）结果，交由上层现有
        # 近空/解析失败路径处理；标记便于统计与（未来）系统性退化窗口判定。
        if isinstance(result, dict):
            result[DEGEN_EXHAUSTED_FLAG] = deg_cls
        return result

    def _one_chat_attempt(
        self,
        messages: list[dict],
        attempt_temperature: float,
        max_tokens: Optional[int],
        retry_on_error: bool,
        max_retries: int,
        extra_body: Optional[dict],
        timeout: Optional[int],
        timeout_spec: Optional[TimeoutSpec],
        cache_bust: bool = False,
    ) -> dict:
        client = self._stream_client(timeout_spec)
        last_error = None
        use_stream = (timeout_spec is not None and timeout_spec.stream)

        for attempt in range(max_retries):
            try:
                body = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": attempt_temperature,
                    "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
                }
                if extra_body:
                    body.update(extra_body)
                if cache_bust:
                    # 跨温度已改 body；nonce 仅为规避任何网关级坏缓存，缺省字段对端通常忽略。
                    body["client_nonce"] = f"{int(time.time() * 1000)}_{random.randint(1000, 9999)}"
                if use_stream:
                    body["stream"] = True
                    body["stream_options"] = {"include_usage": True}
                    return self._stream_chat(client, body, timeout_spec, max_tokens)
                else:
                    resp = client.post("/v1/chat/completions", json=body)
                    self._raise_status(
                        resp.status_code,
                        self._resp_bytes(resp),
                        getattr(resp, "headers", None) or {},
                    )
                    data = resp.json()
                usage = data.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)
                total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
                reasoning_tokens = usage.get("reasoning_tokens", 0)
                choice = data["choices"][0]
                reasoning_content = choice.get("message", {}).get("reasoning_content", "")
                with _CALL_LOG_LOCK:
                    _call_log.append({
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "reasoning_tokens": reasoning_tokens,
                        "total_tokens": total_tokens,
                    })
                # Record call metrics for unified cost tracking
                try:
                    phase = getattr(self, 'phase', 'unknown')
                    record_call(
                        phase=phase,
                        model=self.model,
                        success=True,
                        failover=False,
                        prompt_tokens=prompt_tokens,
                        completion_tokens=completion_tokens,
                        reasoning_tokens=reasoning_tokens,
                        latency_s=0,
                        cost_usd=None,  # compute from module-level pricing rates
                    )
                except Exception:
                    pass
                return {
                    "role": choice.get("message", {}).get("role", "assistant"),
                    "content": choice.get("message", {}).get("content", ""),
                    "finish_reason": choice.get("finish_reason"),
                    "reasoning_content": reasoning_content or "",
                    "_usage": {
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                        "reasoning_tokens": reasoning_tokens,
                    },
                }
            except (PermanentLLMError, ConfigFatalError):
                raise
            except (TransientLLMError, RateLimitError) as e:
                last_error = e
                if not retry_on_error or attempt >= max_retries - 1:
                    raise
                _wait = getattr(e, "retry_after", None)
                time.sleep(float(_wait) if _wait else float(min(2 ** attempt * 5, 60)))
            except httpx.HTTPStatusError as e:
                last_error = e
                self._raise_status(e.response.status_code, e.response.read(), e.response.headers)
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.TimeoutException) as e:
                last_error = e
                msg = str(e)
                is_disconnected = "Server disconnected" in msg or "RemoteProtocolError" in msg or "Connection closed" in msg
                logger.error(f"连接失败 (尝试 {attempt+1}/{max_retries}): {e}")
                if not retry_on_error or attempt == max_retries - 1:
                    raise TransientLLMError(f"API连接失败：{e}", phase=getattr(self, 'phase', None)) from e
                if is_disconnected:
                    continue
                time.sleep(2 if is_disconnected else 10)
            except Exception as e:
                last_error = e
                msg = str(e)
                if "429" in msg or getattr(e, 'status_code', None) == 429:
                    raise TransientLLMError(f"429 from {self.model}", phase=getattr(self, 'phase', None)) from e
                logger.error(f"未知错误 (尝试 {attempt+1}/{max_retries}): {e}")
                if not retry_on_error or attempt == max_retries - 1:
                    raise TransientLLMError(str(e), phase=getattr(self, 'phase', None)) from e
                time.sleep(2 ** attempt)

        # Record final failure once if all retries exhausted
        try:
            record_call(
                phase=getattr(self, 'phase', 'unknown'),
                model=self.model,
                success=False,
                failover=False,
                prompt_tokens=0,
                completion_tokens=0,
                reasoning_tokens=0,
                latency_s=0,
                cost_usd=None,  # compute from module-level pricing rates
            )
        except Exception:
            pass
        raise TransientLLMError(f"LLM调用失败，已重试{max_retries}次：{last_error}", phase=getattr(self, 'phase', None))

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """无 usage 回传时的保守 token 估算（CJK 按 1 字约 1 token，ASCII 按 4 字符约 1）。"""
        if not text:
            return 0
        cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
        other = len(text) - cjk
        return cjk + max(1, other // 4)

    @staticmethod
    def _estimate_usage(body: dict, content: str, reasoning: str) -> dict:
        """末块无 usage 时给出保守的整数 token 估算（CJK≈1/token，ASCII≈4字符/token）。"""
        completion = LLMClient._estimate_tokens(content) + LLMClient._estimate_tokens(reasoning)
        prompt_chars = sum(len(str(m.get("content", ""))) for m in (body.get("messages") or []))
        prompt = max(1, prompt_chars // 3)
        return {
            "prompt_tokens": int(prompt),
            "completion_tokens": int(completion),
            "total_tokens": int(prompt + completion),
        }

    def _stream_chat(self, client, body: dict, spec, max_tokens) -> dict:
        """单次流式请求：解析 SSE 并累加 content/reasoning/usage。

        重试由 chat_completion 外层统一处理。超时基于可 monkeypatch 的 time.monotonic：
        - 相邻数据块间隔 > idle_s 抛 TransientLLMError("idle ...")
        - 自发起耗时 > effective_total(max_tokens) 抛 TransientLLMError("total ...")
        """
        spec = spec or TimeoutSpec()
        body = dict(body)
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
        total_cap = spec.effective_total(max_tokens)
        start = time.monotonic()
        last_data = start
        content_parts: list[str] = []
        reason_parts: list[str] = []
        finish_reason = None
        usage = None

        with client.stream("POST", "/v1/chat/completions", json=body) as resp:
            if resp.status_code != 200:
                self._raise_status(
                    resp.status_code,
                    self._resp_bytes(resp),
                    getattr(resp, "headers", None) or {},
                )
            for line in resp.iter_lines():
                now = time.monotonic()
                elapsed = now - start
                if elapsed > total_cap:
                    raise TransientLLMError(
                        f"total cap exceeded ({total_cap}s, elapsed {elapsed:.1f}s)"
                    )
                if now - last_data > spec.idle_s:
                    raise TransientLLMError(
                        f"idle timeout waiting for next chunk (>{spec.idle_s}s)"
                    )
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                payload = line[5:].strip() if line.startswith("data:") else line.strip()
                if not payload:
                    continue
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                choices = obj.get("choices") or []
                got_data = False
                if choices:
                    delta = choices[0].get("delta") or {}
                    piece = delta.get("content")
                    if piece:
                        content_parts.append(piece)
                        got_data = True
                    rpiece = delta.get("reasoning_content")
                    if rpiece:
                        reason_parts.append(rpiece)
                        got_data = True
                    fr = delta.get("finish_reason") or choices[0].get("finish_reason")
                    if fr:
                        finish_reason = fr
                        got_data = True
                if obj.get("usage"):
                    usage = obj["usage"]
                    got_data = True
                if got_data:
                    last_data = now

        content = "".join(content_parts)
        reasoning = "".join(reason_parts)
        if usage is None:
            usage = self._estimate_usage(body, content, reasoning)
        prompt_tokens = int(usage.get("prompt_tokens", 0))
        completion_tokens = int(usage.get("completion_tokens", 0))
        total_tokens = int(usage.get("total_tokens", prompt_tokens + completion_tokens))
        _details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = int(
            usage.get("reasoning_tokens", _details.get("reasoning_tokens", 0)) or 0
        )
        with _CALL_LOG_LOCK:
            _call_log.append({
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "reasoning_tokens": reasoning_tokens,
                "total_tokens": total_tokens,
            })
        # Record call metrics for unified cost tracking
        try:
            record_call(
                phase=getattr(self, 'phase', 'unknown'),
                model=self.model,
                success=True,
                failover=False,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                reasoning_tokens=reasoning_tokens,
                latency_s=0,
                cost_usd=None,  # compute from module-level pricing rates
            )
        except Exception:
            pass
        return {
            "role": "assistant",
            "content": content,
            "finish_reason": finish_reason or "stop",
            "reasoning_content": reasoning,
            "_usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "reasoning_tokens": reasoning_tokens,
            },
        }

    @staticmethod
    def _resp_bytes(resp) -> bytes:
        # 兼容流式 context-manager 响应（.read）与测试 fake（.content/_content）。
        for attr in ("content", "_content"):
            v = getattr(resp, attr, None)
            if isinstance(v, (bytes, bytearray)):
                return bytes(v)
        try:
            return resp.read()
        except Exception:
            return b""

    def _stream_client(self, spec: Optional[TimeoutSpec] = None) -> httpx.Client:
        injected = getattr(self._local, 'client', None)
        if injected is not None:
            return injected
        key = (spec.connect_s, spec.idle_s, spec.write_s, spec.pool_s) if spec else (self.timeout, self.timeout, self.timeout, self.timeout)
        cx = getattr(self._local, 'spec_client', None)
        if cx is None or getattr(self._local, 'spec_key', None) != key:
            headers = {'Content-Type': 'application/json'}
            if self.api_key:
                headers['Authorization'] = f'Bearer {self.api_key}'
            timeout_kwargs = {}
            if spec:
                timeout_kwargs = dict(
                    connect=spec.connect_s,
                    read=spec.idle_s,
                    write=spec.write_s,
                    pool=spec.pool_s,
                )
            else:
                timeout_kwargs['connect'] = self.timeout
                timeout_kwargs['read'] = self.timeout
                timeout_kwargs['write'] = self.timeout
                timeout_kwargs['pool'] = self.timeout
            import httpx
            cx = httpx.Client(base_url=self.api_base, headers=headers,
                              timeout=httpx.Timeout(**timeout_kwargs))
            self._local.spec_client = cx
            self._local.spec_key = key
        return cx

    def _raise_status(self, status: int, text: bytes, headers) -> None:
        if status == 400:
            raise PermanentLLMError(f'HTTP {status}: {text.decode("utf-8", errors="replace")}')
        elif status in (401, 403):
            raise ConfigFatalError(f'HTTP {status}: {text.decode("utf-8", errors="replace")}')
        elif status == 429:
            retry_after = headers.get('Retry-After') if headers is not None else None
            e = RateLimitError(f'HTTP {status}: rate limited')
            try:
                e.retry_after = int(retry_after)
            except (TypeError, ValueError):
                pass
            raise e
        elif status >= 400:
            raise TransientLLMError(f'HTTP {status}')
        # For 2xx and other success codes, do nothing
    def close(self):
        client = getattr(self._local, "client", None)
        if client is not None:
            client.close()
            self._local.client = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _repair_json(cleaned: str) -> Optional[dict]:
    """多级修复模型返回的 JSON，成功返回 dict，失败返回 None。"""
    import re
    # Handle double-brace JSON (model may echo Python f-string escapes like {{...}})
    cleaned = cleaned.replace('{{', '{').replace('}}', '}')
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    try:
        import json_repair
        parsed = json_repair.loads(cleaned)
        if parsed is not None and isinstance(parsed, dict):
            logger.info("JSON repair succeeded via json_repair")
            return parsed
    except Exception:
        pass
    repaired = re.sub(
        r'(?<=:\s)([^",{}\[\]\n\r]+?)(?=\s*[},\]\n\r])',
        lambda m: '"' + m.group(1).strip() + '"',
        cleaned,
    )
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    repaired2 = re.sub(
        r':\s*([^":\{\[\],}\n\r][^,}\n\r]*?)(?=\s*[},\]])',
        lambda m: ': "' + m.group(1).strip() + '"',
        repaired,
    )
    try:
        return json.loads(repaired2)
    except json.JSONDecodeError:
        pass
    try:
        import json_repair
        m = re.search(r'\{[\s\S]*\}', cleaned)
        if m:
            parsed = json_repair.loads(m.group())
            if parsed and isinstance(parsed, dict):
                return parsed
    except Exception:
        pass
    return None


def _load_runtime_config() -> dict:
    """读取 runtime_config.json 一次，供 client 与 provider 复用，避免重复 IO。"""
    try:
        import json
        from pathlib import Path
        return json.loads((Path(__file__).parent.parent / "config" /
                           "runtime_config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def call_llm(
    prompt: str,
    system_prompt: str = "",
    client: Optional[LLMClient] = None,
    output_json: bool = False,
    provider_config: Optional["ProviderConfig"] = None,
    **kwargs,
) -> str | dict:
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})
    cfg = None
    if client is None:
        cfg = _load_runtime_config()
        client = LLMClient.from_config_dict(cfg)
    from novel_engine.core.llm_provider import LLMProvider, ProviderConfig
    if provider_config is None:
        provider_config = (_provider_config_from_runtime(cfg)
                           if cfg is not None else _provider_config_from_runtime())
    provider = LLMProvider(client, provider_config)
    return provider.complete(
        messages, output_json=output_json,
        temperature=kwargs.get("temperature"),
        max_tokens=kwargs.get("max_tokens"),
        extra_body=kwargs.get("extra_body"),
    )


def _provider_config_from_runtime(cfg: Optional[dict] = None, section: str = "provider"):
    try:
        from novel_engine.core.llm_provider import ProviderConfig
        if cfg is None:
            import json
            from pathlib import Path
            cfg = json.loads((Path(__file__).parent.parent / "config" /
                              "runtime_config.json").read_text(encoding="utf-8"))
        p = cfg.get(section, {})
        return ProviderConfig(
            family=p.get("family", "qwen"),
            api_base=p.get("api_base", "https://api.siliconflow.cn"),
            model=p.get("model", "Qwen/Qwen3.5-4B"),
            reasoning_fallback=p.get("reasoning_fallback", True),
            thinking_param=p.get("thinking_param", "enable_thinking"),
            cache_bust_suffix=p.get("cache_bust_suffix", ""),
            retry_temperatures=p.get("retry_temperatures", [0.85, 0.7, 0.95, 1.0]),
        )
    except Exception:
        return ProviderConfig()


def get_call_log(client: Optional["LLMClient"] = None) -> list[dict]:
    """Return a snapshot of the thread-safe module-level call log."""
    with _CALL_LOG_LOCK:
        return list(_call_log)


def reset_call_log(client: Optional["LLMClient"] = None):
    """Clear the thread-safe module-level call log."""
    with _CALL_LOG_LOCK:
        _call_log.clear()
