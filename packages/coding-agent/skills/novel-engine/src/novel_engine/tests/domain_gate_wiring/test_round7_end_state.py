# -*- coding: utf-8 -*-
"""CC round-7 P0-3 end_state persistence + strict director opening anchor."""
import json
import tempfile
from pathlib import Path

from novel_engine.pipeline.event_ledger import (
    append_end_state, latest_end_state, end_state_anchor_block, load_end_states,
)


def _root():
    d = Path(tempfile.mkdtemp())
    (d / "runtime").mkdir(parents=True, exist_ok=True)
    return d


def test_append_latest_and_anchor_block():
    root = _root()
    card = {
        "chapter_num": 1,
        "end_state": {
            "narrative_position": "陈老根抱起婴儿，身影没入浓雾，朝村口灯火走去",
            "location": "雾中归途",
            "completed_actions": ["陈老根在雾中发现并抱起青白婴儿", "扯袍裹住婴儿挡雾"],
            "pending_actions": ["抵达村口面对村民"],
            "time_marker": "当夜",
        },
    }
    assert append_end_state(root, 1, card) is True
    es = latest_end_state(root, 2)
    assert es["chapter_id"] == 1
    assert "抱起青白婴儿" in "、".join(es["completed_actions"])

    block = end_state_anchor_block(root, 2)
    assert "上一章结束于" in block
    assert "抱起青白婴儿" in block
    assert "第一个场景必须" in block
    # chapter 1 has no predecessor
    assert end_state_anchor_block(root, 1) == ""


def test_append_is_idempotent_per_chapter():
    root = _root()
    card = {"chapter_num": 1, "end_state": {
        "narrative_position": "停点A", "completed_actions": ["动作A"],
        "pending_actions": ["待演B"]}}
    append_end_state(root, 1, card)
    append_end_state(root, 1, card)
    assert len(load_end_states(root)) == 1


def test_fallback_derived_from_last_scene():
    root = _root()
    card = {"chapter_num": 3, "scene_blueprints": [
        {"scene_num": 1, "goal": "第一场目标"},
        {"scene_num": 2, "goal": "末场停在祠堂门前"},
    ]}
    assert append_end_state(root, 3, card) is True
    es = latest_end_state(root, 4)
    assert es["narrative_position"] == "末场停在祠堂门前"
    assert es["completed_actions"] == ["末场停在祠堂门前"]


def test_latest_only_strictly_earlier():
    root = _root()
    for ch, pos in [(1, "停点1"), (2, "停点2")]:
        append_end_state(root, ch, {"chapter_num": ch, "end_state": {
            "narrative_position": pos, "completed_actions": [pos]}})
    assert latest_end_state(root, 2)["narrative_position"] == "停点1"
    assert latest_end_state(root, 3)["narrative_position"] == "停点2"
