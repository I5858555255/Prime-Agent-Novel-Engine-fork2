# -*- coding: utf-8 -*-
"""CC round-22 P0-2（缺陷B）：任务卡硬约束词面化履约门（零 LLM 纯规则）。

director 在每个 scene_blueprint 里可给 scene_constraints（只对显式带该字段的卡硬判，
无法词面化的约束如"情绪克制"不在此处理，继续靠 writer 负例 prompt 软约束）。
当前支持两类：

1) action_forbidden_until：某动作在"满足条件"之前禁止发生。
   {type, forbidden_action_markers:[...], condition_markers:[...]}
   正文出现禁止动作词、却未出现任何条件已满足的标记 -> 违规。
2) identity_concealment：某身份/术语在本场景必须保密。
   {type, forbidden_reveal_terms:[...]}
   正文出现任一泄漏术语 -> 违规（带 leaked_terms）。

命中后由编排层对该场景定点重生（负例直接引用违反的约束原文）；本模块只做判定。
"""
from __future__ import annotations

SUPPORTED_TYPES = frozenset({"action_forbidden_until", "identity_concealment"})


def _as_str_list(v) -> list[str]:
    if isinstance(v, str):
        return [v] if v.strip() else []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if str(x).strip()]
    return []


def _check_one(text: str, c: dict) -> dict | None:
    ctype = str(c.get("type", "")).strip()
    if ctype not in SUPPORTED_TYPES:
        return None
    cid = c.get("constraint_id") or ctype

    if ctype == "action_forbidden_until":
        actions = _as_str_list(c.get("forbidden_action_markers"))
        conditions = _as_str_list(c.get("condition_markers"))
        if not actions:
            return None
        hit_actions = [m for m in actions if m in text]
        if not hit_actions:
            return None
        has_condition = any(m in text for m in conditions)
        if has_condition:
            return None
        return {
            "type": ctype,
            "constraint_id": cid,
            "action_terms": hit_actions,
            "note": str(c.get("note", "") or ""),
            "detail": f"出现禁止动作 {hit_actions} 但未见条件满足标记 {conditions}",
        }

    # identity_concealment
    terms = _as_str_list(c.get("forbidden_reveal_terms"))
    if not terms:
        return None
    leaked = [w for w in terms if w in text]
    if not leaked:
        return None
    return {
        "type": ctype,
        "constraint_id": cid,
        "leaked_terms": leaked,
        "note": str(c.get("note", "") or ""),
        "detail": f"泄漏应保密身份术语 {leaked}",
    }


def check_constraint_compliance(text: str, constraints) -> list[dict]:
    """返回该场景正文违反的约束列表（空列表=合规/无约束）。"""
    if not text or not isinstance(constraints, (list, tuple)):
        return []
    out = []
    for c in constraints:
        if isinstance(c, dict):
            v = _check_one(text, c)
            if v is not None:
                out.append(v)
    return out


def constraints_for_blueprint(bp: dict) -> list[dict]:
    """安全读取某场景蓝图的 scene_constraints（仅接受 dict 列表）。"""
    cs = (bp or {}).get("scene_constraints")
    if not isinstance(cs, (list, tuple)):
        return []
    return [c for c in cs if isinstance(c, dict)]


def violation_negative_example(v: dict) -> str:
    """把违规约束转成给定点重生用的负例/返工指令片段。"""
    if v["type"] == "action_forbidden_until":
        cond = ""
        return (f"违反硬约束[{v['constraint_id']}]：{v.get('detail')}。"
                f"在规定条件满足前，严禁演出 {v['action_terms']} 这一动作。")
    return (f"违反保密硬约束[{v['constraint_id']}]：{v.get('detail')}。"
            f"必须删除/改写所有暴露 {v['leaked_terms']} 的措辞，保持该身份与术语完全隐藏。")


def _clean_term_list(value) -> list[str] | None:
    """规整词表：去空白/去重/丢弃非字符串；空表返回 None。"""
    if not isinstance(value, list):
        return None
    out: list[str] = []
    for x in value:
        if isinstance(x, str):
            s = x.strip()
            if s and s not in out:
                out.append(s)
    return out or None


def sanitize_scene_constraints(constraints) -> list[dict]:
    """把单场景 scene_constraints 清洗为可被硬判的合法对象数组。

    真机实测模型会把约束写成自由文本字符串、用错 type 或漏字段；这类项机器无法
    可靠解析，一律丢弃（身份隐藏另有 writer scope 约束兜底），只保留结构完整的
    action_forbidden_until / identity_concealment 两类。
    """
    if not isinstance(constraints, list):
        return []
    keep: list[dict] = []
    for c in constraints:
        if not isinstance(c, dict):
            continue
        typ = c.get("type")
        cid = c.get("constraint_id")
        cid = cid if isinstance(cid, str) and cid.strip() else f"c_{len(keep)}"
        if typ == "action_forbidden_until":
            fa = _clean_term_list(c.get("forbidden_action_markers"))
            cm = _clean_term_list(c.get("condition_markers"))
            if fa and cm:
                keep.append({"constraint_id": cid, "type": typ,
                             "forbidden_action_markers": fa, "condition_markers": cm})
        elif typ == "identity_concealment":
            fr = _clean_term_list(c.get("forbidden_reveal_terms"))
            if fr:
                keep.append({"constraint_id": cid, "type": typ, "forbidden_reveal_terms": fr})
    return keep


def sanitize_task_card_constraints(task_card: dict) -> dict:
    """就地清洗任务卡内每个场景的 scene_constraints，返回同一 task_card。"""
    if isinstance(task_card, dict):
        for bp in task_card.get("scene_blueprints", []) or []:
            if isinstance(bp, dict):
                bp["scene_constraints"] = sanitize_scene_constraints(bp.get("scene_constraints"))
    return task_card
