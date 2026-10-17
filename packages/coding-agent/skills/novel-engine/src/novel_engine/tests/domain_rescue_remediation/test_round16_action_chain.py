# -*- coding: utf-8 -*-
"""CC round-16 P0-3：beat 动作闭环（action_chain）检测。"""
from novel_engine.quality.density_gate import evaluate_action_chains, evaluate_scene_semantic


def _bp(chain):
    return {"concrete_events": [
        {"event": "陈老根端详婴儿额头纹路后收回手", "observable_action": "触近、停驻、收回",
         "action_chain": chain}]}


def test_action_chain_complete_when_all_nodes_present():
    scene = ("陈老根俯下身，指尖探近那道淡青色的叶脉纹路，在额前一寸处停驻片刻，"
             "终究没有触碰，随即缓缓收回了手。")
    out = evaluate_action_chains(_bp(["探近", "停驻", "收回"]), scene, {})
    assert len(out) == 1
    assert out[0]["ok"] is True
    assert out[0]["missing"] == []


def test_action_chain_incomplete_when_final_node_missing():
    # 真机 ch1：只悬停、按襁褓，缺“触近纹路”与“收回”闭环
    scene = ("陈老根的目光落在额间纹路上，指尖悬停在婴儿额前一寸的位置停驻片刻，"
             "最终没有落下，只是在襁褓的边缘轻轻一按。")
    out = evaluate_action_chains(_bp(["触碰纹路", "停驻", "收回"]), scene, {})
    assert len(out) == 1
    assert out[0]["ok"] is False
    assert out[0]["missing"]
    # 停驻在场（悬停/停了片刻），缺的是触碰与收回
    assert "停驻" not in out[0]["missing"]


def test_scene_without_action_chain_unaffected():
    scene = "村民们围着火把争执不休，陈老根走上前，枣木棍顿地，人群安静下来。"
    bp = {"concrete_events": [{"event": "陈老根镇住争执的村民", "observable_action": "顿棍"}]}
    assert evaluate_action_chains(bp, scene, {}) == []


def test_semantic_eval_rows_include_action_chain():
    cfg = {"coverage_threshold": 0.6}
    scene = "陈老根探近纹路，停驻片刻，随后收回手。"
    res = evaluate_scene_semantic(_bp(["探近", "停驻", "收回"]), scene, cfg, root="", arc=None)
    kinds = [r["kind"] for r in res["rows"]]
    assert "action_chain" in kinds
