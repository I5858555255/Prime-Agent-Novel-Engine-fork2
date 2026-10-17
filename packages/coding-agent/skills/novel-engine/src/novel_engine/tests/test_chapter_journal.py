from novel_engine.pipeline.chapter_journal import (
    append_scene,
    completed_scene_ids,
    journal_path,
    load_scenes,
)

def test_corrupt_line_means_scene_incomplete(tmp_path):
    append_scene(tmp_path, 1, {"scene_id": 1, "scene_text": "a", "hook": "", "beats": []})
    append_scene(tmp_path, 1, {"scene_id": 2, "scene_text": "b", "hook": "h", "beats": ["x"]})
    # Corrupt lines go into the REAL journal file (via the module's path helper)
    with open(journal_path(tmp_path, 1), "a", encoding="utf-8") as f:
        f.write("{broken json\n")            # malformed JSON
        f.write('{"scene_text": "no id"}\n')  # missing scene_id
        f.write("[1, 2, 3]\n")                # valid JSON but not a scene dict
    assert completed_scene_ids(tmp_path, 1) == {1, 2}
    # File stays valid: further appends still work and count
    append_scene(tmp_path, 1, {"scene_id": 3, "scene_text": "c", "hook": "", "beats": []})
    assert completed_scene_ids(tmp_path, 1) == {1, 2, 3}
    loaded = load_scenes(tmp_path, 1)
    assert [(d["scene_id"], d["scene_text"], d["hook"], d["beats"]) for d in loaded] == [
        (1, "a", "", []),
        (2, "b", "h", ["x"]),
        (3, "c", "", []),
    ]

def test_missing_journal_means_nothing_complete(tmp_path):
    assert completed_scene_ids(tmp_path, 99) == set()
    assert load_scenes(tmp_path, 99) == []

def test_state_machine_touches_no_resume_files():
    import pathlib
    src = pathlib.Path("novel_engine/core/state_machine.py").read_text(encoding="utf-8")
    assert "resume_state" not in src
    assert "last_success" not in src

def test_resume_skips_completed_scenes(tmp_path):
    from novel_engine.pipeline.chapter_journal import append_scene, completed_scene_ids
    append_scene(tmp_path, 3, {"scene_id": 1, "scene_text": "a", "hook": "", "beats": []})
    append_scene(tmp_path, 3, {"scene_id": 2, "scene_text": "b", "hook": "", "beats": []})
    assert completed_scene_ids(tmp_path, 3) == {1, 2}

def test_kill_simulation_resume(tmp_path):
    """D2: process dies mid-chapter-3; restart must skip verified commits
    (ch 1-2) and resume ch 3 from the journaled scenes only."""
    from novel_engine.core.checkpoint import CheckpointManager, create_commit_transaction
    from novel_engine.pipeline.chapter_journal import append_scene, completed_scene_ids
    from novel_engine.pipeline.production_runner import (
        is_chapter_committed,
        last_success_chapter,
        load_resume_state,
        save_resume_state,
    )
    root = tmp_path
    mgr = CheckpointManager(root)
    for ch in (1, 2):
        create_commit_transaction(
            mgr, root, ch,
            novel_content=f"novel {ch}", synopsis_content=f"syn {ch}",
            outline_content="{}", world_state_snapshot={"w": ch},
        )
    # Partial chapter 3 on disk when the process is killed.
    append_scene(root, 3, {"scene_id": 1, "scene_text": "a", "hook": "", "beats": []})
    append_scene(root, 3, {"scene_id": 2, "scene_text": "b", "hook": "", "beats": []})
    save_resume_state(root, [1, 2])
    # --- simulated restart: only files survive, no in-memory state ---
    state = load_resume_state(root)
    assert state["done"] == [1, 2]
    assert last_success_chapter(root) == 2
    assert is_chapter_committed(root, 1)
    assert is_chapter_committed(root, 2)
    assert not is_chapter_committed(root, 3)
    assert completed_scene_ids(root, 3) == {1, 2}
    # Resume decision: skip 1-2, reopen chapter 3 after scene 2.
    assert max(state["done"]) + 1 == 3

def test_resume_state_roundtrip_idempotent(tmp_path):
    from novel_engine.pipeline.production_runner import load_resume_state, save_resume_state
    assert load_resume_state(tmp_path) == {"done": [], "last_success_chapter": 0}
    save_resume_state(tmp_path, [2, 1, 2])
    assert load_resume_state(tmp_path) == {"done": [1, 2], "last_success_chapter": 2}
    assert (tmp_path / "runtime" / "last_success_chapter.txt").read_text(encoding="utf-8").strip() == "2"

