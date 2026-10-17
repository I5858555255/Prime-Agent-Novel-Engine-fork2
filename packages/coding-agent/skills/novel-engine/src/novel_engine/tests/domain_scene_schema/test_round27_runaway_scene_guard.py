# -*- coding: utf-8 -*-
"""CC round-27：journal 权威读取的单场失控长度护栏。

r39 真机出现 scene1 末条记录 20047 字（LLM 定点重生失控，把整章体量吐进单场），
被 append-only partials 记录后污染“最新非空即权威”的读取。权威读取必须跳过失控
记录、回退上一合理版本；只有全部版本失控才兜底返回最后一条，避免整章缺失。
"""
from novel_engine.pipeline.chapter_journal import (
    append_scene, load_scenes, load_authoritative_scenes,
    get_authoritative_scene_content, SCENE_RUNAWAY_DEFAULT_CHARS,
)

NORMAL = "这是一场长度正常的场景正文。" * 20          # ~360 字
RUNAWAY = "失控" * 12000                              # 24000 字，必超整章目标


def test_get_authoritative_skips_runaway_takes_last_good():
    records = [
        {"scene_id": 1, "scene_text": NORMAL + "V1"},
        {"scene_id": 1, "scene_text": RUNAWAY},        # 失控，不得成为权威
    ]
    got = get_authoritative_scene_content(records, 1)
    assert got == NORMAL + "V1"
    assert RUNAWAY not in got


def test_get_authoritative_all_runaway_falls_back_last_nonempty():
    records = [{"scene_id": 2, "scene_text": RUNAWAY}]
    got = get_authoritative_scene_content(records, 2)  # 不抛 SceneContentMissingError
    assert got == RUNAWAY


def test_guard_disabled_returns_latest_as_is():
    records = [
        {"scene_id": 1, "scene_text": NORMAL},
        {"scene_id": 1, "scene_text": RUNAWAY},
    ]
    assert get_authoritative_scene_content(records, 1, max_chars=None) == RUNAWAY


def test_empty_record_never_authoritative():
    records = [
        {"scene_id": 3, "scene_text": ""},
        {"scene_id": 3, "scene_text": NORMAL},
    ]
    assert get_authoritative_scene_content(records, 3) == NORMAL


def test_load_authoritative_scenes_filters_runaway_from_disk(tmp_path):
    append_scene(tmp_path, 5, {"scene_id": 1, "scene_text": NORMAL + "good"})
    append_scene(tmp_path, 5, {"scene_id": 1, "scene_text": RUNAWAY})  # 失控
    append_scene(tmp_path, 5, {"scene_id": 2, "scene_text": NORMAL})
    raw = load_scenes(tmp_path, 5)
    assert len(raw) == 3
    auth = load_authoritative_scenes(tmp_path, 5)  # 无 config -> 回退默认 8000
    by = {int(d["scene_id"]): d["scene_text"] for d in auth}
    assert set(by) == {1, 2}
    assert by[1] == NORMAL + "good"                  # sid1 回退合理版，非 24000 字怪物
    assert len(by[1]) < SCENE_RUNAWAY_DEFAULT_CHARS
    assert by[2] == NORMAL
