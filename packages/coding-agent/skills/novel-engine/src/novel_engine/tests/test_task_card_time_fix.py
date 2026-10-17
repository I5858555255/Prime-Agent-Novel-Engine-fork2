# -*- coding: utf-8 -*-
"""ch295 归因回归：时间倒置必须有确定性修复指令，不能无差别重发。

背景：detect_task_card_scene_time_inversion 能检测倒置（CC round-30），但 strict 分流
只处理字段缺失（call2/call3 无差别重发），倒置语义错误无针对性指令 → 重发大概率再次
倒置 → 重试耗尽 FAILED（ch295）。修复：_call_scene_craft 新增 time_fix_instructions
参数注入"时间修正要求"；generate_task_card(_cached) 分流新增 call2_timefix 分支。
零 LLM，fake LLM 按 prompt 特征返回（含"时间修正要求"则输出单调时间）。
"""
from __future__ import annotations

import json
from typing import Any

from novel_engine.agents.chapter_director import ChapterDirector

_INVERTED_TIMES = ["清晨", "午后", "上午", "黄昏"]   # scene3 上午 早于 scene2 午后 → 倒置
_FIXED_TIMES = ["清晨", "上午", "午后", "黄昏"]      # 单调推进
_SPAN_TIMES = ["清晨", "午时", "黄昏", "次日晨"]     # scene4 次日晨 超出章级跨度（span=一日）→ 跨日


class _FakeLLM:
    """假 LLM：记录 prompt；含"时间修正要求"时返回单调时间，否则返回倒置时间。"""

    def __init__(self) -> None:
        self.captured_prompts: list[str] = []
        self.timefix_prompts: list[str] = []

    def _scenes(self, times: list[str]) -> list[dict]:
        return [
            {"scene_num": i, "narrative_time": t, "location": "村口",
             "characters": ["C001", "C002"], "goal": "推进剧情", "conflict": "新冲突",
             "emotion": "平静", "beats": ["发生一件事", "产生反应", "留下钩子"],
             "concrete_events": [{"event": "做了事", "observable_action": "被看见"}],
             "named_interactions": [], "info_reveal_points": [],
             "protagonist_interiority": "内心", "scene_constraints": [],
             "scene_progression_contract": {"new_state_or_entity": ["新变化"],
                                            "irreversible_change": "不可逆"}}
            for i, t in zip((1, 2, 3, 4), times)
        ]

    def chat_completion(self, messages: list[dict], **kwargs: Any) -> dict:
        user_prompt = ""
        for m in messages:
            if m.get("role") == "user":
                user_prompt = m.get("content", "")
        self.captured_prompts.append(user_prompt)
        if "时间修正要求" in user_prompt:
            self.timefix_prompts.append(user_prompt)
            body = {"scene_blueprints": self._scenes(_FIXED_TIMES)}
        elif "生成章级元数据" in user_prompt:
            body = {
                "chapter_num": 1, "protagonist_agency_level": "normal",
                "core_goal": "本章核心目标", "conflicts": {"internal": "内心", "external": "外部"},
                "emotion_curve": {"start": "起", "middle": "中", "climax": "高", "end": "钩子"},
                "chapter_hook": "章末钩子", "foreshadow_actions": [],
                "chapter_events": [{"event_type": "互动", "one_line_summary": "一句摘要",
                                    "participants": ["C001"], "location": "村口",
                                    "narrative_time": "清晨", "consequence_state": "结果"}],
                "state_changes": [], "foreshadow_execution": [],
                "end_state": {"narrative_position": "末帧", "location": "村口",
                              "completed_actions": ["完成"], "pending_actions": ["接续"],
                              "time_marker": "当日"},
                "timeline_anchor": {"chapter_start_marker": "清晨",
                                    "max_time_progression": "一日",
                                    "forbidden_markers": []},
            }
        elif "补充每个场景的重字段" in user_prompt:
            body = {"scene_blueprints": self._scenes(_INVERTED_TIMES)}
        else:  # skeleton
            body = {"scene_blueprints": self._scenes(_INVERTED_TIMES)}
        return {"content": json.dumps(body, ensure_ascii=False),
                "finish_reason": "stop", "_usage": {}}


def _build_director(tmp_path):
    (tmp_path / "config" / "planning").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "planning" / "volumes.json").write_text(
        json.dumps({"volumes": [{"id": "V01", "chapter_range": [1, 316]}]},
                   ensure_ascii=False), encoding="utf-8")
    (tmp_path / "config" / "planning" / "plot_graph.json").write_text(
        json.dumps({"nodes": []}), encoding="utf-8")
    (tmp_path / "config" / "foreshadow").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "foreshadow" / "registry.json").write_text(
        '{"foreshadows": []}', encoding="utf-8")
    fake = _FakeLLM()
    director = ChapterDirector(tmp_path, llm_client=fake)
    return director, fake


def test_craft_injects_time_fix_instructions(tmp_path):
    """time_fix_instructions 非空时必须注入"时间修正要求"块到 craft prompt。"""
    director, fake = _build_director(tmp_path)
    ctx, prior = director._build_shared_context(1)
    skeleton = fake._scenes(_INVERTED_TIMES)
    time_inv = ["场景3 叙事时间「上午」早于前序场景「午后」，章内时间顺序倒置（午后 → 上午），请按故事时序重排场景或修正 narrative_time"]
    director._call_scene_craft(1, ctx, prior, skeleton, time_fix_instructions=time_inv)
    last = fake.captured_prompts[-1]
    assert "时间修正要求" in last
    assert "场景3 叙事时间「上午」早于前序场景「午后」" in last
    assert "随 scene_num 单调推进" in last


