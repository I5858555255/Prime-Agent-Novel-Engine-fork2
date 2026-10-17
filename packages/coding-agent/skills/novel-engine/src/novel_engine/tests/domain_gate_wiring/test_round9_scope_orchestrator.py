# -*- coding: utf-8 -*-
"""CC round-9 orchestrator scope 门接线测试（轻量假对象，不构造完整 Pipeline）。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_engine.agents.scene_schema import SceneOutput
from novel_engine.core.errors import ChapterResampleRequiredError
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
from novel_engine.quality import scope_gate

INFANT = {
    "arc": "infant", "chapters": [1, 5], "negation_window": 20,
    "negation_markers": ["不是", "并非"], "idiom_whitelist": ["魂飞魄散"],
"hard_block": ["守护者", "魂魄印记", "仙门", "筑基", "吐纳", "调息", "气感", "内视", "行气探查", "气机探查"],
    "soft_warn": ["修炼", "境界"],
    "night_anchor_markers": ["当夜", "数时辰"],
    "dawn_markers": ["天亮", "鱼肚白"],
    "future_markers": ["等", "等到", "再说"],
}


@pytest.fixture()
def harness(tmp_path: Path):
    d = tmp_path / "config" / "leak_terms"
    d.mkdir(parents=True)
    (d / "infant.json").write_text(json.dumps(INFANT, ensure_ascii=False), encoding="utf-8")
    scope_gate.reset_config_cache()
    bps = [
        {"scene_num": 1, "sequence_index": 1},
        {"scene_num": 2, "sequence_index": 2},
    ]
    card = {"chapter_num": 1, "scene_blueprints": bps,
            "timeline_anchor": {"max_time_progression": "数时辰内"}}
    yield tmp_path, card
    scope_gate.reset_config_cache()


def _run(fake, card, scenes, assembled=""):
    return PipelineOrchestrator._run_scope_gate(fake, 1, card, scenes, assembled)


def _fake(root):
    f = SimpleNamespace(root=root, config={},
                        writer=SimpleNamespace(last_scenes=None),
                        _chapter_gate_fired=False)
    return f


def test_extra_scene_count_is_hard_block(harness):
    root, card = harness
    scenes = [SceneOutput(1, "甲", "", []), SceneOutput(2, "乙", "", []),
              SceneOutput(3, "丙", "", [])]  # 3 > 规划 2
    f = _fake(root)
    with pytest.raises(ChapterResampleRequiredError) as ei:
        _run(f, card, scenes)
    assert ei.value.replan == "scope_block"


def test_hard_leak_regenerated_and_resolved(harness):
    root, card = harness
    scenes = [
        SceneOutput(1, "陈老根在雾中蹲下身查看焦土。", "", []),
        SceneOutput(2, "雾里响起守护者的低语，挥之不去。", "", []),
    ]
    clean = SceneOutput(2, "他抱起裹好麻布的婴儿，转身沿山道往村里走。", "", [])
    f = _fake(root)
    f._regen_scene_for_id = lambda *a, **k: clean
    out = _run(f, card, scenes, "assembled")
    assert "守护者" not in out
    assert f._chapter_gate_fired is True
    # 被修场景已替换为干净文本
    s2 = next(s for s in scenes if s.scene_id == 2)
    assert "守护者" not in s2.scene_text


def test_persistent_leak_raises_scope_block(harness):
    root, card = harness
    scenes = [
        SceneOutput(1, "陈老根在雾中蹲下身。", "", []),
        SceneOutput(2, "雾里响起守护者的低语。", "", []),
    ]
    still_bad = SceneOutput(2, "那守护者又借魂魄印记传念。", "", [])
    f = _fake(root)
    f._regen_scene_for_id = lambda *a, **k: still_bad
    with pytest.raises(ChapterResampleRequiredError) as ei:
        _run(f, card, scenes)
    assert ei.value.replan == "scope_block"
    assert 2 in (ei.value.scene_ids or [])


def test_clean_chapter_no_gate_fired(harness):
    root, card = harness
    scenes = [
        SceneOutput(1, "陈老根借着雾气摸到村口外的焦土。", "", []),
        SceneOutput(2, "他抱起婴儿，麻布裹紧，深一脚浅一脚往回走。", "", []),
    ]
    f = _fake(root)
    f._regen_scene_for_id = lambda *a, **k: pytest.fail("clean chapter must not regen")
    out = _run(f, card, scenes, "assembled")
    assert out == "assembled"
    assert f._chapter_gate_fired is False
def _make_fake_with_regen(root, regen_return):
    """构建 fake orchestrator，_regen_scene_for_id 始终返回 regen_return。"""
    f = _fake(root)
    f._regen_scene_for_id = lambda *a, **k: regen_return
    return f


def test_deterministic_replace_tuna_to_adjust_breath(harness):
    """Ch9 根因回归：重生后 LLM 仍产禁词"吐纳" → 确定性替换"调整呼吸"生效。"""
    root, card = harness
    scenes = [
        SceneOutput(1, "陈老根盘膝而坐，缓缓吐纳，气息绵长。", "", []),
    ]
    regenerated = SceneOutput(1, "陈老根盘膝而坐，缓缓吐纳，气息绵长。", "", [])
    f = _make_fake_with_regen(root, regenerated)
    out = _run(f, card, scenes)
    assert "吐纳" not in out
    s1 = next(s for s in scenes if s.scene_id == 1)
    assert "调整呼吸" in s1.scene_text
    assert "吐纳" not in s1.scene_text


def test_deterministic_replace_tiaoxi_to_adjust_breath(harness):
    """Ch9 根因回归：重生后 LLM 仍产禁词"调息" → 确定性替换"调整呼吸"生效。"""
    root, card = harness
    scenes = [
        SceneOutput(1, "陈老根在深夜里调息，气息绵长规律。", "", []),
    ]
    regenerated = SceneOutput(1, "陈老根在深夜里调息，气息绵长规律。", "", [])
    f = _make_fake_with_regen(root, regenerated)
    out = _run(f, card, scenes)
    assert "调息" not in out
    s1 = next(s for s in scenes if s.scene_id == 1)
    assert "调整呼吸" in s1.scene_text


def test_deterministic_replace_qigan_to_inner_anomaly(harness):
    """Ch9 根因回归：重生后 LLM 仍产禁词"气感" → 确定性替换"体内异样"生效。"""
    root, card = harness
    scenes = [
        SceneOutput(1, "婴儿体内气感涌动，仿佛有暖流游走。", "", []),
    ]
    regenerated = SceneOutput(1, "婴儿体内气感涌动，仿佛有暖流游走。", "", [])
    f = _make_fake_with_regen(root, regenerated)
    out = _run(f, card, scenes)
    assert "气感" not in out
    s1 = next(s for s in scenes if s.scene_id == 1)
    assert "体内异样" in s1.scene_text


def test_deterministic_replace_neishi_to_neiguan(harness):
    """Ch9 根因回归：重生后 LLM 仍产禁词"内视" → 确定性替换"内观"生效。"""
    root, card = harness
    scenes = [
        SceneOutput(1, "陈老根闭目内视，感知体内气息流动。", "", []),
    ]
    regenerated = SceneOutput(1, "陈老根闭目内视，感知体内气息流动。", "", [])
    f = _make_fake_with_regen(root, regenerated)
    out = _run(f, card, scenes)
    assert "内视" not in out
    s1 = next(s for s in scenes if s.scene_id == 1)
    assert "内观" in s1.scene_text


def test_deterministic_replace_avoids_hard_block_targets(harness):
    """Ch9 根因回归：所有确定性替换目标本身不得是 hard_block 词。

    旧映射"吐纳"→"调息"失败因为"调息"也是 hard_block。
    修复后目标词（调整呼吸/体内异样/内观/感知气息）均非禁词，
    复检不应再次触发 hard。
    """
    root, card = harness
    # 使用2个场景测试多种替换
    scenes = [
        SceneOutput(1, "他缓缓吐纳，气息绵长。", "", []),
        SceneOutput(2, "婴儿体内气感涌动。", "", []),
    ]
    # 模拟重生后仍含禁词，触发替换
    regenerated1 = SceneOutput(1, "他缓缓吐纳，气息绵长。", "", [])
    regenerated2 = SceneOutput(2, "婴儿体内气感涌动。", "", [])
    regen_count = [0]
    def fake_regen(task_card, sid, scenes_list, fix_directive='', frequency_penalty=0.4, presence_penalty=0.4):
        regen_count[0] += 1
        return regenerated1 if sid == 1 else regenerated2
    f = _fake(root)
    f._regen_scene_for_id = fake_regen
    _run(f, card, scenes)
    s1, s2 = scenes[0], scenes[1]
    assert "吐纳" not in s1.scene_text
    assert "调整呼吸" in s1.scene_text
    assert "气感" not in s2.scene_text
    assert "体内异样" in s2.scene_text