def test_resume_state_tolerates_corrupt_files(tmp_path):
    from novel_engine.pipeline.production_runner import load_resume_state
    rt = tmp_path / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    (rt / "resume_state.json").write_text("{broken", encoding="utf-8")
    assert load_resume_state(tmp_path) == {"done": [], "last_success_chapter": 0}
    (rt / "resume_state.json").write_text('{"done": [1]}', encoding="utf-8")
    (rt / "last_success_chapter.txt").write_text("not-a-number", encoding="utf-8")
    assert load_resume_state(tmp_path) == {"done": [1], "last_success_chapter": 1}

def test_reset_gate_honors_file_resume():
    """D2 fix F1: reset gate must honor file state OR the CLI pointer."""
    from novel_engine.pipeline.production_runner import compute_resume_plan
    # CLI 0 + file done=[1,2] -> resume, never fresh (no wipe).
    plan = compute_resume_plan(0, {"done": [1, 2], "last_success_chapter": 2}, 1)
    assert plan == {"effective_resume": 2, "fresh": False, "done": []}
    # True fresh run: CLI 0 + empty file -> wipe allowed.
    plan = compute_resume_plan(0, {"done": [], "last_success_chapter": 0}, 1)
    assert plan == {"effective_resume": 0, "fresh": True, "done": []}
    # CLI pointer alone still resumes.
    plan = compute_resume_plan(3, {"done": [], "last_success_chapter": 0}, 1)
    assert plan == {"effective_resume": 3, "fresh": False, "done": []}
    # Out-of-window entries survive for start_from > 1; in-window do not.
    plan = compute_resume_plan(0, {"done": [1, 2], "last_success_chapter": 2}, 3)
    assert plan == {"effective_resume": 2, "fresh": False, "done": [1, 2]}

def test_file_resume_skips_verified_only(tmp_path):
    """D2 fix F1: CLI 0 + file done=[1,2] with committed files present ->
    both chapters verify, so both skip (no wipe, no regeneration)."""
    from novel_engine.core.checkpoint import CheckpointManager, create_commit_transaction
    from novel_engine.pipeline.production_runner import (
        compute_resume_plan,
        is_chapter_committed,
        load_resume_state,
        save_resume_state,
    )
    mgr = CheckpointManager(tmp_path)
    for ch in (1, 2):
        create_commit_transaction(
            mgr, tmp_path, ch,
            novel_content=f"novel {ch}", synopsis_content=f"syn {ch}",
            outline_content="{}", world_state_snapshot={"w": ch},
        )
    save_resume_state(tmp_path, [1, 2])
    plan = compute_resume_plan(0, load_resume_state(tmp_path), 1)
    assert plan["fresh"] is False and plan["effective_resume"] == 2
    # Runner skip path: verified chapters re-enter done, nothing regenerates.
    done = list(plan["done"])
    for i in (1, 2):
        assert is_chapter_committed(tmp_path, i)
        if i not in done:
            done.append(i)
    assert sorted(done) == [1, 2]

def test_stale_done_not_repersisted(tmp_path):
    """D2 fix F1: CLI 0 + file done=[1,2] with committed files ABSENT ->
    regenerate, and the re-save after regenerating ch 1 must be [1], not [1,2]."""
    from novel_engine.core.checkpoint import CheckpointManager, create_commit_transaction
    from novel_engine.pipeline.production_runner import (
        compute_resume_plan,
        is_chapter_committed,
        load_resume_state,
        save_resume_state,
    )
    save_resume_state(tmp_path, [1, 2])
    plan = compute_resume_plan(0, load_resume_state(tmp_path), 1)
    assert plan["fresh"] is False and plan["done"] == []
    assert not is_chapter_committed(tmp_path, 1)
    assert not is_chapter_committed(tmp_path, 2)
    # Simulate the runner loop regenerating chapter 1 then saving.
    done = list(plan["done"])
    create_commit_transaction(
        CheckpointManager(tmp_path), tmp_path, 1,
        novel_content="novel 1", synopsis_content="syn 1",
        outline_content="{}", world_state_snapshot={"w": 1},
    )
    assert is_chapter_committed(tmp_path, 1)
    if 1 not in done:
        done.append(1)
    save_resume_state(tmp_path, done)
    assert load_resume_state(tmp_path) == {"done": [1], "last_success_chapter": 1}

def test_fresh_reset_clears_runner_resume_files(tmp_path):
    """D2 fix F2: a true fresh reset deletes the runner-owned resume files."""
    from novel_engine.pipeline.production_runner import load_resume_state, save_resume_state
    from novel_engine.pipeline.reset_state import reset_runtime_state
    save_resume_state(tmp_path, [1, 2])
    legacy = tmp_path / "audit" / "resume_state.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text('{"done": [1]}', encoding="utf-8")
    reset_runtime_state(tmp_path)
    assert not (tmp_path / "runtime" / "resume_state.json").exists()
    assert not (tmp_path / "runtime" / "last_success_chapter.txt").exists()
    assert not legacy.exists()
    assert load_resume_state(tmp_path) == {"done": [], "last_success_chapter": 0}