def test_craft_timefix_includes_backref_label_guidance(tmp_path):
    """修复F（ch394）：timefix 指令必须含回溯标注指引——闪回场景须加"回忆/先前"前缀，
    非回溯必须重排场景顺序，禁止仅改 narrative_time 文本。"""
    director, fake = _build_director(tmp_path)
    ctx, prior = director._build_shared_context(1)
    skeleton = fake._scenes(_INVERTED_TIMES)
    time_inv = ["场景3 叙事时间「午后」早于前序场景「当夜」，章内时间顺序倒置（当夜 → 午后），请按故事时序重排场景或修正 narrative_time"]
    director._call_scene_craft(1, ctx, prior, skeleton, time_fix_instructions=time_inv)
    last = fake.captured_prompts[-1]
    assert "回忆/先前" in last, "timefix must guide flashback prefix labeling"
    assert "重排场景顺序" in last, "timefix must require reordering for non-flashback"
    assert "禁止仅修改 narrative_time 文本" in last, "timefix must forbid label-only edits"


def test_craft_without_time_fix_no_block(tmp_path):
    """time_fix_instructions 为 None 时 prompt 不含修正块（默认路径不变）。"""
    director, fake = _build_director(tmp_path)
    ctx, prior = director._build_shared_context(1)
    skeleton = fake._scenes(_FIXED_TIMES)
    director._call_scene_craft(1, ctx, prior, skeleton)
    last = fake.captured_prompts[-1]
    assert "时间修正要求" not in last


def test_generate_task_card_time_inversion_routed_to_timefix(tmp_path):
    """时间倒置错误必须走 call2_timefix（带修正指令重发 craft），而非无差别重发后 FAILED。"""
    director, fake = _build_director(tmp_path)
    card = director.generate_task_card(1)
    # 分流生效：LLM 收到过一次带"时间修正要求"的重发（fake 据此输出单调时间）
    assert fake.timefix_prompts, "time-inversion must trigger a time-fix retry"
    scenes = card.get("scene_blueprints", [])
    times = [s.get("narrative_time") for s in scenes]
    assert times == _FIXED_TIMES, f"final card must be time-ordered, got {times}"
    # 终检通过：无 strict 错误（generate_task_card 成功返回即校验通过）
    assert len(scenes) == 4
    assert director.validate_task_card(card, 1, strict=True) == []


class _SpanFakeLLM(_FakeLLM):
    """跨日 fake：首次 craft 返回跨日时间（次日晨超出章级跨度）；收到时间修正要求后返回当日单调时间。"""

    def chat_completion(self, messages: list[dict], **kwargs: Any) -> dict:
        user_prompt = ""
        for m in messages:
            if m.get("role") == "user":
                user_prompt = m.get("content", "")
        self.captured_prompts.append(user_prompt)
        if "时间修正要求" in user_prompt:
            self.timefix_prompts.append(user_prompt)
            body = {"scene_blueprints": self._scenes(_FIXED_TIMES)}
        elif "生成章级元数据" in user_prompt:
            body = {
                "chapter_num": 1, "protagonist_agency_level": "normal",
                "core_goal": "本章核心目标", "conflicts": {"internal": "内心", "external": "外部"},
                "emotion_curve": {"start": "起", "middle": "中", "climax": "高", "end": "钩子"},
                "chapter_hook": "章末钩子", "foreshadow_actions": [],
                "chapter_events": [{"event_type": "互动", "one_line_summary": "一句摘要",
                                    "participants": ["C001"], "location": "村口",
                                    "narrative_time": "清晨", "consequence_state": "结果"}],
                "state_changes": [], "foreshadow_execution": [],
                "end_state": {"narrative_position": "末帧", "location": "村口",
                              "completed_actions": ["完成"], "pending_actions": ["接续"],
                              "time_marker": "当日"},
                "timeline_anchor": {"chapter_start_marker": "清晨",
                                    "max_time_progression": "一日",
                                    "forbidden_markers": []},
            }
        else:  # skeleton / craft（无修正指令）
            body = {"scene_blueprints": self._scenes(_SPAN_TIMES)}
        return {"content": json.dumps(body, ensure_ascii=False),
                "finish_reason": "stop", "_usage": {}}


def test_generate_task_card_overnight_span_routed_to_timefix(tmp_path):
    """跨日 span 错误（超出章级跨度）必须走 call2_timefix，注入时间跨度修正指令后重发。"""
    director, _ = _build_director(tmp_path)
    fake = _SpanFakeLLM()
    director = ChapterDirector(tmp_path, llm_client=fake)
    card = director.generate_task_card(1)
    assert fake.timefix_prompts, "overnight-span must trigger a time-fix retry"
    fix_prompt = fake.timefix_prompts[0]
    assert "超出章级跨度" in fix_prompt or "章内不得跨日" in fix_prompt, \
        "retry prompt must carry the span violation detail"
    scenes = card.get("scene_blueprints", [])
    times = [s.get("narrative_time") for s in scenes]
    assert times == _FIXED_TIMES, f"final card must stay within chapter span, got {times}"
    assert director.validate_task_card(card, 1, strict=True) == []
