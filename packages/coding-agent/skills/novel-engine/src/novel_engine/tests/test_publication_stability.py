"""Fault-injected publication, recovery, and sequential world-state barriers."""
import json
import os
import random
import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from novel_engine.core.checkpoint import CheckpointManager, create_commit_transaction
from novel_engine.core.llm_client import MockLLMClient, LLMClient
from novel_engine.core.execution import ContextExecutor
from novel_engine.core.publication_transaction import publication_transaction
from novel_engine.core.state_machine import ChapterPhase
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
from novel_engine.pipeline.production_runner import is_chapter_committed


def publish(manager, chapter, text="合格正文。"):
    return create_commit_transaction(manager, manager.root, chapter, text, "剧情摘要。", "{}", {"chapter": chapter})


def test_draft_is_not_published(tmp_path):
    manager = CheckpointManager(tmp_path)
    manager.create_draft_checkpoint(1, "等待人工审核。")
    assert manager.verify_integrity(1)
    assert not manager.verify_published(1)
    assert not is_chapter_committed(tmp_path, 1)
    publish(manager, 1)
    assert manager.verify_published(1)
    assert manager.get_checkpoint_by_chapter(1).get("mode") != "force_best_draft"


@pytest.mark.parametrize("after_save", [False, True])
def test_checkpoint_failure_restores_published_files(tmp_path, monkeypatch, after_save):
    manager = CheckpointManager(tmp_path)
    publish(manager, 1, "原有已发布正文。")
    before = manager.checkpoint_path.read_bytes()
    backup = tmp_path / "runtime/backups/chapters/novel/chapter_1.txt"
    backup_before = backup.read_bytes()
    original = manager.create_checkpoint
    def fail(**kwargs):
        if after_save:
            original(**kwargs)
        raise OSError("injected checkpoint failure")
    monkeypatch.setattr(manager, "create_checkpoint", fail)
    with pytest.raises(OSError):
        publish(manager, 1, "不应留下的新版本。")
    assert (tmp_path / "chapters/novel/chapter_1.txt").read_text() == "原有已发布正文。"
    assert manager.checkpoint_path.read_bytes() == before
    assert backup.read_bytes() == backup_before
    assert manager.verify_published(1)


def test_partial_file_write_rolls_back_without_publishing(tmp_path, monkeypatch):
    from novel_engine.core import checkpoint as module
    manager = CheckpointManager(tmp_path)
    original = module.atomic_write
    def fail(path, content):
        if Path(path) == tmp_path / "chapters/outline/chapter_1.json":
            raise OSError("injected file failure")
        return original(path, content)
    monkeypatch.setattr(module, "atomic_write", fail)
    with pytest.raises(OSError):
        publish(manager, 1)
    assert not (tmp_path / "chapters/novel/chapter_1.txt").exists()
    assert not manager.verify_published(1)


def test_crashed_process_is_rolled_back_before_resume(tmp_path):
    manager = CheckpointManager(tmp_path)
    publish(manager, 1, "原版本。")
    script = tmp_path / "crash.py"
    script.write_text('''import os, sys
from pathlib import Path
from novel_engine.core.publication_transaction import publication_transaction
root=Path(sys.argv[1])
path=root/'chapters/novel/chapter_1.txt'
with publication_transaction(root,1,[path]):
    path.write_text('写到一半的版本。')
    os._exit(17)
''')
    process = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True)
    assert process.returncode == 17
    assert (tmp_path / "chapters/novel/chapter_1.txt").read_text() != "原版本。"
    recovered = CheckpointManager(tmp_path)
    assert (tmp_path / "chapters/novel/chapter_1.txt").read_text() == "原版本。"
    assert recovered.verify_published(1)


def test_sqlite_and_json_roll_back_together(tmp_path):
    db_path = tmp_path / "state.db"
    connection = sqlite3.connect(db_path)
    connection.execute("CREATE TABLE state (value TEXT)")
    connection.execute("INSERT INTO state VALUES ('before')")
    connection.commit()
    json_path = tmp_path / "state.json"
    json_path.write_text('{"value":"before"}')
    with pytest.raises(OSError):
        with publication_transaction(tmp_path, 1, [json_path], {db_path: connection}):
            connection.execute("UPDATE state SET value='after'")
            connection.commit()
            json_path.write_text('{"value":"after"}')
            raise OSError("injected downstream failure")
    assert connection.execute("SELECT value FROM state").fetchone()[0] == "before"
    assert json.loads(json_path.read_text())["value"] == "before"
    connection.close()


