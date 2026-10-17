# -*- coding: utf-8 -*-
"""CC29 批C：director 开场事件密度零 LLM 校验 + writer 注入 单测。"""
from novel_engine.quality import opening_density as od
from novel_engine.agents.scene_schema import build_scene_prompt


def _bp(num, ee="有人在院外叩门，陈老根起身开门", delta="陈老根决定收养弃婴",
        anomaly=None, loc="雾隐村村口", t="当夜子时", chars=("陈老根",)):
    return {
        "scene_num": num, "location": loc, "narrative_time": t,
        "characters": list(chars), "goal": "推进剧情的一个明确目标内容",
        "beats": ["beat一", "beat二", "beat三"],
        "external_event": ee, "irreversible_state_delta": delta,
        "world_anomaly_signal": anomaly,
    }


# 1) 字段存在性
def test_field_existence_helpers():
    assert od.has_external_event(_bp(1, ee="村民抬头看见异象发生"))
    assert not od.has_external_event(_bp(1, ee=""))
    assert not od.has_external_event(_bp(1, ee="短"))
    assert od.has_state_delta(_bp(1, delta="婴儿被正式抱离原地"))
    assert not od.has_state_delta(_bp(1, delta=""))
    # 兼容既有 scene_progression_contract.irreversible_change
    bp = _bp(1, delta="")
    bp["scene_progression_contract"] = {"irreversible_change": "陈老根当众立下收养誓言"}
    assert od.has_state_delta(bp)


def test_clean_when_all_fields_present():
    bps = [_bp(1, anomaly="雾气反常上升聚成漩涡"), _bp(2), _bp(3), _bp(4)]
    r = od.assess_opening_density(bps, 1)
    assert r["clean"] is True and r["hard_violations"] == []


# 3) 作用域分级
def test_enforcement_level():
    assert od.enforcement_level(1) == "STRICT"
    assert od.enforcement_level(3) == "STRICT"
    assert od.enforcement_level(2, True) == "STRICT"  # 卷开篇
    assert od.enforcement_level(50, True) == "STRICT"
    assert od.enforcement_level(50, False) == "SOFT"


# 4) 连续>=2场纯内省
def test_consecutive_interior_only_flagged():
    bps = [_bp(1, anomaly="老槐树无风自摇", loc="屋内"), _bp(2, ee="", loc="屋内"),
           _bp(3, ee="", loc="屋内"), _bp(4, loc="屋内")]  # 2、3场连续无 external_event 且全室内
    r = od.assess_opening_density(bps, 2)
    types = [v["type"] for v in r["hard_violations"]]
    assert "consecutive_interior_only" in types


def test_single_transition_scene_allowed():
    bps = [_bp(1, anomaly="老槐树无风自摇", loc="屋内"), _bp(2, ee="", loc="屋内"),
           _bp(3, loc="屋内"), _bp(4, loc="屋内")]  # 仅第2场无外部事件
    r = od.assess_opening_density(bps, 2)
    assert not any(v["type"] == "consecutive_interior_only" for v in r["hard_violations"])
    # 单场缺口仍记入软信号
    assert any(s["type"] == "missing_external_event" for s in r["soft_signals"])


# 2) 相邻 anchor 相同且无推进
def test_duplicate_anchor_no_progression_flagged():
    bps = [_bp(1, anomaly="绿雾逆升"),
           _bp(2, ee="", delta="", loc="雾隐村村口", t="当夜子时")]
    r = od.assess_opening_density(bps, 1)
    assert any(v["type"] == "duplicate_anchor_no_progression" for v in r["hard_violations"])


def test_same_anchor_with_delta_not_flagged():
    bps = [_bp(1, anomaly="绿雾逆升"),
           _bp(2, ee="陈老根跨过门槛进屋", delta="陈老根抱婴进屋、离开原地",
               loc="雾隐村村口", t="当夜子时")]
    r = od.assess_opening_density(bps, 1)
    assert not any(v["type"] == "duplicate_anchor_no_progression" for v in r["hard_violations"])


