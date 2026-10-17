# -*- coding: utf-8 -*-
"""CC round-21 offline tests：单场景目标下限 + 不提交章分数主导路由。"""
from novel_engine.core.quality_policy import derive_scene_targets
from novel_engine.quality.noncommit_routing import route_noncommitted


# ---------- P1 单场景目标下限 ----------

def test_scene_target_floor_raises_short_chapter():
    # chapter 8000 / 4 = 2000，但被 2150 下限抬升（只升不降）
    assert derive_scene_targets(8000, 4) == [2150, 2150, 2150, 2150]


def test_scene_target_unchanged_when_share_above_floor():
    assert derive_scene_targets(10000, 4) == [2500, 2500, 2500, 2500]
    assert derive_scene_targets(10000, 3) == [3333, 3333, 3334]


def test_scene_target_explicit_floor():
    out = derive_scene_targets(8000, 4, scene_floor=2200)
    assert out == [2200, 2200, 2200, 2200]


# ---------- Q2 路由：分数为主判据 ----------

def test_case1_length_ok_but_score_below_line_gap():
    assert route_noncommitted(80.0, chars=7000) == ("gap", "quality_below_line")


def test_case4_severe_and_below_line_gap_with_reason():
    assert route_noncommitted(80.8, chars=5544, severe_shortfall=True) == \
        ("gap", "shortfall_and_quality_below_line")


def test_case5_topup_range_but_below_line_still_gap():
    assert route_noncommitted(84.0, chars=6000) == ("gap", "quality_below_line")


def test_gray_band_below_publication_gap_continues_unattended():
    # CC round-23：85<=分<88 灰带仍不可出版，但无人值守下不再 HALT 停批 -> 隔离+继续
    assert route_noncommitted(85.0, chars=6000) == ("gap", "gray_band_below_publication")
    assert route_noncommitted(87.05, chars=7000) == ("gap", "gray_band_below_publication")


def test_at_or_above_publication_uncommitted_conservative_halt():
    # 分>=出版线88却仍未提交，才是真正的上游异常 -> HALT 留人排查
    assert route_noncommitted(88.0, chars=6000)[0] == "halt"
    assert route_noncommitted(90.0, chars=5000, severe_shortfall=True)[0] == "halt"


def test_unparseable_score_conservative_halt():
    assert route_noncommitted("N/A", chars=5000, severe_shortfall=True)[0] == "halt"
    assert route_noncommitted(None)[0] == "halt"


# ---------- 终局 gap 不在进程内整章重试（r21 真机发现的翻倍调用缺陷） ----------

def _orch_with_stub_gen(returned):
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.config = {"pipeline": {"max_review_retries": 3}}
    calls = {"n": 0}

    def _gen(ch):
        calls["n"] += 1
        return returned
    orch.generate_single_chapter = _gen
    return orch, calls


def test_final_gate_blocked_returns_without_replan_retry():
    orch, calls = _orch_with_stub_gen(
        {"success": False, "final_gate_blocked": True,
         "final_gate_violations": [{"kind": "punctuation"}]})
    r = orch._run_with_retry(1)
    assert r["final_gate_blocked"] is True
    assert calls["n"] == 1  # 绝不整章重排重试


def test_quality_gap_continue_returns_without_replan_retry():
    orch, calls = _orch_with_stub_gen(
        {"success": False, "quality_replan": True, "quality_gap_continue": True,
         "gap_kind": "continuity"})
    r = orch._run_with_retry(1)
    assert r["quality_gap_continue"] is True
    assert calls["n"] == 1