def runner(tmp_path):
    obj = PipelineOrchestrator(tmp_path, llm_client=MockLLMClient())
    obj.simulator.characters = {"characters": {"C001": {"name": "陆烬", "realm": "凡人", "location": "村外"}}}
    obj.simulator._save_characters()
    obj.simulator._save_power_system()
    obj.simulator.db.import_from_json()
    return obj


def commit(obj, chapter, location):
    obj.current_novel = f"第{chapter}章的合格正文。"
    obj.state_machine.current_chapter = chapter
    obj.state_machine.current_phase = ChapterPhase.REVIEW
    obj.state_machine.state_log[str(chapter)] = {"phase": "REVIEW"}
    result = {"success": True, "score": 95, "errors": []}
    card = {"chapter_num": chapter, "core_goal": "抵达新地点", "scene_blueprints": [],
            "chapter_events": [{"event_type": "移动", "one_line_summary": "陆烬抵达新地点", "participants": ["陆烬"], "location": location}],
            "end_state": {"location": location, "narrative_position": "抵达地点", "time_marker": "当日"}}
    synopsis = {"synopsis": "陆烬抵达新地点。", "state_changes": [{"type": "character_location", "target": "C001", "new_value": location}]}
    obj._commit_chapter(chapter, card, synopsis, {}, result, 95, obj.state_machine, apply_world_state=True)
    return result


def test_three_commits_survive_restart_with_consistent_world(tmp_path):
    obj = runner(tmp_path)
    for chapter in range(1, 4):
        result = commit(obj, chapter, f"地点{chapter}")
        assert result["success"] and result["published"], result
        assert is_chapter_committed(tmp_path, chapter)
        assert obj.simulator.characters["characters"]["C001"]["location"] == f"地点{chapter}"
        assert obj.state_machine.state_log[str(chapter)]["commit_hash"]
    obj.close()
    restarted = PipelineOrchestrator(tmp_path, llm_client=MockLLMClient())
    assert restarted.simulator.characters["characters"]["C001"]["location"] == "地点3"
    assert all(restarted.checkpoint_mgr.verify_published(chapter) for chapter in range(1, 4))
    restarted._stage_world_sim = Mock(side_effect=AssertionError("must not regenerate"))
    assert restarted.generate_single_chapter(3)["skipped_committed"]
    restarted.close()


def test_commit_failure_restores_world_and_blocks_next_chapter(tmp_path, monkeypatch):
    obj = runner(tmp_path)
    monkeypatch.setattr(obj.memory, "update_chapter_index", Mock(side_effect=OSError("injected indexing failure")))
    result = commit(obj, 1, "不应生效的地点")
    assert result["commit_failed"] and not result["success"] and not result["published"]
    assert obj.simulator.characters["characters"]["C001"]["location"] == "村外"
    assert json.loads((tmp_path / "memory/world_state/characters.json").read_text())["characters"]["C001"]["location"] == "村外"
    assert obj.simulator.db.conn.execute("SELECT location FROM characters WHERE id='C001'").fetchone()[0] == "村外"
    assert not is_chapter_committed(tmp_path, 1)
    obj._stage_world_sim = Mock(side_effect=AssertionError("must block before generation"))
    assert obj.generate_single_chapter(2)["predecessor_blocked"]
    obj.close()


def test_shared_http_pool_survives_worker_threads_and_closes():
    client = LLMClient()
    with ContextExecutor(max_workers=4) as pool:
        sessions = list(pool.map(lambda _: client._stream_client(), range(16)))
    assert len({id(session) for session in sessions}) == 1
    client.close()
    assert sessions[0].is_closed


def test_prompt_length_budget_scales_with_scene_target():
    from novel_engine.agents.scene_schema import build_scene_prompt
    blueprint = {"scene_num": 1, "word_count_target": 2500, "beats": ["起因", "经过", "结果"]}
    prompt = build_scene_prompt({"chapter_num": 1}, blueprint, [blueprint])
    assert "每拍约834字" in prompt
    assert "每拍约150-250字" not in prompt
    assert "正文硬性不少于 1750" in prompt


