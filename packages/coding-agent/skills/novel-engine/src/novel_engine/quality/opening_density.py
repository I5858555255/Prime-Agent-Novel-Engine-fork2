# -*- coding: utf-8 -*-
"""CC29 批C：director 开场事件密度零 LLM 校验（存在性/结构性，不做语义猜测）。

长篇开篇（及每卷开篇）若连续多场只有主角内心独白、没有外部可观察事件，读者会流失；
相邻场在同一地点/时刻且无不可逆状态变化，即把同一现场再规划一遍。本门在【任务卡源头】
做确定性拦截，语义责任在 director（显式声明字段），零 LLM 只校验声明是否存在/是否自洽，
不从自由文本猜测，避免启发式漂移误伤。

裁决（CC29）：
- 新增 3 个显式字段 external_event / irreversible_state_delta / world_anomaly_signal；
  scene_anchor 由零 LLM 从既有 location+narrative_time+characters 派生。
- 作用域：chapter<=3 或卷开篇 = STRICT（硬违规回灌重生）；普通章 = SOFT（只记录信号、
  注入 writer 提示、交下游密度契约/评审，不拒卡）。
- 即便 STRICT 也只禁“连续≥2场 external_event 皆空（纯内省连场）”，允许 4 场中 1 个过渡场。
- world_anomaly_signal 仅第 1 章前 2 场强制 ≥1 个；只做字段存在+最小长度校验。
- 密度类硬违规回灌重生最多 2 次，仍不合格 fail-open（注入 writer 硬约束+打标），不 HALT；
  结构性缺失（beats/编号/字段/场数）仍走原 3 次失败 HALT。
"""
from __future__ import annotations

MIN_FIELD_CHARS = 6
STRICT_FIRST_N_CHAPTERS = 3
MAX_INTERIOR_RUN = 1          # 允许至多连续 1 个无外部事件场；>=2 即违规
DENSITY_REGEN_BUDGET = 2      # 密度类回灌重生次数，超过 fail-open

# CC round-11 R2：户外/公共场所关键词，命中任一即认为该场景不在纯室内。
_OUTDOOR_LOCATION_MARKERS = {
    "村口", "老槐树", "古树", "大槐树", "祠堂", "井台", "老井", "水井",
    "巷道", "村道", "土路", "晒谷场", "晒场", "田埂", "田里", "田垄",
    "院外", "门外", "门口空地", "河边", "溪边", "河岸", "河滩", "溪滩",
    "集市", "渡口", "山坡", "坡地", "山腰", "山道",
    "森林", "树林", "草原", "沼泽", "火山", "悬崖", "岩石",
    "街道", "小巷", "广场", "公园", "寺庙", "神社", "修道院",
    "走廊", "阳台", "露台", "天台", "庭院", "花园", "菜园",
    "战场", "要塞", "城门", "城墙", "城楼", "吊桥",
}


def is_outdoor_scene(bp: dict) -> bool:
    """判断蓝图场景是否属于户外/公共空间。仅检查 location 字段。"""
    if not isinstance(bp, dict):
        return False
    loc = _clean(bp.get("location", "") or "").lower()
    for marker in _OUTDOOR_LOCATION_MARKERS:
        if marker.lower() in loc:
            return True
    return False


def _clean(v) -> str:
    return str(v).strip() if v is not None else ""


def has_external_event(bp: dict) -> bool:
    return len(_clean(bp.get("external_event"))) >= MIN_FIELD_CHARS


def has_state_delta(bp: dict) -> bool:
    """不可逆状态变化：新字段优先；兼容既有 scene_progression_contract.irreversible_change
    （二者都是 director 的显式声明，属存在性判据，非自由文本猜测）。"""
    if len(_clean(bp.get("irreversible_state_delta"))) >= MIN_FIELD_CHARS:
        return True
    contract = bp.get("scene_progression_contract") or {}
    if isinstance(contract, dict) and len(_clean(contract.get("irreversible_change"))) >= MIN_FIELD_CHARS:
        return True
    return False


def has_world_anomaly(bp: dict) -> bool:
    return len(_clean(bp.get("world_anomaly_signal"))) >= MIN_FIELD_CHARS


def compute_scene_anchor(bp: dict) -> str:
    loc = _clean(bp.get("location"))
    t = _clean(bp.get("narrative_time"))
    chars = bp.get("characters") or []
    if not isinstance(chars, list):
        chars = [chars]
    names = sorted({_clean(c) for c in chars if _clean(c)})
    return "|".join([loc, t, ",".join(names)])


