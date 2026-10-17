# -*- coding: utf-8 -*-
"""CC28 批B：fix 循环 journal 候选快照 + 原子回滚原语单测。"""
from novel_engine.pipeline.chapter_journal import (
    append_scene, load_scenes, snapshot_journal, restore_journal,
    journal_path)


def _sid_text(recs):
    return {r["scene_id"]: r["scene_text"] for r in recs}


def test_restore_reverts_later_overwrite(tmp_path):
    append_scene(tmp_path, 1, {"scene_id": 1, "scene_text": "最初的好版本一。"})
    append_scene(tmp_path, 1, {"scene_id": 2, "scene_text": "最初的好版本二。"})
    snap = snapshot_journal(tmp_path, 1)
    original = _sid_text(load_scenes(tmp_path, 1))

    # 模拟 fix 轮次就地覆写 journal（劣化）
    p = journal_path(tmp_path, 1)
    p.write_text(
        '{"scene_id": 1, "scene_text": "劣化后的版本一", "hook": "", "beats": [], "entities_json": ""}\n'
        '{"scene_id": 2, "scene_text": "劣化后的版本二", "hook": "", "beats": [], "entities_json": ""}\n',
        encoding="utf-8")
    assert _sid_text(load_scenes(tmp_path, 1)) != original

    # 回滚到最佳候选快照
    assert restore_journal(tmp_path, 1, snap) is True
    assert _sid_text(load_scenes(tmp_path, 1)) == original
    assert not journal_path(tmp_path, 1).with_suffix(".jsonl.rollback.tmp").exists()


def test_snapshot_missing_file_returns_none(tmp_path):
    assert snapshot_journal(tmp_path, 9) is None


def test_restore_none_deletes_file(tmp_path):
    append_scene(tmp_path, 3, {"scene_id": 1, "scene_text": "x"})
    assert journal_path(tmp_path, 3).exists()
    assert restore_journal(tmp_path, 3, None) is True
    assert not journal_path(tmp_path, 3).exists()
    assert load_scenes(tmp_path, 3) == []


def test_restore_none_when_absent_is_ok(tmp_path):
    # 目标态无文件且当前也无文件：幂等成功
    assert restore_journal(tmp_path, 4, None) is True
