"""Tests for P0/P1 director fixes: prompt slimming, synopsis removal,
wall-clock budget, connect-wall detection, and chapter_status format compat."""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent.parent / "src"),
)

from novel_engine.agents.chapter_director import (
    ChapterDirector,
    DIRECTOR_PHASE_BUDGET_S,
    _CONNECT_WALL_THRESHOLD,
)
from novel_engine.pipeline.chapter_status import (
    get_status, set_status, last_committed, COMMITTED,
    _read_status_map, _write_status_map,
)


def test_director_output_no_synopsis(tmp_path):
    """Director JSON schema no longer includes 'synopsis' field."""
    cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_DIRECTOR_SYN",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "director": {"models": ["M1"], "response_format": "json_object", "stream": True},
                },
            }
        },
    }
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "llm_providers.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    os.environ["TEST_DIRECTOR_SYN"] = "sk-test"
    try:
        import inspect
        src = inspect.getsource(ChapterDirector.generate_task_card)
        assert '"synopsis"' not in src, "synopsis must be removed from director output schema"
    finally:
        os.environ.pop("TEST_DIRECTOR_SYN", None)


def test_director_max_tokens_updated():
    """Director call_llm uses max_tokens=1500 for skeleton, 2000 for craft, 1200 for metadata."""
    import inspect
    src = inspect.getsource(ChapterDirector._call_scene_skeleton)
    assert "max_tokens=1500" in src
    src2 = inspect.getsource(ChapterDirector._call_scene_craft)
    assert "max_tokens=2000" in src2
    src3 = inspect.getsource(ChapterDirector._call_chapter_metadata)
    assert "max_tokens=1200" in src3


def test_director_system_prompts_are_slim():
    """Three dedicated slim system prompts exist; generic SYSTEM_PROMPT is empty."""
    import inspect
    src = inspect.getsource(ChapterDirector)
    assert '_SKELETON_SYSTEM' in src
    assert '_CRAFT_SYSTEM' in src
    assert '_METADATA_SYSTEM' in src
    # Generic SYSTEM_PROMPT should be empty (not the 1930-char bulk prompt)
    assert '网络小说章节导演' not in ChapterDirector.SYSTEM_PROMPT
    # Skeleton system must NOT contain heavy craft/density/opening/夜章 rules
    assert 'density' not in ChapterDirector._SKELETON_SYSTEM.lower()
    assert '夜章' not in ChapterDirector._SKELETON_SYSTEM
    # Craft system must describe all 7 craft fields
    assert 'concrete_events' in ChapterDirector._CRAFT_SYSTEM
    assert 'scene_craft_elements' in ChapterDirector._CRAFT_SYSTEM
    # Metadata system must focus on chapter-level fields
    assert 'timeline_anchor' in ChapterDirector._METADATA_SYSTEM
    assert 'end_state' in ChapterDirector._METADATA_SYSTEM


def test_prompt_slimmed_bible_slices(tmp_path):
    """Director prompt uses reduced bible slices."""
    import inspect
    src = inspect.getsource(ChapterDirector._call_scene_skeleton)
    # Bible slices should be small (world/char 500, style 300)
    # world/character sliced to 300, style to 200 in skeleton call
    assert "[:300]" in src
    assert "[:200]" in src
    # volume_outline truncated to 800 in skeleton
    assert "[:800]" in src


def test_connect_wall_detects_disconnections():
    """Two consecutive Server disconnected errors trigger fast-fail."""
    fake_err = RuntimeError("Server disconnected without sending a response.")
    is_disconnect = any(kw in str(fake_err) for kw in
                        ("Server disconnected", "RemoteProtocolError", "Connection closed"))
    assert is_disconnect
    assert _CONNECT_WALL_THRESHOLD == 2


def test_wall_clock_budget_raises():
    """When elapsed > DIRECTOR_PHASE_BUDGET_S, raise RuntimeError."""
    import inspect
    src = inspect.getsource(ChapterDirector._check_budget)
    assert "wall-clock budget exceeded" in src
    assert DIRECTOR_PHASE_BUDGET_S == 600


def test_status_read_legacy_format(tmp_path):
    """Old string-format status files are read correctly."""
    status_data = {
        "chapters": {
            "1": "PASS",
            "2": "COMMITTED",
            "2_extra": '{"score": 88.4}',
        }
    }
    status_path = tmp_path / "runtime" / "chapter_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status_data, ensure_ascii=False), encoding="utf-8")
    root = tmp_path
    assert get_status(root, 1) == "PASS"
    assert get_status(root, 2) == "COMMITTED"
    assert last_committed(root) == 2


def test_status_read_new_format(tmp_path):
    """New dict-format status files are read correctly."""
    status_data = {
        "chapters": {
            "1": {"status": "PASS"},
            "2": {"status": "COMMITTED", "score": 88.4},
        }
    }
    status_path = tmp_path / "runtime" / "chapter_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(status_data, ensure_ascii=False), encoding="utf-8")
    root = tmp_path
    assert get_status(root, 1) == "PASS"
    assert get_status(root, 2) == "COMMITTED"
    assert last_committed(root) == 2


def test_status_write_new_format(tmp_path):
    """set_status writes in new dict format."""
    root = tmp_path
    set_status(root, 3, COMMITTED, score=90.1)
    data = json.loads((root / "runtime" / "chapter_status.json").read_text(encoding="utf-8"))
    chapters = data["chapters"]
    assert chapters["3"]["status"] == COMMITTED
    assert chapters["3"]["score"] == 90.1
    assert "3_extra" not in chapters


def test_status_roundtrip_legacy_to_new(tmp_path):
    """Legacy file read -> write produces clean new-format file."""
    legacy_chapters = {'1': 'COMMITTED', '1_extra': '{"score": 85.1}', '2': 'GAP_HUMAN_REVIEW'}
    legacy = {'chapters': legacy_chapters}
    status_path = tmp_path / "runtime" / "chapter_status.json"
    status_path.parent.mkdir(parents=True, exist_ok=True)
    status_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    normalized = _read_status_map(tmp_path)
    assert normalized["1"]["status"] == COMMITTED
    assert normalized["1"]["score"] == 85.1
    assert normalized["2"]["status"] == "GAP_HUMAN_REVIEW"
    _write_status_map(tmp_path, normalized)
    data = json.loads(status_path.read_text(encoding="utf-8"))
    assert "1_extra" not in data["chapters"]
    assert isinstance(data["chapters"]["1"], dict)

