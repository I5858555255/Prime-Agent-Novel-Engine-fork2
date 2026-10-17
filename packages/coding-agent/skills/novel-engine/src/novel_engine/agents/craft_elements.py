# -*- coding: utf-8 -*-
"""CC round-25 P0-3: 场景文学性技法要素（零 LLM，纯确定性，离线可测）。

director 任务卡的每个场景蓝图补一个 ``scene_craft_elements``：
- hook_type：场末钩子的"技法类型"（不是范文/句式模板），按 章号+场序 在固定枚举上
  轮换，保证相邻场、相邻章不重复同一种钩子手法；
- hook_position：固定 scene_end，要求钩子落在场景最后一两句而非散落；
- info_reversal_point：本场是否包含一次信息反转（模型可给具体内容，缺省否）；
- sensory_contrast_pair：一对用于张力高点的反差意象"类型"（同样只给类型不给范文）。

设计原则（CC round-25）：只给"技法类型清单 + 一句抽象效果要求"，绝不提供具体句式
范文，避免抄袭腔与同质化；具体措辞由 flash 在类型约束下原创。本层**不新增任何
零 LLM 检测门**——hook 是否真的成立是语义判断，交给 reviewer 的 hook_strength 维。
"""
from __future__ import annotations

# 钩子手法枚举：key -> 一句抽象效果描述（不给具体句子）
HOOK_TYPES: list[tuple[str, str]] = [
    ("revelation", "在结尾抛出一条颠覆角色此前认知的新信息，让读者意识到前面的判断需要重估"),
    ("reversal", "让本场看似成立的局势或判断在结尾陡然翻面，但翻面必须由本场已铺的细节支撑"),
    ("sensory_contrast", "在结尾用一组强烈反差意象收束，把情绪顶到张力点，意象必须服务信息而非堆砌"),
    ("question_planted", "在结尾抛出一个角色当下无法回答、读者也想知道答案的具体问题，逼出追读"),
    ("decision_point", "把角色逼到必须立刻做出有代价选择的当口收束，不写他选完之后"),
    ("threat_revealed", "让此前只在暗处的威胁在结尾第一次具体显形（轮廓、动作或后果之一）"),
    ("discovery", "让角色在结尾第一次注意到一个被所有人忽略、却改变局面含义的关键细节"),
    ("interrupted", "在关键动作或真相将出未出的瞬间被外力强行打断，场面停在截断处"),
]
HOOK_KEYS = [k for k, _ in HOOK_TYPES]
_HOOK_EFFECT = dict(HOOK_TYPES)

# 反差意象类型（给类型，不给现成句子）
SENSORY_PAIRS: list[tuple[str, str]] = [
    ("温暖", "冰冷"),
    ("光亮", "黑暗"),
    ("寂静", "嘈杂"),
    ("甘美", "苦涩"),
    ("开阔", "逼仄"),
    ("柔软", "坚硬"),
    ("鲜活", "枯败"),
    ("轻盈", "沉滞"),
]

CRAFT_KEY = "scene_craft_elements"


def _info_reversal(existing) -> dict:
    """保留模型给出的信息反转内容；缺省/非法归一为 {present:false,content:''}。"""
    if isinstance(existing, dict):
        content = str(existing.get("content", "") or "").strip()
        present = bool(existing.get("present", False)) or bool(content)
        return {"present": bool(present), "content": content}
    if isinstance(existing, str) and existing.strip():
        return {"present": True, "content": existing.strip()[:120]}
    return {"present": False, "content": ""}


