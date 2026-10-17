# -*- coding: utf-8 -*-
"""CC round-7 P0-2 rescue ladder + chapter-level resample breaker (orchestrator)."""
import json
import random
import tempfile
from pathlib import Path

import pytest

from novel_engine.agents.scene_schema import SceneOutput, build_scene_prompt
from novel_engine.core.errors import ChapterResampleRequiredError
from novel_engine.pipeline.pipeline_orchestrator import (
    PipelineOrchestrator, _normalize_blueprint_beats,
)


def _setup_orch(writer):
    tmp = tempfile.mkdtemp()
    Path(tmp, "config").mkdir(parents=True, exist_ok=True)
    Path(tmp, "config", "runtime_config.json").write_text(
        json.dumps({"llm": {"use_mock": True}, "chapter_target_chars": 10000}),
        encoding="utf-8")
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.root = tmp
    orch.writer = writer
    orch._current_synopsis_text = ""
    return orch


def _card():
    return {
        "chapter_num": 1,
        "timeline_anchor": {"max_time_progression": "当日"},
        "scene_blueprints": [
            {"scene_num": i, "goal": f"目标{i}", "location": "雾隐村",
             "characters": ["陆烬", "陈老根"], "beats": ["b1", "b2", "b3"]}
            for i in (1, 2, 3)
        ],
    }


_POOL = "的一是在了不人有我他这中大来上个国地地道说也子就去得那要下生会自之着过子时年出么" * 8


def _valid_text(seed: int, n: int = 1600) -> str:
    rng = random.Random(seed)
    chars = [rng.choice(_POOL) for _ in range(n)]
    # 加句号分隔，避免无标点；末尾以句号收尾
    for k in range(40, n - 1, 47):
        chars[k] = "。"
    return "".join(chars).rstrip("。") + "。"


class _Writer:
    def __init__(self, responder):
        self.calls = 0
        self.last_kwargs = None
        self._responder = responder

    def generate_scene(self, task_card, bp, syn, **kw):
        self.calls += 1
        self.last_kwargs = kw
        return self._responder(bp)


def _valid_scene(sid: int, seed: int, n: int = 2400) -> SceneOutput:
    # CC round-21：达标场景需 ≥ 该卡单场目标的 0.7（10000/3→3333，0.7≈2333），否则会
    # 触发新增的"初稿偏短源头重生"一级，干扰本文件对近空/重复/连贯短稿阶梯的聚焦断言。
    return SceneOutput(sid, _valid_text(seed, n=n), "章末钩子悬念。", ["b1", "b2", "b3"],
                       beats_covered=["c1", "c2", "c3"], structured=True, finish_reason="stop")


def test_normalize_blueprint_beats_coerces_objects():
    card = {"scene_blueprints": [{"scene_num": 1, "beats": [
        {"beat": "动作一"}, {"desc": "动作二"}, "纯字符串", 123, None, {"foo": "x"},
    ]}]}
    _normalize_blueprint_beats(card)
    beats = card["scene_blueprints"][0]["beats"]
    assert beats[0] == "动作一" and beats[1] == "动作二" and beats[2] == "纯字符串"
    assert beats[3] == "123"
    assert json.loads(beats[4]) == {"foo": "x"}
    # build_scene_prompt must not crash on the normalized card
    bp = card["scene_blueprints"][0]
    p = build_scene_prompt(card, bp, card["scene_blueprints"])
    assert "动作一" in p and isinstance(p, str)


def test_three_structured_near_empty_no_longer_immediately_resample_cc25():
    # CC round-25 P0-1：>=3 近空不再"零调用立即整章重排"，而是先跑 L1 单场跨温度重试；
    # L1 把三场都救回即正常通过、不 HALT（整章重排门槛已提高到"L2/L3 耗尽后仍≥2 场"）。
    writer = _Writer(lambda bp: _valid_scene(int(bp["scene_num"]), seed=int(bp["scene_num"]) * 7))
    orch = _setup_orch(writer)
    scenes = [SceneOutput(i, "近空。", "", [], beats_covered=[], structured=True)
              for i in (1, 2, 3)]
    orch._validate_and_regen_scenes(scenes, _card(), 1)  # 不再抛 ChapterResampleRequiredError
    assert writer.calls == 3


