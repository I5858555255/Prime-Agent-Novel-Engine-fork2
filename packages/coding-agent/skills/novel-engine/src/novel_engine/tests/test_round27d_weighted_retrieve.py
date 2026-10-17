# -*- coding: utf-8 -*-
"""CC round-27d: weighted_retrieve is opt-in and preserves default behaviour.

weighted_retrieve(enabled=False) must be identical to retrieve_by_keywords;
with enabled=True it must rank by match count and recency when
current_chapter is provided. Uses a synthetic chapter_index (no real
runtime artifacts required).
"""
import json

from novel_engine.core.memory_manager import MemoryManager


def _make_manager(tmp_path):
    mm = MemoryManager(str(tmp_path))
    idx = {"entries": {
        "1": {"chapter": 1, "keywords": ["陆烬", "婴儿", "雾隐村"]},
        "5": {"chapter": 5, "keywords": ["陆烬", "吐纳", "陈老根"]},
        "6": {"chapter": 6, "keywords": ["陈老根", "吐纳", "气感"]},
        "8": {"chapter": 8, "keywords": ["军旅", "身份", "陈老根"]},
    }}
    p = tmp_path / "memory" / "long_term"
    p.mkdir(parents=True, exist_ok=True)
    (p / "chapter_index.json").write_text(json.dumps(idx), encoding="utf-8")
    return mm


def test_disabled_is_identical_to_plain(tmp_path):
    mm = _make_manager(tmp_path)
    kw = ["陆烬", "陈老根"]
    plain = mm.retrieve_by_keywords(kw, limit=10)
    weighted = mm.weighted_retrieve(kw, current_chapter=6, limit=10, enabled=False)
    assert [e["chapter"] for e in weighted] == [e["chapter"] for e in plain]


def test_enabled_ranks_recency_when_chapter_given(tmp_path):
    mm = _make_manager(tmp_path)
    kw = ["陆烬", "陈老根"]
    # ch5 与 ch1 均命中 2 词；current_chapter=6 → ch5 应排前面
    weighted = mm.weighted_retrieve(kw, current_chapter=6, limit=10, enabled=True,
                                    base_weight=2.0, decay=0.5)
    chapters = [e["chapter"] for e in weighted]
    assert chapters[0] == 5, f"expected ch5 first (recency), got {chapters}"


def test_enabled_match_count_still_dominates(tmp_path):
    mm = _make_manager(tmp_path)
    kw = ["陈老根", "吐纳", "气感"]
    # ch6 命中 3 词（最高匹配）；尽管 current_chapter=1 距 ch6 远，仍应第一
    weighted = mm.weighted_retrieve(kw, current_chapter=1, limit=10, enabled=True,
                                    base_weight=2.0, decay=0.1)
    chapters = [e["chapter"] for e in weighted]
    assert chapters[0] == 6, f"expected ch6 first (match dominance), got {chapters}"


def test_no_match_returns_empty(tmp_path):
    mm = _make_manager(tmp_path)
    out = mm.weighted_retrieve(["不存在的词"], enabled=True)
    assert out == []
