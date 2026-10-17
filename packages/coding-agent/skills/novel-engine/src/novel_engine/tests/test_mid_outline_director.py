# -*- coding: utf-8 -*-
"""中纲（章级意图）接入 Director 的 hermetic 测试。

- approved 中纲条目必须进入 skeleton prompt（强约束）；
- draft 中纲条目不得被使用（必须经 audit 校验通过标记 approved）；
- 无中纲条目时退回原有松散参考逻辑。
零 LLM，fake LLM 捕获 prompt。
"""
from __future__ import annotations

import json
from typing import Any

from novel_engine.agents.chapter_director import ChapterDirector

SAMPLE_EVENT_25 = "陈老根带陆烬第一次进县城赶集，偶遇猎户老周，得知山中妖兽增多"
SAMPLE_NODE = {"chapter_target": 43, "description": "陆烬体内呼吸法运转时与迷雾方向隐隐共鸣",
               "type": "main_plot"}


class _FakeLLM:
    """假 LLM：记录 user prompt，按调用序号返回 4 场景骨架（skeleton 调用）。"""

    def __init__(self) -> None:
        self.captured_prompts: list[str] = []
        self._call_count = 0

    def chat_completion(self, messages: list[dict], **kwargs: Any) -> dict:
        user_prompt = ""
        for m in messages:
            if m.get("role") == "user":
                user_prompt = m.get("content", "")
        self.captured_prompts.append(user_prompt)
        self._call_count += 1
        n = self._call_count
        if n == 1:
            body = {
                "scene_blueprints": [
                    {"scene_num": i, "narrative_time": "清晨", "location": "村口",
                     "characters": ["C001", "C002"], "goal": "推进剧情", "conflict": "新冲突",
                     "emotion": "平静", "beats": ["发生一件事", "产生反应", "留下钩子"]}
                    for i in (1, 2, 3, 4)
                ]
            }
        else:
            body = {"scene_blueprints": []}
        return {"content": json.dumps(body, ensure_ascii=False), "finish_reason": "stop", "_usage": {}}


