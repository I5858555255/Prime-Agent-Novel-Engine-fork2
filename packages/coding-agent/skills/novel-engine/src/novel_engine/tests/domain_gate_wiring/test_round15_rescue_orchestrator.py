# -*- coding: utf-8 -*-
"""CC round-15：structured 场景初稿韧性聚合——单个无法修复不 HALT，>=2 才整章重采。"""
import pytest

from novel_engine.agents.scene_schema import SceneOutput
from novel_engine.core.errors import ChapterResampleRequiredError

from novel_engine.tests.domain_gate_wiring.test_round7_rescue_ladder import (
    _setup_orch, _card, _valid_scene, _Writer,
)


def _empty_structured(sid: int) -> SceneOutput:
    # flash 摆烂：极短、无句末、finish_reason=stop → HARD/MALFORMED，无法自动修复
    return SceneOutput(sid, "近空", "", [], beats_covered=[],
                       structured=True, finish_reason="stop")


def test_single_structured_empty_scene_does_not_halt():
    writer = _Writer(lambda bp: _empty_structured(int(bp["scene_num"])))
    orch = _setup_orch(writer)
    scenes = [
        _empty_structured(1),
        _valid_scene(2, seed=22),
        _valid_scene(3, seed=33),
    ]
    # 仅 1 个无法修复：不抛错，章继续（交下游/人工标记）
    orch._validate_and_regen_scenes(scenes, _card(), 1)


def test_two_plus_structured_empty_scenes_resample_chapter():
    writer = _Writer(lambda bp: _empty_structured(int(bp["scene_num"])))
    orch = _setup_orch(writer)
    scenes = [_empty_structured(1), _empty_structured(2), _valid_scene(3, seed=33)]
    with pytest.raises(ChapterResampleRequiredError) as ei:
        orch._validate_and_regen_scenes(scenes, _card(), 1)
    assert set(ei.value.scene_ids) == {1, 2}