def ensure_scene_craft_elements(task_card: dict) -> dict:
    """按最终场序为每个蓝图确定性补齐/轮换 scene_craft_elements（幂等，就地修改）。

    hook_type 与 sensory pair 由 (chapter_num, 场序) 权威轮换，保证相邻不重复；
    info_reversal_point 保留模型在任务卡里给出的具体内容。对已存在但非法的 hook_type
    同样按轮换纠正。
    """
    if not isinstance(task_card, dict):
        return task_card
    bps = task_card.get("scene_blueprints") or []
    if not isinstance(bps, list) or not bps:
        return task_card
    try:
        chapter_num = int(task_card.get("chapter_num", 0) or 0)
    except (TypeError, ValueError):
        chapter_num = 0
    n_hooks = len(HOOK_KEYS)
    n_pairs = len(SENSORY_PAIRS)
    idx = 0
    for bp in bps:
        if not isinstance(bp, dict):
            continue
        craft = bp.get(CRAFT_KEY)
        if not isinstance(craft, dict):
            craft = {}
        hook_key = HOOK_KEYS[(chapter_num + idx) % n_hooks]
        a, b = SENSORY_PAIRS[(chapter_num * 3 + idx * 2) % n_pairs]
        bp[CRAFT_KEY] = {
            "hook_type": hook_key,
            "hook_position": "scene_end",
            "info_reversal_point": _info_reversal(craft.get("info_reversal_point")),
            "sensory_contrast_pair": [a, b],
        }
        idx += 1
    return task_card


def hook_effect(hook_key: str) -> str:
    return _HOOK_EFFECT.get(hook_key, _HOOK_EFFECT["question_planted"])


def craft_block(scene_blueprint: dict) -> str:
    """渲染给 writer 的"技法类型要求"区块（非范文）；无 craft 要素时返回空串。"""
    if not isinstance(scene_blueprint, dict):
        return ""
    craft = scene_blueprint.get(CRAFT_KEY)
    if not isinstance(craft, dict):
        return ""
    hook_key = str(craft.get("hook_type", "") or "").strip()
    if hook_key not in _HOOK_EFFECT:
        return ""
    pair = craft.get("sensory_contrast_pair") or ["", ""]
    pair_txt = f"“{pair[0]}／{pair[1]}”" if len(pair) == 2 and pair[0] and pair[1] else ""
    rev = _info_reversal(craft.get("info_reversal_point"))
    lines = [
        "## 文学性技法要求（CC round-25：以下是技法类型与效果要求，不是范文，具体措辞须你原创）",
        f"- 场末钩子手法：{hook_key}——{hook_effect(hook_key)}。钩子必须落在本场景最后一两句，"
        "用本场已写到的具体情节/意象自然达成，禁止另起一段喊口号、禁止套用陈词句式。",
    ]
    if pair_txt:
        lines.append(
            f"- 反差意象：在张力高点可使用一组{pair_txt}的感官反差强化张力，全场至多一处、"
            "必须服务于信息或情绪，不单独堆砌环境。")
    if rev["present"] and rev["content"]:
        lines.append(f"- 信息反转：本场应包含一次认知反转——{rev['content']}；反转前需有可回看的细节铺垫。")
    lines.append("- 只要求达到上述效果，严禁照抄本指令句式或使用套路化模板表达。")
    return "\n".join(lines) + "\n"


def craft_directive_lines(scene_blueprint: dict) -> list[str]:
    """供 Level2 聚焦重写等精简 prompt 复用的单行技法要求（非范文）。"""
    if not isinstance(scene_blueprint, dict):
        return []
    craft = scene_blueprint.get(CRAFT_KEY)
    if not isinstance(craft, dict):
        return []
    hook_key = str(craft.get("hook_type", "") or "").strip()
    if hook_key not in _HOOK_EFFECT:
        return []
    out = [f"场末钩子用 {hook_key} 手法（{hook_effect(hook_key)}），落在最后一两句，具体措辞原创、禁套路句。"]
    pair = craft.get("sensory_contrast_pair") or ["", ""]
    if len(pair) == 2 and pair[0] and pair[1]:
        out.append(f"张力高点可用一组“{pair[0]}／{pair[1]}”感官反差，至多一处、服务信息。")
    return out
