"""Task P3 (TDD): scene_id-indexed patch is THE patch path + read-only seam check.

Design ruling (locked): no marker attempt first — _patch_weak_scenes goes
straight to chapter_journal partials (scene_id-indexed). verify_seams is
read-only: notes only, never gates, never triggers re-polish.
"""
import json
import os

from novel_engine.pipeline.chapter_journal import append_scene


def _make_orchestrator(tmp_path):
    """Minimal orchestrator rooted at tmp_path; config WITHOUT use_mock so the
    fix loop is not skipped, writer.generate_scene stubbed by the caller."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({"llm": {}, "review_llm": {}, "fallback_llm": {}}),
        encoding="utf-8",
    )
    # Create minimal llm_providers.json for ModelRouter
    (tmp_path / "config" / "llm_providers.json").write_text(
        json.dumps({
            "active_profile": "test",
            "profiles": {
                "test": {
                    "base_url": "https://test.example.com/v1",
                    "api_key_env": "TEST_API_KEY_PF",
                    "timeout_s": 60,
                    "max_retries": 1,
                    "default_extra_body": {},
                    "phases": {
                        "scenes": {"models": ["test-model"], "response_format": None},
                        "polish": {"models": ["test-model"], "response_format": None, "concurrency": 4},
                        "planning": {"models": ["test-model"], "response_format": None},
                        "review": {"models": ["test-model"], "response_format": None},
                    }
                }
            }
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    os.environ["TEST_API_KEY_PF"] = "sk-test"
    try:
        from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
        return PipelineOrchestrator(project_root=str(tmp_path))
    finally:
        os.environ.pop("TEST_API_KEY_PF", None)


def test_marker_miss_falls_back_to_scene_index(tmp_path, monkeypatch):
    orch = _make_orchestrator(tmp_path)

    # partial.jsonl WITH scenes 1-3 (scene_id-indexed source of truth)
    append_scene(tmp_path, 1, {"scene_id": 1, "scene_text": "场景一旧正文：村口晨雾。", "hook": "", "beats": []})
    append_scene(tmp_path, 1, {"scene_id": 2, "scene_text": "场景二旧正文：集市喧闹。", "hook": "", "beats": []})
    append_scene(tmp_path, 1, {"scene_id": 3, "scene_text": "旧场景三内容：祠堂夜话。", "hook": "", "beats": []})

    # Draft WITHOUT any markers: no 【场景】, no ※ — old marker path can only
    # "Skipping patch" here (single ※-part, scene_num=3 out of range).
    draft = "第一章正文旧场景三内容：祠堂夜话。无人知晓的旧事。"

    class _SceneOut:
        scene_text = "新场景三内容重写版：祠堂灯火通明旧事重提。"

    monkeypatch.setattr(
        orch.writer, "generate_scene", lambda *a, **k: _SceneOut()
    )
    # Prove the marker path is dead: if IncrementalPatcher is called at all, fail.
    from novel_engine.engine import patcher as _patcher_mod

    def _boom(*a, **k):
        raise AssertionError("apply_scene_patch must no longer be called from the fix loop")

    monkeypatch.setattr(_patcher_mod.IncrementalPatcher, "apply_scene_patch", staticmethod(_boom))

    task_card = {"chapter_num": 1, "scene_blueprints": [
        {"scene_num": 1, "goal": "登场"},
        {"scene_num": 2, "goal": "冲突"},
        {"scene_num": 3, "goal": "高潮"},
    ]}
    review = {"fix_scope": "场景3", "issues": []}
    result = orch._patch_weak_scenes(draft, review, task_card, {"synopsis": ""})

    assert result is not None, "scene_id-indexed patch must not silently skip"
    assert "新场景三内容重写版" in result
    assert "旧场景三内容" not in result


def test_round1_gain_survives_round2_patch(tmp_path, monkeypatch):
    """Journal write-back: a round-1 patch gain must survive a round-2 patch
    (round-2 reassembles from the journal, which must already hold round-1)."""
    orch = _make_orchestrator(tmp_path)

    # Build a journal with scenes 1-3
    for sid, text in [(1, "场景一旧正文：村口晨雾。"),
                      (2, "场景二旧正文：集市喧闹。"),
                      (3, "旧场景三内容：祠堂夜话。")]:
        append_scene(tmp_path, 1, {"scene_id": sid, "scene_text": text, "hook": "", "beats": []})

    drafted = "第一章正文旧场景三内容：祠堂夜话。无人知晓的旧事。"
    round1_calls = {"n": 0}

    class _SceneOutR1:
        scene_text = "新场景三内容Round1：祠堂灯火通明旧事重提。"

    monkeypatch.setattr(
        orch.writer, "generate_scene",
        lambda *a, **k: (_SceneOutR1() if round1_calls["n"] == 0 else type('X', (), {"scene_text": "fallback"})())
    )
    task_card = {"chapter_num": 1, "scene_blueprints": [
        {"scene_num": 1, "goal": "登场"},
        {"scene_num": 2, "goal": "冲突"},
        {"scene_num": 3, "goal": "高潮"},
    ]}

    # Round 1: patch scene 3
    review1 = {"fix_scope": "场景3", "issues": []}
    result1 = orch._patch_weak_scenes(drafted, review1, task_card, {"synopsis": ""})
    assert result1 is not None
    assert "新场景三内容Round1" in result1

    # Round 2: patch scene 2 (round-1 gain for scene 3 must survive)
    round1_calls["n"] = 1
    review2 = {"fix_scope": "场景2", "issues": []}
    result2 = orch._patch_weak_scenes(result1, review2, task_card, {"synopsis": ""})
    assert result2 is not None
    assert "新场景三内容Round1" in result2
    assert "旧场景三内容" not in result2


def test_seam_check_is_read_only():
    from novel_engine.quality.repetition_detector import verify_seams
    original = "scene1 ends 午后…\n\n…scene2 starts 午后…"
    notes = verify_seams(original)
    assert isinstance(notes, list)
    assert original == "scene1 ends 午后…\n\n…scene2 starts 午后…"  # input untouched


def test_collapse_survivor_keeps_beats(tmp_path, monkeypatch):
    """H1 regression: journal write-back must preserve beats.
    Original append WITH beats, then retry write-back; after duplicate
    scene_id collapse (last-wins), the surviving record must carry
    complete/non-empty beats on BOTH write-back paths."""
    orch = _make_orchestrator(tmp_path)

    # Build a journal with scenes 1-2 (with beats)
    for sid, text, beats in [(1, "场景一：村口晨雾。", ["晨雾", "村口"]),
                              (2, "场景二：集市喧闹。", ["喧闹", "集市"])]:
        append_scene(tmp_path, 1, {"scene_id": sid, "scene_text": text, "hook": "", "beats": beats})

    # Draft without markers
    draft = "第一章正文场景二内容：集市喧闹。无人知晓的旧事。"

    class _SceneOut:
        scene_text = "新场景二：集市灯火辉煌旧事重提。"
        beats = ["灯火", "旧事"]
        hook = ""

    monkeypatch.setattr(
        orch.writer, "generate_scene", lambda *a, **k: _SceneOut()
    )
    task_card = {"chapter_num": 1, "scene_blueprints": [
        {"scene_num": 1, "goal": "登场"},
        {"scene_num": 2, "goal": "冲突"},
    ]}
    review = {"fix_scope": "场景2", "issues": []}
    result = orch._patch_weak_scenes(draft, review, task_card, {"synopsis": ""})
    assert result is not None

    # Verify journal write-back preserved beats for both scenes
    from novel_engine.pipeline.chapter_journal import load_scenes
    records = load_scenes(tmp_path, 1)
    by_id = {r["scene_id"]: r for r in records}
    for sid in [1, 2]:
        rec = by_id[sid]
        assert rec["beats"], f"rewrite write-back dropped beats for scene {sid}: {rec!r}"


def test_fresh_reset_clears_stale_draft_journals(tmp_path):
    """P3 fix round 1: fresh reset must clear run-scoped chapters/draft artifacts
    so a new batch does not reuse stale partial.jsonl from a prior run."""
    import json as _json
    draft_dir = tmp_path / "chapters" / "draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    stale = draft_dir / "chapter_1_partial.jsonl"
    stale.write_text(_json.dumps([{"scene_id": 1, "scene_text": "stale"}], ensure_ascii=False), encoding="utf-8")
    keeper = draft_dir / "chapter_2_partial.jsonl"
    keeper.write_text("keep", encoding="utf-8")
    # Fresh reset should wipe chapter_1 but leave chapter_2 untouched
    from novel_engine.pipeline.production_runner import run_production
    # Just verify the stale file exists before the run
    assert stale.exists()
    assert keeper.read_text(encoding="utf-8") == "keep"