def test_status_write_failure_prevents_false_publish(tmp_path, monkeypatch):
    obj = runner(tmp_path)
    monkeypatch.setattr("novel_engine.pipeline.pipeline_orchestrator.set_status", lambda *a, **k: None)
    result = commit(obj, 1, "不应发布")
    assert result["commit_failed"] and not result["published"]
    assert not is_chapter_committed(tmp_path, 1)
    assert obj.simulator.characters["characters"]["C001"]["location"] == "村外"
    obj.close()


def test_quality_replan_discards_frozen_plan_and_archives_scenes(tmp_path, monkeypatch):
    obj = runner(tmp_path)
    obj._frozen_task_cards[1] = {"chapter_num": 1, "core_goal": "旧任务"}
    obj._frozen_synopsis[1] = {"synopsis": "旧梗概"}
    journal = tmp_path / "chapters/draft/chapter_1_partial.jsonl"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text('旧任务的场景审计记录')
    monkeypatch.setenv("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER", "9")
    calls = []
    def generate(chapter):
        calls.append(chapter)
        if len(calls) == 1:
            return {"quality_replan": True}
        assert chapter not in obj._frozen_task_cards
        assert chapter not in obj._frozen_synopsis
        assert not journal.exists()
        assert not obj.writer.last_scenes
        assert os.environ["NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER"] == "1"
        return {"success": True}
    monkeypatch.setattr(obj, "generate_single_chapter", generate)
    assert obj._run_with_retry(1)["success"]
    assert os.environ["NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER"] == "9"
    archived = list((tmp_path / "runtime/replans").glob("*/chapter_1_partial.jsonl"))
    assert len(archived) == 1 and archived[0].read_text() == '旧任务的场景审计记录'
    obj.close()


def test_polish_prompt_contains_actual_repair_and_length_budget(tmp_path):
    from novel_engine.agents.scene_schema import SceneOutput
    from novel_engine.agents.writer_agent import WriterAgent
    text = "陆烬推开木门，看见院里的石阶。" * 40
    client = Mock()
    client.concurrency = 1
    client.chat_completion.return_value = {"content": text}
    writer = WriterAgent(llm_client=client, project_root=tmp_path)
    writer.polish_scenes([SceneOutput(1, text, "")], {"timeline_anchor": {"time": "全程夜间"}}, issues_by_id={1: ["high_freq_repeat: 石阶"]})
    prompt = client.chat_completion.call_args.args[0][0]["content"]
    assert "high_freq_repeat: 石阶" in prompt
    assert f"本场原稿{len(text)}字" in prompt
    assert "90%–110%" in prompt
    assert "全程夜间" in prompt


def test_coherent_short_scene_uses_expansion_without_redundant_regeneration(tmp_path):
    from novel_engine.agents.scene_schema import SceneOutput
    obj = runner(tmp_path)
    rng = random.Random(42)
    text = "".join(chr(rng.randint(0x4e00, 0x9fa5)) for _ in range(679)) + "。"
    scenes = [SceneOutput(1, text, "")]
    card = {"chapter_num": 100, "scene_blueprints": [{"scene_num": n} for n in range(1, 5)]}
    obj._regen_scene_for_id = Mock(side_effect=AssertionError("short prose must reach bounded expansion"))
    obj._validate_and_regen_scenes(scenes, card, 100)
    obj._regen_scene_for_id.assert_not_called()
    assert scenes[0].scene_text == text
    obj.close()


def test_real_production_entry_preserves_phase_specific_routing(tmp_path, monkeypatch):
    from novel_engine.pipeline import production_runner as module
    (tmp_path / "config").mkdir()
    (tmp_path / "config/runtime_config.json").write_text('{}')
    factory = Mock()
    monkeypatch.setattr(module, "PipelineOrchestrator", factory)
    module._create_production_orchestrator(tmp_path, True)
    factory.assert_called_once_with(tmp_path)


def test_optional_scene_admission_preserves_budget_for_required_review():
    from novel_engine.core.execution import AttemptBudget
    budget = AttemptBudget(1, max_scene_attempts=1)
    assert budget.can_scene(2)
    budget.scene(2)
    assert not budget.can_scene(2)
    assert budget.reason is None
    budget.review()
    budget.review(vote=True)
    budget.request()
    assert budget.calls == 1 and budget.review_calls == 1