# 7) world_anomaly 仅第1章强制
def test_world_anomaly_only_chapter1():
    # 第1章前2场均无异常 -> 命中
    r1 = od.assess_opening_density([_bp(1, anomaly=None), _bp(2)], 1)
    assert any(v["type"] == "no_world_anomaly_in_opening" for v in r1["hard_violations"])
    # 第2章不强制
    r2 = od.assess_opening_density([_bp(1, anomaly=None), _bp(2, anomaly=None)], 2)
    assert not any(v["type"] == "no_world_anomaly_in_opening" for v in r2["hard_violations"])
    # 第1章第2场有异常 -> 通过（其余场给足外部事件/变化避免别的违规）
    r3 = od.assess_opening_density([_bp(1, anomaly=None), _bp(2, anomaly="老槐树无风自摇")], 1)
    assert r3["clean"] is True


# 8) SOFT 普通章不拒卡、只记软信号
def test_soft_chapter_records_signal_without_hard():
    bps = [_bp(1, ee="", anomaly=None), _bp(2, ee="", anomaly=None),
           _bp(3), _bp(4)]
    r = od.assess_opening_density(bps, 50)
    assert r["level"] == "SOFT"
    assert r["hard_violations"] == []
    assert r["soft_signals"]


# 5) 分级路由：密度类 2 次重生后 fail-open
def test_decide_density_action():
    assert od.decide_density_action("STRICT", True, 0) == "regenerate"
    assert od.decide_density_action("STRICT", True, 1) == "regenerate"
    assert od.decide_density_action("STRICT", True, 2) == "fail_open"
    assert od.decide_density_action("STRICT", False, 0) == "clean"
    assert od.decide_density_action("SOFT", True, 0) == "clean"


# 6) fail-open/SOFT writer 约束注入到对应场景 prompt
def test_writer_prompt_injects_density_constraint():
    bp = _bp(2, ee="", delta="")
    tc = {
        "chapter_num": 50,
        "scene_blueprints": [
            _bp(1), _bp(2, ee="", delta=""),
        ],
        "_density_scene_constraints": {
            "2": "任务卡未给本场景规划明确的外部事件，严禁写成纯内心独白"},
    }
    prompt = build_scene_prompt(tc, bp, tc["scene_blueprints"])
    assert "开场事件密度硬约束" in prompt
    assert "严禁写成纯内心独白" in prompt
    # 其它场景不带该块
    p1 = build_scene_prompt(tc, _bp(1), tc["scene_blueprints"])
    assert "开场事件密度硬约束" not in p1


# ---------- CC round-11 R2：户外/公共场所豁免 ----------

def test_outdoor_locations_not_flagged_as_interior():
    """户外公共场景即使 external_event 为空，也不判 consecutive_interior_only。"""
    outdoor_bps = [
        _bp(1, ee="", loc="村口老槐树下", t="清晨"),
        _bp(2, ee="", loc="井台旁", t="上午"),
        _bp(3, ee="", loc="祠堂前", t="中午"),
        _bp(4, ee="", loc="巷道", t="傍晚"),
    ]
    r = od.assess_opening_density(outdoor_bps, 3)
    types = [v["type"] for v in r["hard_violations"]]
    assert "consecutive_interior_only" not in types


def test_still_indoor_interior_flagged():
    """四场确实都在室内且无外部事件，仍应判 consecutive_interior_only。"""
    indoor_bps = [
        _bp(1, ee="", loc="屋内", t="清晨"),
        _bp(2, ee="", loc="屋内", t="上午"),
        _bp(3, ee="", loc="帐内", t="中午"),
        _bp(4, ee="", loc="洞内", t="傍晚"),
    ]
    r = od.assess_opening_density(indoor_bps, 3)
    types = [v["type"] for v in r["hard_violations"]]
    assert "consecutive_interior_only" in types


def test_is_outdoor_scene_helper():
    assert od.is_outdoor_scene({"location": "村口老槐树下"})
    assert od.is_outdoor_scene({"location": "祠堂"})
    assert od.is_outdoor_scene({"location": "井台"})
    assert od.is_outdoor_scene({"location": "晒谷场"})
    assert od.is_outdoor_scene({"location": "田埂"})
    assert od.is_outdoor_scene({"location": "河边"})
    assert od.is_outdoor_scene({"location": "集市"})
    assert od.is_outdoor_scene({"location": "山坡"})
    assert od.is_outdoor_scene({"location": "城门"})
    assert not od.is_outdoor_scene({"location": "屋内"})
    assert not od.is_outdoor_scene({"location": "帐内"})
    assert not od.is_outdoor_scene({"location": "洞内"})
    assert not od.is_outdoor_scene({"location": "卧室"})
    assert not od.is_outdoor_scene({})
