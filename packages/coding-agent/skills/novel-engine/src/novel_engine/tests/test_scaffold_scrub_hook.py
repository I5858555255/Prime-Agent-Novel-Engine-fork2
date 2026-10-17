# -*- coding: utf-8 -*-
"""修复G2 回归：scaffold_scrub 字段词表补 hook/chapter_hook。

ch378 归因：模型把 task card 的 chapter_hook 字段名写进正文，final gate 拦到
latin_leak 样例 'hook'——FIELD_WORDS 缺该词，scrub 阶段无法清洗。
hermetic：纯函数，零 LLM。
"""
from __future__ import annotations

from novel_engine.quality.scaffold_scrub import scrub_scaffold_latin


def test_scrub_removes_isolated_hook():
    """孤立 'hook' 泄漏（JSON 上下文）必须被清掉。"""
    res = scrub_scaffold_latin("他暗暗记下此事。\n\"hook\": \"章末钩子\"。\n陈老根继续巡视。")
    assert res["status"] == "scrubbed"
    assert "hook" not in res["text"].lower()
    assert "hook" in res["removed_tokens"]


def test_scrub_removes_chapter_hook():
    """'chapter_hook' 字段名泄漏同样被清。"""
    res = scrub_scaffold_latin("夜色渐深。chapter_hook：阿紫失踪。\n他转身离开。")
    assert res["status"] == "scrubbed"
    assert "chapter_hook" not in res["text"].lower()


def test_scrub_keeps_clean_chinese():
    """纯中文正文不受影响。"""
    res = scrub_scaffold_latin("陈老根顿了顿枣木棍，笃的一声，三名村民退后半步。")
    assert res["status"] == "unchanged"
