# -*- coding: utf-8 -*-
"""阶段 2 测试：Reviewer 注入设定对照块（真实 bible 内容，非文件名字符串）。

hermetic：不依赖任何真实生成产物；call_llm 用 unittest.mock 打桩为固定返回。
"""
from __future__ import annotations

import json
import re
from unittest import mock

from novel_engine.agents.reviewer_agent import ReviewerAgent

DIM_SCORES = {
    "plot_consistency": 20.0,
    "character_consistency": 15.0,
    "foreshadow_execution": 15.0,
    "style_match": 10.0,
    "pacing": 8.0,
    "innovation": 8.0,
    "hook_strength": 6.0,
    "reader_retention": 5.0,
    "cliffhensity": 4.0,
}

FAKE_REVIEW = {
    "verdict": "pass",
    "scores": DIM_SCORES,
    "issues": [],
    "praise": "ok",
}


def _make_agent() -> ReviewerAgent:
    return ReviewerAgent(llm_client=None)  # call_llm 被 mock，client 不实际使用


def _call_capture(monkeypatch) -> dict:
    captured = {}

    def _fake_call_llm(prompt: str, **kwargs):
        captured["prompt"] = prompt
        return dict(FAKE_REVIEW)

    monkeypatch.setattr(
        "novel_engine.agents.reviewer_agent.call_llm", _fake_call_llm)
    return captured


def test_setting_block_contains_real_bible_content_for_ch1():
    """第一阶段章节：对照块含 character_bible 全文、style_bible 全文、
    第一阶段 forbidden（陆烬正式修炼体系/师承禁令）。"""
    agent = _make_agent()
    block = agent._build_setting_block(1)
    assert "设定对照块" in block
    # character_bible 全文注入（取 bible 文件真实首行作为锚点）
    from pathlib import Path
    _bible = Path(__file__).parent.parent / "bible"
    _cb_head = (_bible / "character_bible.md").read_text(encoding="utf-8").strip()
    assert _cb_head.splitlines()[0] in block
    # 第一阶段 forbidden 条款（author_intent 原文）
    assert "正式修炼体系" in block or "师承" in block


def test_setting_block_slices_by_volume():
    """第二/第三阶段章节取到对应卷的 forbidden，而非第一阶段。"""
    agent = _make_agent()
    block_317 = agent._build_setting_block(317)
    block_700 = agent._build_setting_block(700)
    # 各阶段 forbidden 内容不同（317 属于第二阶段、700 属于第三阶段）
    assert block_317 != block_700


def test_review_prompt_includes_setting_block(monkeypatch):
    """review_chapter 构造的 prompt 真实包含设定对照块内容（不是只有文件名）。"""
    captured = _call_capture(monkeypatch)
    agent = _make_agent()
    agent.review_chapter(
        chapter_num=1,
        task_card={"chapter_num": 1, "scene_blueprints": []},
        synopsis={"synopsis": "第一章梗概"},
        novel_text="陆烬降生，陈老根收留。",
        world_state={},
        deterministic_signals=None,
    )
    prompt = captured["prompt"]
    assert "设定对照块" in prompt
    assert "人物设定 character_bible 全文" in prompt
    assert "文风设定 style_bible 全文" in prompt
    # 审查要求第 3 条已强化为"对照设定对照块逐条核对"
    assert "对照上方" in prompt


def test_review_prompt_with_past_life_signal(monkeypatch):
    """deterministic_signals 含 past_life_reveal 且偏高 → prompt 注入揭示尺度说明
    （soft，不阻断）。"""
    captured = _call_capture(monkeypatch)
    agent = _make_agent()
    agent.review_chapter(
        chapter_num=48,
        task_card={"chapter_num": 48, "scene_blueprints": []},
        synopsis={"synopsis": "前世临终记忆片段"},
        novel_text="病床上咽气，前世弥留，白墙吊瓶，心电图归于一条直线。",
        world_state={},
        deterministic_signals={
            "past_life_reveal": {
                "reveal_scale_high": True, "max_window_hits": 5, "windows": []},
        },
    )
    prompt = captured["prompt"]
    assert "past_life_reveal" in prompt or "揭示尺度偏高" in prompt
    assert "不构成阻断" in prompt
    assert "reveal_scale" in prompt