def enforcement_level(chapter_num: int, is_volume_opening: bool = False) -> str:
    try:
        ch = int(chapter_num)
    except (TypeError, ValueError):
        ch = 0
    if ch <= STRICT_FIRST_N_CHAPTERS or bool(is_volume_opening):
        return "STRICT"
    return "SOFT"


def _scene_constraint(bp: dict, no_ee: bool, no_delta: bool) -> str:
    parts = []
    if no_ee:
        parts.append("任务卡未给本场景规划明确的外部事件，写作时必须让可被旁人观察到的"
                     "外部事件/动作实际发生，严禁写成纯内心独白或纯气氛堆砌")
    if no_delta:
        parts.append("必须在本场景结束时落下一个不可逆的世界/人物/关系变化，不能让状态原地不动")
    return "；".join(parts)


def assess_opening_density(blueprints: list[dict], chapter_num: int,
                           is_volume_opening: bool = False) -> dict:
    """返回开场密度判定。hard_violations 仅 STRICT 下用于回灌重生；SOFT 恒为空。"""
    bps = list(blueprints or [])
    level = enforcement_level(chapter_num, is_volume_opening)

    no_ee: list[bool] = []
    no_delta: list[bool] = []
    # CC round-11 R2：连续纯室内判定——无 external_event 且非户外场景才算室内连场
    is_interior: list[bool] = []
    soft_signals: list[dict] = []
    scene_constraints: dict[str, str] = {}
    for idx, bp in enumerate(bps):
        bp = bp if isinstance(bp, dict) else {}
        sn = bp.get("scene_num", idx + 1)
        ee = has_external_event(bp)
        outdoor = is_outdoor_scene(bp)
        sd = has_state_delta(bp)
        no_ee.append(not ee)
        no_delta.append(not sd)
        # 纯室内判定：无 external_event 且非户外
        is_interior.append((not ee) and not outdoor)
        if not ee:
            soft_signals.append({"scene": sn, "type": "missing_external_event"})
        if not sd:
            soft_signals.append({"scene": sn, "type": "missing_state_delta"})
        if (not ee or not sd):
            directive = _scene_constraint(bp, not ee, not sd)
            if directive:
                scene_constraints[str(sn)] = directive

    hard_violations: list[dict] = []
    if level == "STRICT" and bps:
        # 连续 >=2 场纯室内（无 external_event 且非户外）
        run_start = None
        for i, flag in enumerate(is_interior + [False]):
            if flag and run_start is None:
                run_start = i
            elif not flag and run_start is not None:
                run_len = i - run_start
                if run_len > MAX_INTERIOR_RUN:
                    hard_violations.append({
                        "type": "consecutive_interior_only",
                        "scenes": [bps[k].get("scene_num", k + 1) for k in range(run_start, i)]})
                run_start = None
        # 相邻场 anchor 相同且后场无不可逆状态变化
        anchors = [compute_scene_anchor(bp if isinstance(bp, dict) else {}) for bp in bps]
        for i in range(1, len(anchors)):
            if anchors[i] and anchors[i] == anchors[i - 1] and no_delta[i]:
                hard_violations.append({
                    "type": "duplicate_anchor_no_progression",
                    "scenes": [bps[i - 1].get("scene_num", i), bps[i].get("scene_num", i + 1)]})
        # 仅第 1 章：前 2 场至少 1 个世界异常信号
        try:
            _ch1 = int(chapter_num) == 1
        except (TypeError, ValueError):
            _ch1 = False
        if _ch1 and not any(has_world_anomaly(bp if isinstance(bp, dict) else {})
                            for bp in bps[:2]):
            hard_violations.append({"type": "no_world_anomaly_in_opening", "scenes": [1, 2]})

    return {
        "level": level,
        "clean": not hard_violations,
        "hard_violations": hard_violations,
        "soft_signals": soft_signals,
        "scene_constraints": scene_constraints,
    }


def decide_density_action(level: str, has_hard: bool, density_attempts: int) -> str:
    """STRICT 且有硬违规：前 DENSITY_REGEN_BUDGET 次回灌重生，之后 fail-open；其余放行。"""
    if level == "STRICT" and has_hard:
        if int(density_attempts) < DENSITY_REGEN_BUDGET:
            return "regenerate"
        return "fail_open"
    return "clean"
