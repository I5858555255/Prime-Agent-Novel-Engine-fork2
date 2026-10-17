# -*- coding: utf-8 -*-
"""CC round-12 A：dawn_overrun 确定性整句切除，与 hard_leak 的 LLM 重生分轨。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_engine.quality import scope_gate
from novel_engine.quality.scope_gate import (
    detect_scope_violations,
    excise_dawn_overrun,
    scope_fix_directive,
)
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

INFANT = {
    "arc": "infant",
    "chapters": [1, 5],
    "negation_window": 20,
    "negation_markers": ["不是", "等", "等到", "直到", "尚未"],
    "idiom_whitelist": ["魂飞魄散"],
    "hard_block": ["魂魄印记", "跨界", "守护者", "经脉"],
    "soft_warn": ["修炼"],
    "night_anchor_markers": ["子时", "当夜", "数时辰"],
    "dawn_markers": ["天亮", "黎明", "鸡鸣", "晨光"],
    "future_markers": ["等", "等到", "直到", "再说", "尚未"],
}
NIGHT_ANCHOR = {"chapter_start_marker": "出生当夜", "max_time_progression": "数时辰内"}
DAY_ANCHOR = {"chapter_start_marker": "次日清晨", "max_time_progression": "当日"}


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    d = tmp_path / "config" / "leak_terms"
    d.mkdir(parents=True)
    (d / "infant.json").write_text(json.dumps(INFANT, ensure_ascii=False), encoding="utf-8")
    scope_gate.reset_config_cache()
    yield tmp_path
    scope_gate.reset_config_cache()


def test_excise_removes_dawn_sentence_keeps_exempt(root):
    txt = ("陆烬屏息听着夜色。\n\n"
           "远处鸡鸣声越来越密，晨光漫过石径。\n\n"
           "陈老根低声道：“等天亮再走。”他尚未动身。")
    before = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert "鸡鸣" in {v["term"] for v in before["hard"]}
    out, removed = excise_dawn_overrun(txt, 1, root, NIGHT_ANCHOR)
    assert any("鸡鸣" in s for s in removed)
    assert "鸡鸣" not in out and "晨光" not in out
    assert "等天亮" in out          # 引号对话豁免
    assert "尚未动身" in out         # 将来时豁免
    assert "陆烬屏息听着夜色" in out
    after = detect_scope_violations(out, 1, NIGHT_ANCHOR, root)
    assert not [v for v in after["hard"] if v["kind"] == "dawn_overrun"]


def test_excise_noop_for_day_anchor(root):
    txt = "晨光铺满院子，鸡鸣阵阵。"
    out, removed = excise_dawn_overrun(txt, 1, root, DAY_ANCHOR)
    assert out == txt and removed == []


def test_directive_drops_dawn_when_only_leak():
    # dawn 走切除，hard_leak 的重生 directive 不应再点名黎明词
    hard = [{"kind": "dawn_overrun", "term": "鸡鸣"},
            {"kind": "hard_leak", "term": "经脉"}]
    d = scope_fix_directive([h for h in hard if h["kind"] != "dawn_overrun"])
    assert "经脉" in d and "鸡鸣" not in d


def _make_fake_orch(root):
    """只绑定 _run_scope_gate 所需属性的轻量对象。"""
    o = SimpleNamespace()
    o.root = root
    o._chapter_gate_fired = False
    o._regen_calls = []
    o.writer = SimpleNamespace(last_scenes=None)

    def _fake_regen(task_card, sid, scenes, fix_directive=None,
                    frequency_penalty=None, presence_penalty=None):
        o._regen_calls.append((sid, fix_directive))
        # 返回一个清除了 hard_leak 但仍带 dawn 词的新场景（验证 dawn 交切除而非重生）
        return SimpleNamespace(scene_id=sid,
                               scene_text="他在夜色里稳住呼吸。远处鸡鸣又起，晨光微露。",
                               hook="h", beats=["b1", "b2", "b3"])
    o._regen_scene_for_id = _fake_regen
    o._run_scope_gate = PipelineOrchestrator._run_scope_gate.__get__(o, PipelineOrchestrator)
    return o


def _scene(sid, text):
    return SimpleNamespace(scene_id=sid, scene_text=text, hook="h",
                           beats=["b1", "b2", "b3"])


def test_gate_dawn_only_excises_without_regen(root, monkeypatch):
    monkeypatch.setattr(
        "novel_engine.pipeline.pipeline_orchestrator.append_scene",
        lambda *a, **k: None)
    monkeypatch.setattr(
        "novel_engine.pipeline.pipeline_orchestrator.purify_novel_for_publish",
        lambda t, chapter_num=None: t)
    o = _make_fake_orch(root)
    scenes = [_scene(1, "夜很静。\n\n远处鸡鸣声密，晨光渐起，天快亮了。")]
    task_card = {"timeline_anchor": NIGHT_ANCHOR,
                 "scene_blueprints": [{"scene_num": 1}]}
    out = o._run_scope_gate(1, task_card, scenes, "ASSEMBLED")
    assert o._regen_calls == []          # dawn-only 不触发 LLM 重生
    assert "鸡鸣" not in scenes[0].scene_text and "晨光" not in scenes[0].scene_text
    assert "夜很静" in out
    assert o._chapter_gate_fired is True


def test_gate_hard_leak_regens_then_excises_residual_dawn(root, monkeypatch):
    monkeypatch.setattr(
        "novel_engine.pipeline.pipeline_orchestrator.append_scene",
        lambda *a, **k: None)
    monkeypatch.setattr(
        "novel_engine.pipeline.pipeline_orchestrator.purify_novel_for_publish",
        lambda t, chapter_num=None: t)
    o = _make_fake_orch(root)
    scenes = [_scene(1, "他感应到经脉中有灵息，远处鸡鸣声密。")]
    task_card = {"timeline_anchor": NIGHT_ANCHOR,
                 "scene_blueprints": [{"scene_num": 1}]}
    out = o._run_scope_gate(1, task_card, scenes, "ASSEMBLED")
    assert len(o._regen_calls) == 1      # hard_leak 触发一次重生
    # 重生返回稿残留 dawn → 被确定性切除，章节放行而非 scope_block
    assert "鸡鸣" not in scenes[0].scene_text
    assert "稳住呼吸" in out