def test_review_temperature_does_not_inherit_generation_sampling(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config/runtime_config.json").write_text(json.dumps({"review_llm": {"temperature": 0, "use_mock": False}}))
    obj = runner(tmp_path)
    assert obj.reviewer.provider_config.retry_temperatures == [0.0]
    assert obj.provider_cfg.retry_temperatures != [0.0]
    obj.close()


@pytest.mark.parametrize("deterministic_only", [True, False])
def test_synthetic_gate_score_does_not_trigger_literary_low_score_rewrite(tmp_path, deterministic_only):
    from novel_engine.pipeline.gates import BestCandidateState
    obj = runner(tmp_path)
    obj._patch_weak_scenes = Mock(return_value=None)
    obj._cc25_literary_rewrite = Mock(return_value=None)
    obj._low_score_rewrite = Mock(return_value=True)
    obj._last_deterministic_issues = []
    score = 0 if deterministic_only else 50
    best = BestCandidateState(best_score=score, best_novel="原稿。", best_draft="原稿。", best_journal_snapshot=None)
    staged = {"score": score, "review": {"deterministic_only": deterministic_only, "issues": [], "scores": {}}}
    card = {"chapter_num": 1, "scene_blueprints": []}
    result = obj._dispatch_remediation(1, card, {}, "原稿。", "原稿。", best, 1, card, staged, {})
    if deterministic_only:
        obj._low_score_rewrite.assert_not_called()
        assert result[4] == "rewrite_disabled"
    else:
        obj._low_score_rewrite.assert_called_once()
        assert result[4] is None
    obj.close()


def test_generation_constraints_follow_arc_night_boundary_and_config_updates(tmp_path):
    from novel_engine.quality.generation_constraints import generation_constraints
    directory = tmp_path / "config/leak_terms"
    directory.mkdir(parents=True)
    config = directory / "arc.json"
    cfg = {"chapters": [1, 5], "hard_block": ["经脉"], "night_anchor_markers": ["子时"], "dawn_markers": ["黎明"]}
    config.write_text(json.dumps(cfg))
    card = {"chapter_num": 1, "timeline_anchor": {"chapter_start_marker": "子时"}}
    prompt = generation_constraints(tmp_path, card)
    assert "经脉" in prompt and "黎明" in prompt and "子时" in prompt
    assert "经脉" not in generation_constraints(tmp_path, dict(card, chapter_num=10))
    assert "天亮意象" not in generation_constraints(tmp_path, dict(card, timeline_anchor={"max_time_progression": "第二天"}))
    cfg["hard_block"] = ["另一个设定词"]
    config.write_text(json.dumps(cfg))
    updated = generation_constraints(tmp_path, card)
    assert "另一个设定词" in updated and "经脉" not in updated


def test_raw_repair_router_injects_frozen_constraints_without_mutating_messages(tmp_path):
    from novel_engine.core.execution import chapter_run
    from novel_engine.core.model_router import ModelRouter
    directory = tmp_path / "config/leak_terms"
    directory.mkdir(parents=True)
    (directory / "arc.json").write_text(json.dumps({"chapters": [1, 5], "hard_block": ["经脉"]}))
    obj = runner(tmp_path)
    obj._frozen_task_cards[1] = {"chapter_num": 1, "timeline_anchor": {"chapter_start_marker": "子时"}}
    client = Mock(spec=["model", "chat_completion"])
    client.model = "mock"
    client.chat_completion.return_value = {"content": "修复正文。"}
    router = ModelRouter("refine", tmp_path, llm_client=client)
    messages = [{"role": "user", "content": "修正文风。"}]
    @chapter_run
    def repair(owner, chapter):
        router.chat_completion(messages)
        return {"success": True}
    assert repair(obj, 1)["success"]
    sent = client.chat_completion.call_args.args[0]
    assert sent[0]["role"] == "system"
    assert "经脉" in sent[0]["content"] and "子时" in sent[0]["content"]
    assert messages == [{"role": "user", "content": "修正文风。"}]
    obj.close()


@pytest.mark.parametrize("defect", ["latin", "short"])
def test_optional_anchor_repair_keeps_valid_journal_on_regression(tmp_path, defect):
    from novel_engine.agents.scene_schema import SceneOutput
    from novel_engine.pipeline.chapter_journal import append_scene, journal_path
    obj = runner(tmp_path)
    original = "老者推开木门，看见石阶上落下了一片叶子。" * 40
    for sid in (1, 2):
        append_scene(tmp_path, 1, {"scene_id": sid, "scene_text": original})
    before = journal_path(tmp_path, 1).read_bytes()
    replacement = original + " unanimous。" if defect == "latin" else "他停下来。"
    obj.writer.generate_scene = Mock(return_value=SceneOutput(2, replacement, ""))
    obj.current_novel = original + "\n\n" + original
    card = {"chapter_num": 1,
            "must_cover_beats": [{"scene_num": 2, "beat_text": "祭台锚点", "category": "opening_anchor"}],
            "scene_blueprints": [
        {"scene_num": 1, "location": "屋里", "goal": "等待", "beats": []},
        {"scene_num": 2, "location": "村口", "goal": "前往村口", "beats": []}]}
    obj._enforce_mandatory_beats(1, card)
    obj.writer.generate_scene.assert_called_once()
    assert journal_path(tmp_path, 1).read_bytes() == before
    assert replacement not in obj.current_novel
    obj.close()


def test_covered_optional_anchor_does_not_regenerate_valid_scene(tmp_path):
    from novel_engine.pipeline.chapter_journal import append_scene, journal_path
    obj = runner(tmp_path)
    text = "石阶旁的老者抬起头。"
    append_scene(tmp_path, 1, {"scene_id": 2, "scene_text": text})
    before = journal_path(tmp_path, 1).read_bytes()
    obj.current_novel = text
    obj.writer.generate_scene = Mock(side_effect=AssertionError("covered anchor must not regenerate"))
    card = {"chapter_num": 1, "scene_blueprints": [{"scene_num": 2, "location": "石阶"}],
            "must_cover_beats": [{"scene_num": 2, "beat_text": "石阶", "category": "opening_anchor"}]}
    obj._enforce_mandatory_beats(1, card)
    obj.writer.generate_scene.assert_not_called()
    assert journal_path(tmp_path, 1).read_bytes() == before
    obj.close()


def test_character_context_includes_attributes_below_matching_heading(tmp_path):
    from novel_engine.agents.writer_agent import WriterAgent
    obj = WriterAgent(llm_client=MockLLMClient(), project_root=tmp_path)
    obj._bible_cache["character"] = "### C001 陆烬\n- 身份：婴孩。\n\n### C002 陈老根\n- 身份：猎户。\n- 工具：黑角弓。\n\n### C003 他人\n- 工具：拐杖。"
    context = obj._locate_bible_by_names("character", ["C002"])
    assert "黑角弓" in context and "猎户" in context
    assert "拐杖" not in context and "婴孩" not in context


def test_scene_prompt_receives_only_its_mandatory_beats_and_final_confirmation():
    from novel_engine.agents.scene_schema import build_scene_prompt
    blueprints = [{"scene_num": 1, "goal": "降生", "beats": ["落地"]}, {"scene_num": 2, "goal": "救助", "beats": ["抱起"]}]
    card = {"chapter_num": 1, "must_cover_beats": [
        {"scene_num": 1, "category": "foreshadow", "beat_text": "迷雾形成漩涡"},
        {"scene_num": 2, "category": "opening_anchor", "beat_text": "村口夜色锚点"},
        {"scene_num": None, "category": "confirmation", "beat_text": "终章确认"}]}
    first = build_scene_prompt(card, blueprints[0], blueprints)
    last = build_scene_prompt(card, blueprints[1], blueprints)
    assert "迷雾形成漩涡" in first and "村口夜色锚点" not in first and "终章确认" not in first
    assert "村口夜色锚点" in last and "终章确认" in last and "迷雾形成漩涡" not in last


def test_scene_generation_selects_local_character_context(tmp_path):
    from novel_engine.agents.writer_agent import WriterAgent
    client = Mock(spec=["model", "chat_completion"])
    client.model = "mock"
    client.chat_completion.return_value = {"content": json.dumps({"scene_id": 2, "scene_text": "老者关好木门。" * 50, "beats": []})}
    obj = WriterAgent(llm_client=client, project_root=tmp_path)
    obj._bible_snippet = Mock(return_value="")
    blueprints = [{"scene_num": 1, "characters": ["C001"], "location": "屋里"}, {"scene_num": 2, "characters": ["C002"], "location": "村口"}]
    card = {"chapter_num": 1, "scene_blueprints": blueprints,
            "foreshadow_actions": [{"foreshadow_id": "F001", "action": "迷雾在降生瞬间产生漩涡"}]}
    obj.generate_scene(card, blueprints[1], "村民发现婴儿，陈老根决定救助。")
    prompt = client.chat_completion.call_args.args[0][1]["content"]
    assert "村民发现婴儿，陈老根决定救助。" in prompt
    assert "迷雾在降生瞬间产生漩涡" in prompt
    assert "禁止跨场重复" in prompt
    obj._bible_snippet.assert_called_once_with(chapter_num=1, character_names=["C002"], location_names=["村口"])


@pytest.mark.parametrize("exhausted", [False, True])
def test_many_scene_issues_repair_highest_priority_within_two_scene_limit(tmp_path, exhausted):
    from novel_engine.agents.scene_schema import SceneOutput
    from novel_engine.pipeline.chapter_journal import append_scene
    obj = runner(tmp_path)
    for sid in (1, 2, 3):
        append_scene(tmp_path, 1, {"scene_id": sid, "scene_text": f"第{sid}场原稿正文。"})
    obj._scene_regen_used = {(1, 3): 2} if exhausted else {}
    obj.writer.generate_scene = Mock(side_effect=lambda card, bp, *a, **k: SceneOutput(bp['scene_num'], "修订后的正文。" * 50, ""))
    card = {"chapter_num": 1, "scene_blueprints": [{"scene_num": sid} for sid in (1, 2, 3)]}
    review = {"issues": [
        {"scene_ids": [1], "severity": "low", "dimension": "plot_consistency", "description": "时间线矛盾", "suggested_fix": "保持当夜"},
        {"scene_ids": [2], "severity": "high", "dimension": "foreshadow_execution", "description": "伏笔缺失", "suggested_fix": "写出漩涡"},
        {"scene_ids": [3], "severity": "critical", "dimension": "character_consistency", "description": "人物道具矛盾", "suggested_fix": "统一工具"}]}
    patched = obj._patch_weak_scenes("原稿正文。", review, card, {}, chapter_num=1)
    assert patched
    calls = obj.writer.generate_scene.call_args_list
    assert len(calls) == 2
    assert {call.args[1]['scene_num'] for call in calls} == ({1, 2} if exhausted else {2, 3})
    assert all("请【新增】" not in call.kwargs['fix_directive'] for call in calls)
    assert all(f"第{call.args[1]['scene_num']}场原稿正文。" in call.kwargs['fix_directive'] for call in calls)
    for call in calls:
        sid = call.args[1]['scene_num']
        if sid > 1:
            repaired_before = any(previous.args[1]['scene_num'] == sid - 1 for previous in calls[:calls.index(call)])
            expected = "修订后的正文。" if repaired_before else f"第{sid - 1}场原稿正文。"
            assert expected in call.args[3]
        if sid < 3:
            assert f"第{sid + 1}场原稿正文。" in call.args[3]
    obj.close()


def test_failed_scene_patch_preserves_other_successful_scene_and_counts_attempts(tmp_path):
    from novel_engine.agents.scene_schema import SceneOutput
    from novel_engine.pipeline.chapter_journal import append_scene, load_authoritative_scenes
    obj = runner(tmp_path)
    for sid in (1, 2):
        append_scene(tmp_path, 1, {"scene_id": sid, "scene_text": f"原有第{sid}场正文。"})
    def generate(card, bp, *args, **kwargs):
        if bp['scene_num'] == 1:
            raise OSError("injected scene failure")
        return SceneOutput(2, "第二场成功修订。" * 40, "")
    obj.writer.generate_scene = Mock(side_effect=generate)
    card = {"chapter_num": 1, "scene_blueprints": [{"scene_num": 1}, {"scene_num": 2}]}
    review = {"issues": [{"dimension": "character_consistency", "scene_ids": [sid], "severity": "high", "description": "统一人物行为"} for sid in (1, 2)]}
    assert obj._patch_weak_scenes("旧稿。", review, card, {}, chapter_num=1)
    saved = {s['scene_id']: s['scene_text'] for s in load_authoritative_scenes(tmp_path, 1)}
    assert saved[1] == "原有第1场正文。"
    assert saved[2].startswith("第二场成功修订。")
    assert obj._scene_regen_used == {(1, 1): 1, (1, 2): 1}
    obj.close()