def _write_mid_outline(tmp_path, status: str, chapters: dict | None = None,
                       filename: str = "mid_outline_V01_ch21-50.json") -> None:
    d = tmp_path / "config" / "planning"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "meta": {"vid": "V01", "range": [21, 50], "status": status},
        "entries": chapters or {},
    }
    (d / filename).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _build_director(tmp_path, with_plot: bool = True):
    (tmp_path / "config" / "planning").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "planning" / "volumes.json").write_text(
        json.dumps({"volumes": [{"id": "V01", "chapter_range": [1, 316]}]},
                   ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config" / "planning" / "plot_graph.json").write_text(
        json.dumps({"nodes": [SAMPLE_NODE] if with_plot else []},
                   ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config" / "foreshadow").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "foreshadow" / "registry.json").write_text(
        '{"foreshadows": []}', encoding="utf-8")
    fake = _FakeLLM()
    director = ChapterDirector(tmp_path, llm_client=fake)
    return director, fake


def test_approved_mid_outline_used_in_skeleton_prompt(tmp_path):
    """approved 中纲条目必须出现在 skeleton prompt 中（强约束）。"""
    _write_mid_outline(tmp_path, "approved",
                       {"25": {"chapter": 25, "core_event": SAMPLE_EVENT_25,
                               "characters": ["C001", "C002"], "boundary_note": None}})
    director, fake = _build_director(tmp_path)
    ctx, prior = director._build_shared_context(25)
    assert ctx["mid_outline_hint"] != "", "approved entry should produce a hint"
    assert SAMPLE_EVENT_25 in ctx["mid_outline_hint"]
    director._call_scene_skeleton(25, ctx, prior)
    prompt0 = fake.captured_prompts[0]
    assert "中纲" in prompt0, "skeleton prompt should mention mid-outline"
    assert SAMPLE_EVENT_25 in prompt0, "skeleton prompt must contain the approved mid-outline intent"


def test_draft_mid_outline_not_used(tmp_path):
    """draft（未校验通过）中纲条目不得注入 prompt，退回松散参考。"""
    _write_mid_outline(tmp_path, "draft",
                       {"25": {"chapter": 25, "core_event": SAMPLE_EVENT_25,
                               "characters": ["C001", "C002"], "boundary_note": None}})
    director, fake = _build_director(tmp_path)
    entry = director._load_mid_outline_entry(25)
    assert entry is None, "draft entries must not be consumed"
    ctx, prior = director._build_shared_context(25)
    assert ctx["mid_outline_hint"] == "", "draft entry must not produce a hint"
    director._call_scene_skeleton(25, ctx, prior)
    prompt0 = fake.captured_prompts[0]
    assert SAMPLE_EVENT_25 not in prompt0, "draft intent leaked into prompt"


def test_no_mid_outline_falls_back_to_loose(tmp_path):
    """无任何 mid_outline 文件时退回原有松散参考（hint 为空，节点仍注入）。"""
    director, fake = _build_director(tmp_path)
    entry = director._load_mid_outline_entry(25)
    assert entry is None
    ctx, prior = director._build_shared_context(25)
    assert ctx["mid_outline_hint"] == ""
    assert ctx["relevant_plot_nodes"], "loose reference (nearby nodes) must still be present"
    director._call_scene_skeleton(25, ctx, prior)
    prompt0 = fake.captured_prompts[0]
    assert "中纲" in prompt0  # 占位说明文案存在
    assert "暂无 approved 中纲条目" in prompt0


def test_mid_outline_file_status_not_approved_any_variant(tmp_path):
    """status 为任意非 approved 值（如 missing/）均不可用。"""
    _write_mid_outline(tmp_path, "missing",
                       {"25": {"chapter": 25, "core_event": SAMPLE_EVENT_25,
                               "characters": [], "boundary_note": None}})
    director, _ = _build_director(tmp_path)
    assert director._load_mid_outline_entry(25) is None


def _entry(ev: str) -> dict:
    return {"chapter": 25, "core_event": ev, "characters": ["C001", "C002"],
            "boundary_note": None}


def test_entry_status_approved_used_when_file_draft(tmp_path):
    """逐条门禁：文件级 draft + entry.status=approved → 可用（挂起其余章不阻塞）。"""
    _write_mid_outline(tmp_path, "draft",
                       {"25": _entry(SAMPLE_EVENT_25) | {"status": "approved"},
                        "26": _entry("陆烬学着陈老根的样子吐纳") | {"status": "needs_regenerate"}})
    director, fake = _build_director(tmp_path)
    e25 = director._load_mid_outline_entry(25)
    assert e25 is not None, "approved entry must be usable even when file is draft"
    assert director._load_mid_outline_entry(26) is None, "needs_regenerate entry must be skipped"
    ctx, prior = director._build_shared_context(25)
    assert SAMPLE_EVENT_25 in ctx["mid_outline_hint"]


def test_entry_status_pending_human_skipped(tmp_path):
    """逐条门禁：entry.status=pending_human → 挂起不可用。"""
    _write_mid_outline(tmp_path, "draft",
                       {"25": _entry(SAMPLE_EVENT_25) | {"status": "pending_human"}})
    director, _ = _build_director(tmp_path)
    assert director._load_mid_outline_entry(25) is None
    ctx, prior = director._build_shared_context(25)
    assert ctx["mid_outline_hint"] == ""


def test_legacy_approved_file_without_entry_status_usable(tmp_path):
    """旧数据兼容：meta.status=approved 且无 entry.status → 仍可用（不破坏历史 approved 文件）。"""
    _write_mid_outline(tmp_path, "approved",
                       {"25": {"chapter": 25, "core_event": SAMPLE_EVENT_25,
                               "characters": ["C001", "C002"], "boundary_note": None}})
    director, _ = _build_director(tmp_path)
    assert director._load_mid_outline_entry(25) is not None
