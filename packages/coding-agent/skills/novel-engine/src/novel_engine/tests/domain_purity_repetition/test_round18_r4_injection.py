# -*- coding: utf-8 -*-
"""CC round-18 R4：chapter_director 多场任务卡 must_cover 注入测试。"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from novel_engine.agents.chapter_director import _inject_must_cover_opening_anchors


def test_r4_multi_scene_injects_opening_anchors():
    """C3 R4：scene_num>1 的场注入 opening_anchor must_cover beat。"""
    card = {
        "chapter_num": 3,
        "scene_blueprints": [
            {"scene_num": 1, "location": "茅屋"},
            {"scene_num": 2, "location": "井边"},
            {"scene_num": 3, "location": "院中"},
        ],
    }
    out = _inject_must_cover_opening_anchors(card)
    beats = out.get("must_cover_beats", [])
    anchor_beats = [b for b in beats if b.get("category") == "opening_anchor"]
    assert len(anchor_beats) == 2, f"Expected 2 anchors (scenes 2,3), got {len(anchor_beats)}"
    assert anchor_beats[0]["scene_num"] == 2
    assert anchor_beats[1]["scene_num"] == 3
    assert "井边" in anchor_beats[0]["beat_text"]
    assert "院中" in anchor_beats[1]["beat_text"]
    assert not any(b.get("scene_num") == 1 for b in anchor_beats)


def test_r4_single_scene_no_anchor():
    """C3 R4：单场任务卡不注入 opening_anchor。"""
    card = {
        "chapter_num": 1,
        "scene_blueprints": [{"scene_num": 1, "location": "村口"}],
    }
    out = _inject_must_cover_opening_anchors(card)
    assert out.get("must_cover_beats") is None


def test_r4_ch5_injects_confirmation_beat():
    """C3 R4：ch5 额外追加 confirmation beat（陈老根确认陆烬体弱对浊气敏感，严禁气感内视吐纳）。"""
    card = {
        "chapter_num": 5,
        "scene_blueprints": [
            {"scene_num": 1, "location": "茅屋"},
            {"scene_num": 2, "location": "井边"},
            {"scene_num": 3, "location": "院中"},
            {"scene_num": 4, "location": "屋内"},
        ],
    }
    out = _inject_must_cover_opening_anchors(card)
    beats = out.get("must_cover_beats", [])
    confirm_beats = [b for b in beats if b.get("category") == "confirmation"]
    assert len(confirm_beats) == 1, f"Expected 1 confirmation beat, got {len(confirm_beats)}"
    assert "陆烬体弱" in confirm_beats[0]["beat_text"]
    assert "浊气" in confirm_beats[0]["beat_text"]
    assert "气感" in confirm_beats[0]["beat_text"]
    assert "内视" in confirm_beats[0]["beat_text"]
    assert "吐纳" in confirm_beats[0]["beat_text"]
    anchor_beats = [b for b in beats if b.get("category") == "opening_anchor"]
    assert len(anchor_beats) == 3


def test_r4_ch5_confirmation_idempotent():
    """C3 R4：ch5 已有 confirmation beat 时不重复注入。"""
    card = {
        "chapter_num": 5,
        "scene_blueprints": [
            {"scene_num": 1, "location": "茅屋"},
            {"scene_num": 2, "location": "井边"},
        ],
        "must_cover_beats": [
            {"scene_num": None, "beat_text": "已有确认 beat", "category": "confirmation"},
        ],
    }
    out = _inject_must_cover_opening_anchors(card)
    beats = out.get("must_cover_beats", [])
    confirm_beats = [b for b in beats if b.get("category") == "confirmation"]
    assert len(confirm_beats) == 1, "Should not duplicate existing confirmation beat"
    assert "已有确认 beat" in confirm_beats[0]["beat_text"]


def test_r4_non_ch5_no_confirmation():
    """C3 R4：非 ch5 多场卡只注入 opening_anchor，不注入 confirmation。"""
    card = {
        "chapter_num": 4,
        "scene_blueprints": [
            {"scene_num": 1, "location": "茅屋"},
            {"scene_num": 2, "location": "井边"},
            {"scene_num": 3, "location": "院中"},
            {"scene_num": 4, "location": "屋内"},
        ],
    }
    out = _inject_must_cover_opening_anchors(card)
    beats = out.get("must_cover_beats", [])
    confirm_beats = [b for b in beats if b.get("category") == "confirmation"]
    anchor_beats = [b for b in beats if b.get("category") == "opening_anchor"]
    assert len(confirm_beats) == 0
    assert len(anchor_beats) == 3


# P8D D2: 软 beat 执行级测试
def test_d2_soft_opening_anchor_triggers_no_block(tmp_path):
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    from novel_engine.pipeline.chapter_journal import append_scene
    import json as _json
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "runtime_config.json").write_text(_json.dumps({"llm": {"use_mock": True}, "review_llm": {"use_mock": True}, "fallback_llm": {"use_mock": True}}), encoding="utf-8")
    (tmp_path / "config" / "llm_providers.json").write_text(_json.dumps({"active_profile": "test", "profiles": {"test": {"base_url": "https://test.example.com/v1", "api_key_env": "TEST_KEY_D2O", "timeout_s": 60, "max_retries": 1, "default_extra_body": {}, "phases": {"scenes": {"models": ["t"], "response_format": None}, "polish": {"models": ["t"], "response_format": None, "concurrency": 4}, "planning": {"models": ["t"], "response_format": None}, "review": {"models": ["t"], "response_format": None}}}}}), encoding="utf-8")
    import os as _os; _os.environ["TEST_KEY_D2O"] = "sk-test-d2o"
    (tmp_path / "config" / "forbidden.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "quality_policy.json").write_text(_json.dumps({"publication_line": 88, "soft_publication_line": 85, "min_ratio": 0.85, "max_ratio": 1.2, "tolerance_chars": 500}), encoding="utf-8")
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}
    card = {"chapter_num": 3, "core_goal": "test", "scene_blueprints": [
        {"scene_num": 1, "location": "茅屋", "characters": ["陈老根"], "goal": "安抚", "conflict": "夜啼", "emotion": "疲惫", "word_count_target": 1800},
        {"scene_num": 2, "location": "井边", "characters": ["陈老根"], "goal": "打水", "conflict": "浊气", "emotion": "警觉", "word_count_target": 2000},
        {"scene_num": 3, "location": "院中", "characters": ["陈老根"], "goal": "观察", "conflict": "沉静", "emotion": "疑惑", "word_count_target": 1800},
    ], "foreshadow_actions": [], "must_cover_beats": [
        {"scene_num": 2, "beat_text": "场景2开头须有明确时间词+地点锚点", "category": "opening_anchor"},
        {"scene_num": 3, "beat_text": "场景3开头须有明确时间词+地点锚点", "category": "opening_anchor"},
    ]}
    journal = {1: "夜色如墨。", 2: "陈老根打水遇村民。", 3: "陈老根观察陆烬。"}
    for sid, txt in journal.items():
        append_scene(tmp_path, 3, {"scene_id": sid, "scene_text": txt, "hook": "", "beats": []})
    mock_out = MagicMock(); mock_out.scene_text = "清晨，陈老根在井边打水，雾气弥漫。"; mock_out.beats = []; mock_out.hook = ""
    mock_writer = MagicMock(); mock_writer.generate_scene.return_value = mock_out
    orch.writer = mock_writer
    with patch("novel_engine.pipeline.pipeline_orchestrator.load_authoritative_scenes") as mock_load:
        mock_load.return_value = [{"scene_id": k, "scene_text": v} for k, v in journal.items()]
        with patch.object(orch, "writer", mock_writer):
            with patch.object(orch, "_enforce_word_count", side_effect=lambda n, lo, hi: n):
                result = orch._enforce_mandatory_beats(3, card, synopsis_text="")
    assert result.get("blocked") is not True, f"Soft beats must never hard-block, got {result}"
    assert mock_writer.generate_scene.called, "Soft regen must trigger writer"
