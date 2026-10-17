# -*- coding: utf-8 -*-
"""CC P0: cross-chapter event ledger (append-only commit hook + recent-N injection)."""
import json
from pathlib import Path

from novel_engine.pipeline.event_ledger import (
    build_entries, append_chapter_events, load_entries, recent_event_block, ledger_path,
)


def _card_with_events():
    return {"chapter_num": 1, "scene_blueprints": [], "chapter_events": [
        {"event_type": "捡婴", "one_line_summary": "陈老根雾夜山道拾得婴儿",
         "participants": ["陈老根", "陆烬"], "location": "村外山道",
         "narrative_time": "夜", "consequence_state": "婴儿被抱回村"},
    ]}


def _card_derived():
    return {"chapter_num": 2, "scene_blueprints": [
        {"scene_num": 1, "goal": "陈老根为婴儿取名陆烬", "characters": ["陈老根", "陆烬"], "location": "土屋"},
        {"scene_num": 2, "goal": "村民井台议论异婴来历", "characters": ["李寡妇", "王大婶"], "location": "井台"},
    ]}


def test_build_entries_explicit_and_derived():
    e = build_entries(_card_with_events(), 1)
    assert len(e) == 1 and e[0]["event_id"] == "ch1_ev01"
    assert e[0]["participants"] == ["陈老根", "陆烬"] and e[0]["location"] == "村外山道"
    d = build_entries(_card_derived(), 2)
    assert [x["event_id"] for x in d] == ["ch2_ev01", "ch2_ev02"]
    assert "取名陆烬" in d[0]["one_line_summary"]


def test_append_is_atomic_and_idempotent(tmp_path):
    n = append_chapter_events(tmp_path, 1, _card_with_events())
    assert n == 1 and ledger_path(tmp_path).exists()
    # same chapter re-commit adds nothing
    assert append_chapter_events(tmp_path, 1, _card_with_events()) == 0
    rows = [json.loads(x) for x in ledger_path(tmp_path).read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["event_id"] == "ch1_ev01"
    assert load_entries(tmp_path)[0]["consequence_state"] == "婴儿被抱回村"


def test_recent_block_excludes_current_and_limits_window(tmp_path):
    for ch in range(1, 9):
        append_chapter_events(tmp_path, ch, {
            "chapter_num": ch,
            "scene_blueprints": [{"scene_num": 1, "goal": f"第{ch}章发生的事件E{ch}",
                                  "characters": ["陆烬"], "location": "村"}]})
    # chapter 1 has no prior events -> empty
    assert recent_event_block(tmp_path, 1) == ""
    block = recent_event_block(tmp_path, 9, limit=5)
    assert "前情事件台账" in block and "严禁" in block
    assert "E8" in block and "E4" in block
    assert "E3" not in block and "E1" not in block  # only last 5 chapters (4..8)
    # current chapter itself never leaks in
    append_chapter_events(tmp_path, 9, {"chapter_num": 9, "scene_blueprints": [
        {"scene_num": 1, "goal": "第九章才发生的事E9", "characters": [], "location": ""}]})
    assert "E9" not in recent_event_block(tmp_path, 9)
