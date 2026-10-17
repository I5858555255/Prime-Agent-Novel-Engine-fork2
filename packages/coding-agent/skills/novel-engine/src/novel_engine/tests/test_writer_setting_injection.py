# -*- coding: utf-8 -*-
"""阶段 3 测试：Writer 按卷定向注入（当前卷 forbidden 完整 + 实体定位）。

hermetic：直接读 bible 源文件（非真实生成产物），用 object.__new__ 构造
轻量 WriterAgent 避开完整初始化链。
"""
from __future__ import annotations

from pathlib import Path

from novel_engine.agents.writer_agent import WriterAgent

_BIBLE = Path(__file__).parent.parent / "bible"
_BIBLE_FILES = {
    "world": "bible/world_bible.md",
    "character": "bible/character_bible.md",
    "style": "bible/style_bible.md",
    "author_intent": "bible/author_intent.md",
}


def _agent() -> WriterAgent:
    a = object.__new__(WriterAgent)
    a._bible_cache = {}
    a.root = _BIBLE.parent
    for key, rel in _BIBLE_FILES.items():
        p = _BIBLE.parent / rel
        a._bible_cache[key] = p.read_text(encoding="utf-8") if p.exists() else ""
    return a


def test_current_volume_forbidden_ch1():
    """第一阶段（ch1）→ 命中第一阶段 forbidden（陆烬正式修炼体系/师承禁令）。"""
    a = _agent()
    fb = a._extract_current_volume_forbidden(1)
    assert any("正式修炼体系" in f or "师承" in f for f in fb)


def test_current_volume_forbidden_differs_by_volume():
    """不同阶段 forbidden 内容不同（卷级切片生效，不是全局同一份）。"""
    a = _agent()
    fb1 = a._extract_current_volume_forbidden(1)
    fb317 = a._extract_current_volume_forbidden(317)
    fb700 = a._extract_current_volume_forbidden(700)
    assert fb1 and fb317 and fb700
    assert fb1 != fb317
    assert fb317 != fb700


def test_bible_snippet_includes_forbidden_and_directed_character():
    """定向注入：含当前卷 forbidden 原文 + 按角色定位的 character 段落。"""
    a = _agent()
    snip = a._bible_snippet(chapter_num=1, character_names=["陆烬"])
    assert "本卷禁止事项" in snip
    assert "正式修炼体系" in snip
    assert "陆烬" in snip


def test_locator_hits_by_name():
    """角色名命中时返回对应行（不退回截断）。"""
    a = _agent()
    out = a._locate_bible_by_names("character", ["陆烬"])
    assert out
    assert "陆烬" in out
    assert len(out) < len(a._bible_cache["character"])


def test_locator_falls_back_when_no_match():
    """实体不存在 → 退回固定截断兜底且不报错。"""
    a = _agent()
    out = a._locate_bible_by_names("character", ["不存在的角色XYZ"])
    assert out  # 兜底非空
    assert "不存在的角色XYZ" not in out


def test_bible_snippet_no_names_falls_back():
    """无角色/地点 → 兜底截断仍工作，forbidden 依然注入。"""
    a = _agent()
    snip = a._bible_snippet(chapter_num=1)
    assert "本卷禁止事项" in snip
    assert "正式修炼体系" in snip


def test_bible_snippet_chapter_zero_skips_forbidden():
    """chapter_num=0（未定位卷）→ 跳过 forbidden，只保留 bible 兜底，不报错。"""
    a = _agent()
    snip = a._bible_snippet(chapter_num=0)
    assert "本卷禁止事项" not in snip
    assert "【世界观】" in snip or "【人物卡】" in snip