def test_unstructured_short_scenes_do_not_trigger_cluster():
    # mock/散文回退路径：不进入章级熔断（保持单场有界重生语义）
    state = {"n": 0}

    def responder(bp):
        state["n"] += 1
        return SceneOutput(int(bp["scene_num"]), "近空。", "", [], structured=False)

    writer = _Writer(responder)
    orch = _setup_orch(writer)
    from novel_engine.core.errors import SceneUnrecoverableError
    scenes = [SceneOutput(i, "近空。", "", [], structured=False) for i in (1, 2, 3)]
    with pytest.raises(SceneUnrecoverableError):
        orch._validate_and_regen_scenes(scenes, _card(), 1)


def test_single_near_empty_rescue_uses_cross_temp_first():
    writer = _Writer(lambda bp: _valid_scene(int(bp["scene_num"]), seed=11))
    orch = _setup_orch(writer)
    scenes = [
        SceneOutput(1, "近空。", "", [], beats_covered=[], structured=True),
        _valid_scene(2, seed=22),
        _valid_scene(3, seed=33),
    ]
    orch._validate_and_regen_scenes(scenes, _card(), 1)
    assert writer.calls == 1
    kw = writer.last_kwargs
    # CC round-23 P0-3：近空/摆烂稿首次救援走跨温度 0.7（旧 CC7 为默认温度 None）
    assert kw["temperature_override"] == 0.7
    assert kw["frequency_penalty"] == 0.4
    assert kw["presence_penalty"] == 0.4
    assert kw.get("negative_examples")
    assert "完整正文" in (kw.get("fix_directive") or "")


def test_self_repeating_slacker_rescued_with_low_temp_strong_penalty():
    block = "他提起油灯推开木门冷雾扑面而来带着陈年霉味"  # 20 chars, repeats below
    repeated = block + block + "。"
    writer = _Writer(lambda bp: _valid_scene(int(bp["scene_num"]), seed=44))
    orch = _setup_orch(writer)
    scenes = [
        SceneOutput(1, repeated, "", [], beats_covered=[], structured=True),
        _valid_scene(2, seed=55),
        _valid_scene(3, seed=66),
    ]
    orch._validate_and_regen_scenes(scenes, _card(), 1)
    assert writer.calls == 1
    kw = writer.last_kwargs
    assert kw["temperature_override"] == 0.7
    assert kw["frequency_penalty"] == 0.75
    assert kw["presence_penalty"] == 0.6


def test_coherent_short_defers_to_expansion_without_penalty():
    # 560-char 连贯短稿（无任何结构硬伤，仅低于 0.4 字数门）：中性重生两次仍短 → 交有界扩写，不判死
    coherent = _valid_text(77, n=560)
    writer = _Writer(lambda bp: SceneOutput(
        int(bp["scene_num"]), coherent, "钩子。", ["b1", "b2", "b3"],
        beats_covered=["c1", "c2", "c3"], structured=True, finish_reason="stop"))
    orch = _setup_orch(writer)
    scenes = [
        SceneOutput(1, coherent, "", [], beats_covered=["c1", "c2", "c3"], structured=True),
        _valid_scene(2, seed=88),
        _valid_scene(3, seed=99),
    ]
    orch._validate_and_regen_scenes(scenes, _card(), 1)  # must NOT raise
    assert writer.calls == 2
    kw = writer.last_kwargs
    # 连贯短文走中性参数，避免 penalty 诱发空/乱码
    assert kw["temperature_override"] is None
    assert kw["frequency_penalty"] is None
    assert scenes[0].scene_text == coherent
