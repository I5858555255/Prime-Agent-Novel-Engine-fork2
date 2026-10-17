# -*- coding: utf-8 -*-
"""CC round-22：continuity seed 对“任务卡已授权怀抱/承接上一章持有”不得误报倒置。"""
from novel_engine.quality.continuity_gate import build_specs, detect_inversions


def _bp(sid, seq, goal="", beats=None):
    return {"scene_num": sid, "sequence_index": seq, "goal": goal, "beats": beats or []}


def _scene(sid, text):
    return {"scene_id": sid, "scene_text": text}


def test_authorized_carrying_in_blueprint_not_inversion():
    # 真机 r24 ch1：承接上一章末已抱起，首场任务卡明确“抱着襁褓归途”，正文按卡演出。
    # 虽然后面场景提到捡/放下，也不得判时序倒置。
    bps = {
        1: _bp(1, 1, goal="陈老根抱婴连夜回村",
               beats=["陈老根抱着襁褓快步穿过雾气未散的村道",
                      "村口汉子借着月光认出陈老根怀中的襁褓"]),
        2: _bp(2, 2, goal="进屋安置弃婴",
               beats=["将襁褓放在炕上", "温水化干粮喂入婴儿口中"]),
        3: _bp(3, 3, goal="村长闻讯而来",
               beats=["村长质问这雾中捡来的孩子该如何处置"]),
    }
    scenes = [
        _scene(1, "陈老根抱着那襁褓走得急，鞋底踏在湿石板上。有人借着月光认出他怀中的襁褓。"),
        _scene(2, "他推开木门，把襁褓轻轻放在炕上，又舀起一勺米糊喂到婴儿嘴边。"),
        _scene(3, "天刚亮，村长便推门进来，盯着炕上的孩子。"),
    ]
    inv = detect_inversions(build_specs(scenes, bps))
    assert inv == []


def test_true_pre_discovery_holding_still_detected():
    # 真倒置：早场景蓝图只写围观/等待，正文却让他人已怀抱（更晚场景才首次发现抱起）。
    bps = {
        1: _bp(1, 1, goal="紫雾凝婴坠地", beats=["异象降生"]),
        2: _bp(2, 2, goal="次日村民围观", beats=["议论不祥"]),
        3: _bp(3, 3, goal="陈老根当夜入雾发现婴儿并抱回", beats=["入雾", "发现婴儿", "抱回茅屋"]),
    }
    scenes = [
        _scene(1, "紫光散去，焦土上一个灰布婴儿啼哭，周围并无一人。"),
        _scene(2, "鸡鸣初响，村民围拢，婴儿被母亲抱在怀里哄着。"),
        _scene(3, "陈老根拄杖入雾，在灰烬中发现并抱起那婴儿。"),
    ]
    inv = detect_inversions(build_specs(scenes, bps))
    assert any(i["scene_id"] == 2 for i in inv)
    assert not any(i["scene_id"] == 1 for i in inv)
