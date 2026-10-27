"""Contract, commit, resume and portable-export regressions; no paid calls."""
import json
import re
import zipfile
import subprocess
import sys
from pathlib import Path

import pytest

from novel_engine.condensed.contracts import (ProductionConfig, REVIEW_CHECKS, assign_plan_budgets, assign_scene_budgets, bind_review_evidence, digest,
                                             han_count, validate_chapter, validate_plan,
                                             validate_review)
from novel_engine.condensed.export import archive_exports, compile_manuscript, export_chapter, verify_archive, verify_export
from novel_engine.condensed.context import scope_canon
from novel_engine.condensed.runner import CondensedPipeline, DurableBudget, QualityFailure, load_canon
from novel_engine.condensed.render import generation_view, render_brief
from novel_engine.condensed.store import Store
from novel_engine.condensed.timeline import chinese_number, normalize_timeline, validate_timeline
from novel_engine.core.execution import BudgetExceeded


SOURCE = Path(__file__).resolve().parents[1]


def plan(chapter=1, target=4210):
    return {"chapter": chapter, "objective": "发现账目缺页并保留证据", "cast": ["陆烬"],
            "events": [{"id": f"E{i}", "action": "核对账目", "cause": "收支不符", "outcome": "取得线索"} for i in range(1, 4)],
            "ending": "保存账册准备次日询问", "boundaries": ["不能提前确定作案者"], "expanded_han": target}


def draft(chapter=1, target=4210, before=None):
    scenes = []
    for i in range(2):
        scenes.append({"id": f"S{i}", "location": "账房", "time": "当晚", "pov": "陆烬", "cast": ["陆烬"],
                       "start": "账目待核", "end": "保存线索", "emotion": "警惕转为疑惑", "expanded_han": target // 2 + (target % 2 if i else 0),
                       "expansion_guidance": "展开核对过程，不补造作案者", "dialogue_intents": ["独处，无对白"],
                       "text": "陆烬核对账册，发现缺页。他先记录现有数字，再检查装订痕迹，决定暂不声张。" * 9,
                       "beats": [{"id": f"B{i}-{j}", "event_ids": [f"E{j}"], "action": "核对数字",
                                  "cause": "收支不符", "outcome": "发现缺页"} for j in ([1, 2] if i == 0 else [3])]})
    return {"chapter": chapter, "title": "缺页", "story_day": chapter,
            "scenes": scenes, "state_changes": [{"entity": "陆烬", "key": "掌握线索", "before": before,
                                                 "after": f"第{chapter}页缺失", "evidence_beat": "B0-1"}],
            "ending": "保存账册，尚未确定作案者"}


def review(payload=None):
    result = {"input_hash": "input", "checks": dict.fromkeys(REVIEW_CHECKS, True), "issues": []}
    if payload is not None:
        result["chapter_hash"] = digest(payload)
    return result


def committed(store, chapter=1):
    value = draft(chapter, before=None if chapter == 1 else f"第{chapter-1}页缺失")
    value.update(plan=plan(chapter), context=store.context(["陆烬"]))
    store.commit(value, review(value))
    return value


def test_exact_16_million_budget():
    config = ProductionConfig()
    budgets = [config.chapter_budget(n) for n in range(1, 3801)]
    assert sum(budgets) == 16_000_000
    assert min(budgets) >= 4000 and max(budgets) <= 5000
    assert max(budgets) - min(budgets) == 1


@pytest.mark.parametrize("settings", [{"chapters": True}, {"total_expanded_han": 1}, {"repair_rounds": -1}, {"expanded_max": 3000}])
def test_invalid_production_budget(settings):
    with pytest.raises(ValueError):
        ProductionConfig(**settings)


def test_han_count_excludes_punctuation_ascii_and_numbers():
    assert han_count("中文，abc123。\n𠀀") == 3


def test_plan_coverage_and_budget():
    config = ProductionConfig()
    first = plan(target=config.chapter_budget(1))
    assert not validate_plan({"chapters": [first]}, 1, 1, config)
    assert validate_plan({"chapters": [first, first]}, 1, 2, config)
    first["expanded_han"] = 10000
    assert validate_plan({"chapters": [first]}, 1, 1, config)


def test_real_plan_mixed_array_does_not_crash_validator():
    assert validate_plan({"chapters": [plan(), "blocked_reasons"]}, 1, 1, ProductionConfig())


def test_budget_allocation_is_program_owned_and_keeps_scene_proportions():
    config = ProductionConfig()
    value = {"chapters": [plan(target=10000)]}
    assign_plan_budgets(value, config)
    assert value["chapters"][0]["expanded_han"] == config.chapter_budget(1)
    value = draft()
    value["scenes"][0]["expanded_han"] = 2000
    value["scenes"][1]["expanded_han"] = 2200
    assign_scene_budgets(value, 4211)
    assert sum(s["expanded_han"] for s in value["scenes"]) == 4211
    assert value["scenes"][1]["expanded_han"] > value["scenes"][0]["expanded_han"]
    value["scenes"][0]["expanded_han"] = 9000
    assign_scene_budgets(value, 4211)
    assert sum(s["expanded_han"] for s in value["scenes"]) == 4211
    assert all(s["expanded_han"] >= 300 for s in value["scenes"])


def test_all_original_milestones_preserved_with_valid_dependencies():
    canon = load_canon(SOURCE)
    old = json.loads((SOURCE / "config/planning/plot_graph.json").read_text())
    nodes = {node["id"]: node for node in canon["milestones"]}
    assert set(nodes) == {node["id"] for node in old["nodes"]}
    for node in nodes.values():
        assert all(nodes[dep]["chapter_target"] <= node["chapter_target"] for dep in node["depends_on"])
    assert "不得传授" in nodes["N004"]["description"]


def test_repeated_events_across_three_chapters_rejected():
    config = ProductionConfig()
    entries = [plan(n, config.chapter_budget(n)) for n in range(1, 4)]
    assert any("重复事件" in issue for issue in validate_plan({"chapters": entries}, 1, 3, config))


@pytest.mark.parametrize("change", ["coverage", "state", "time", "budget", "evidence", "cast"])
def test_reject_incomplete_or_inconsistent_drafts(change):
    value = draft()
    assert not validate_chapter(value, plan(), {}, 0)
    if change == "coverage":
        value["scenes"][1]["beats"] = []
    elif change == "state":
        value["state_changes"][0]["before"] = "虚构旧状态"
    elif change == "time":
        value["story_day"] = -1
    elif change == "budget":
        value["scenes"][0]["expanded_han"] = 10000
    elif change == "evidence":
        value["state_changes"][0]["evidence_beat"] = "missing"
    elif change == "cast":
        value["scenes"][0]["cast"].append("未来人物")
    assert validate_chapter(value, plan(), {}, 0)


def test_short_text_is_not_rejected_by_an_arbitrary_length_gate():
    value = draft()
    for scene in value["scenes"]:
        scene["text"] = "陆烬发现账目缺页，记录数字和装订痕迹，保存证据，暂不判断作案者。"
    assert not validate_chapter(value, plan(), {}, 0)


def test_multiple_state_evidence_references_are_all_checked():
    value = draft()
    value["state_changes"][0]["evidence_beat"] = "B0-1, B1-3"
    assert not validate_chapter(value, plan(), {}, 0)
    value["state_changes"][0]["evidence_beat"] += ", missing"
    assert validate_chapter(value, plan(), {}, 0)


def test_context_scope_keeps_transition_rules_and_all_foreshadow_windows():
    canon = load_canon(SOURCE)
    scoped = scope_canon(canon, 311, 320)
    assert "第一阶段" in scoped["intent"] and "第二阶段" in scoped["intent"]
    assert "第十阶段" not in scoped["intent"]
    assert "第十阶段" in scoped["whole_book_roadmap"]
    assert scoped["ending"] == canon["ending"]
    assert any(node["id"] == "N015" for node in scoped["milestones"])
    assert scoped["scope"]["full_canon_hash"] == canon["hash"]


@pytest.mark.parametrize("change", ["hash", "missing", "boolean", "evidence", "contradiction"])
def test_review_fails_closed(change):
    value = review()
    if change == "hash":
        value["input_hash"] = "other"
    elif change == "missing":
        del value["checks"]["knowledge"]
    elif change == "boolean":
        value["checks"]["coverage"] = "true"
    elif change == "evidence":
        value["checks"]["coverage"] = False
    else:
        value["issues"] = [{"check": "canon", "location": "S1", "evidence": "事实错误", "repair": "修改事实"}]
    with pytest.raises(ValueError):
        validate_review(value, "input")


def test_atomic_commit_rolls_back_text_and_state(tmp_path):
    store = Store(tmp_path)
    committed(store)
    value = draft(2, before="错误前值")
    with pytest.raises(ValueError):
        store.commit(value, review(value))
    assert store.prefix() == 1
    assert store.state()[("陆烬", "掌握线索")] == "第1页缺失"
    assert store.chapter(2) is None
    store.close()


def test_commit_needs_matching_review_and_predecessor(tmp_path):
    store = Store(tmp_path)
    with pytest.raises(ValueError):
        store.commit(draft(2), review(draft(2)))
    value = draft()
    checked = review(value)
    value["ending"] = "改变结局"
    with pytest.raises(ValueError):
        store.commit(value, checked)
    assert store.prefix() == 0
    store.close()


def test_tampered_commit_chain_is_detected(tmp_path):
    store = Store(tmp_path)
    committed(store)
    store.db.execute("UPDATE chapters SET payload='{}'")
    store.db.commit()
    with pytest.raises(ValueError):
        store.prefix()
    store.close()


def test_tampered_review_is_not_a_formal_commit(tmp_path):
    store = Store(tmp_path)
    committed(store)
    bad = review(store.chapter(1)["payload"])
    bad["checks"]["canon"] = False
    store.db.execute("UPDATE chapters SET review=?", (json.dumps(bad),))
    store.db.commit()
    with pytest.raises(ValueError):
        store.prefix()
    store.close()


def test_collection_contains_only_committed_chapters(tmp_path):
    store = Store(tmp_path)
    committed(store)
    (tmp_path / "drafts").mkdir()
    (tmp_path / "drafts/chapter_2.json").write_text('{"text":"uncommitted draft"}')
    directory = compile_manuscript(store, ProductionConfig())
    text = (directory / "全部章节简写.md").read_text()
    assert "1/3800" in text and "uncommitted draft" not in text
    assert "第1章" in text and "第2章" not in text
    store.close()


def test_resume_cannot_reset_physical_request_budget(tmp_path):
    settings = ProductionConfig(max_calls_per_operation=2)
    store = Store(tmp_path)
    first = DurableBudget(store, "chapter_1", settings)
    first.request()
    store.save_wall("chapter_1", 20)
    store.close()
    store = Store(tmp_path)
    resumed = DurableBudget(store, "chapter_1", settings)
    assert resumed.remaining() < settings.max_seconds_per_operation - 19
    resumed.request()
    with pytest.raises(BudgetExceeded):
        resumed.request()
    assert store.operation("chapter_1")[0] == 2
    store.close()


def test_portable_package_includes_complete_prompts_and_checksums(tmp_path):
    store = Store(tmp_path / "project")
    value = committed(store)
    canon = load_canon(SOURCE)
    directory = export_chapter(store, 1, canon, ProductionConfig())
    manifest = verify_export(directory)
    prompt = (directory / "扩写提示词.md").read_text()
    assert value["scenes"][0]["text"] in prompt
    assert canon["world"] in prompt and canon["style"] in prompt
    assert "4000至5000" in prompt and "4210" in prompt
    assert manifest["external_dependencies"] == []
    archive = archive_exports(store.root, tmp_path / "output.zip")
    with zipfile.ZipFile(archive) as zipped:
        assert "chapter_0001/扩写提示词.md" in zipped.namelist()
        assert not any("sqlite" in name or "production.log" in name for name in zipped.namelist())
    (directory / "章节简写.md").write_text("changed")
    with pytest.raises(ValueError):
        verify_export(directory)
    store.close()


def test_shared_batch_package_contains_all_dependencies_once(tmp_path):
    store = Store(tmp_path / "project")
    canon = load_canon(SOURCE)
    for number in (1, 2):
        committed(store, number)
        export_chapter(store, number, canon, ProductionConfig())
    archive = archive_exports(store.root, tmp_path / "shared.zip", compact=True, start=1, end=2)
    index = verify_archive(archive)
    assert index["chapters"] == [1, 2] and index["complete_for_requested_range"]
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.namelist().count("shared/全书设定.md") == 1
        assert "chapter_0001/全书设定.md" not in zipped.namelist()
        assert "shared/全书设定.md" in zipped.read("chapter_0002/扩写提示词.md").decode()
    store.close()


def test_exported_word_counter_runs_without_the_project(tmp_path):
    script = tmp_path / "count_han.py"
    script.write_bytes((SOURCE / "condensed/resources/count_han.py").read_bytes())
    prose = tmp_path / "正文.txt"
    for count, code in ((4000, 0), (3999, 1), (5001, 1)):
        prose.write_text("字" * count + ", abc 123\n", encoding="utf-8")
        result = subprocess.run([sys.executable, str(script), str(prose)], cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == code
        assert json.loads(result.stdout)["han"] == count


class FakeRouter:
    def __init__(self, failed=False):
        self.calls = 0
        self.failed = failed

    def chat_completion(self, messages, **kwargs):
        self.calls += 1
        text = messages[-1]["content"]
        if "执行叙事简写逻辑质检" in text:
            value = review()
            value["input_hash"] = re.search(r'"input_hash":"([a-f0-9]+)"', text)[1]
            candidate = json.JSONDecoder().raw_decode(text.split("\n审查对象：\n")[-1])[0]
            context = json.loads(text.split("\n设定及前章事实：\n")[-1].split("\n审查对象：\n")[0])
            task = context["plan"]
            beats = [beat for scene in candidate["scenes"] for beat in scene["beats"]]
            value["event_evidence"] = []
            for event in task["events"]:
                beat = next(beat for beat in beats if event["id"] in beat["event_ids"])
                value["event_evidence"].append({"event_id": event["id"], "covered": not self.failed,
                                              "beat_ids": [beat["id"]], "evidence": beat["action"], "reason": "逐项对照"})
            value["milestone_evidence"] = [{"milestone_id": node, "covered": not self.failed,
                                             "beat_ids": [beats[0]["id"]],
                                             "evidence": beats[0]["action"], "reason": "逐项对照"}
                                            for node in task.get("milestone_ids", [])]
            if self.failed:
                value["checks"]["coverage"] = False
                value["issues"] = [{"check": "coverage", "location": "S1", "evidence": "缺少必要动作", "repair": "补足动作"}]
        elif ("规划第" in text and "请生成本章完整叙事简写" not in text) or text.startswith("仅修复指定章节的规划"):
            config = ProductionConfig(plan_batch=1)
            value = {"chapters": [plan(target=config.chapter_budget(1))]}
            value["chapters"][0]["milestone_ids"] = ["N001"]
        else:
            value = draft(target=ProductionConfig().chapter_budget(1))
            for scene in value["scenes"]:
                for beat in scene["beats"]:
                    beat["event_ids"] = [f"CH1-{ref}" for ref in beat["event_ids"]]
        return {"content": json.dumps(value), "finish_reason": "stop"}


def test_end_to_end_commit_export_resume_without_extra_calls(tmp_path):
    generator, reviewer = FakeRouter(), FakeRouter()
    settings = ProductionConfig(plan_batch=1)
    pipeline = CondensedPipeline(SOURCE, tmp_path, settings, generator, reviewer)
    assert pipeline.generate(1)["committed"]
    calls = (generator.calls, reviewer.calls)
    pipeline.close()
    pipeline = CondensedPipeline(SOURCE, tmp_path, settings)
    assert pipeline.generate(1)["skipped"]
    assert calls == (2, 1)
    assert pipeline.status()["committed_chapters"] == 1
    assert not pipeline.status()["complete"]
    pipeline.close()


def test_failure_stops_without_publication_or_human_queue(tmp_path):
    reviewer = FakeRouter(failed=True)
    pipeline = CondensedPipeline(SOURCE, tmp_path, ProductionConfig(plan_batch=1), FakeRouter(), reviewer)
    with pytest.raises(QualityFailure):
        pipeline.generate(1)
    assert pipeline.store.prefix() == 0
    diagnostic = json.loads((tmp_path / "drafts/chapter_1.json").read_text())
    assert len(diagnostic["history"]) == 3
    assert "human" not in json.dumps(diagnostic)
    assert reviewer.calls == 1
    pipeline.close()


def test_mixed_scene_array_is_automatically_repaired_before_commit(tmp_path):
    class BrokenOnce(FakeRouter):
        def chat_completion(self, messages, **kwargs):
            result = super().chat_completion(messages, **kwargs)
            if self.calls == 2:
                value = json.loads(result["content"])
                value["scenes"][1] = "id：S2"
                result["content"] = json.dumps(value)
            return result
    generator = BrokenOnce()
    pipeline = CondensedPipeline(SOURCE, tmp_path, ProductionConfig(plan_batch=1), generator, FakeRouter())
    assert pipeline.generate(1)["committed"]
    assert generator.calls == 3
    assert all(isinstance(scene, dict) for scene in pipeline.store.chapter(1)["payload"]["scenes"])
    pipeline.close()


def test_review_format_correction_does_not_rewrite_the_chapter(tmp_path):
    class MissingEvidenceOnce(FakeRouter):
        def chat_completion(self, messages, **kwargs):
            result = super().chat_completion(messages, **kwargs)
            if self.calls == 1:
                value = json.loads(result["content"])
                value["event_evidence"] = value["event_evidence"][:1]
                result["content"] = json.dumps(value)
            return result
    generator, reviewer = FakeRouter(), MissingEvidenceOnce()
    pipeline = CondensedPipeline(SOURCE, tmp_path, ProductionConfig(plan_batch=1, repair_rounds=0), generator, reviewer)
    assert pipeline.generate(1)["committed"]
    assert generator.calls == 2 and reviewer.calls == 2
    saved = json.loads((tmp_path / "drafts/chapter_1.json").read_text())
    assert saved["format_repairs"] == 1
    pipeline.close()


def test_stale_trace_snapshot_cannot_erase_reported_usage(tmp_path):
    store = Store(tmp_path)
    for tokens in (100, 50):
        store.record_trace({"run_id": "same", "usage": {"total_tokens": tokens},
                            "events": [{"kind": "provider", "usage": {"total_tokens": tokens}}]})
    assert store.db.execute("SELECT total_tokens FROM usage").fetchone()[0] == 100
    store.close()


def test_changed_configuration_cannot_reuse_committed_state(tmp_path):
    first = CondensedPipeline(SOURCE, tmp_path)
    first.close()
    with pytest.raises(ValueError):
        CondensedPipeline(SOURCE, tmp_path, ProductionConfig(plan_batch=1))


def test_canon_refresh_never_resets_spending_or_committed_chapters(tmp_path):
    pipeline = CondensedPipeline(SOURCE, tmp_path)
    budget = DurableBudget(pipeline.store, "plan_1_10", pipeline.settings)
    budget.request()
    identity = pipeline.store.metadata("identity")
    identity["canon_hash"] = "old"
    pipeline.store.metadata("identity", identity)
    pipeline.close()
    pipeline = CondensedPipeline(SOURCE, tmp_path, refresh_uncommitted_canon=True)
    assert pipeline.store.operation("plan_1_10")[0] == 1
    committed(pipeline.store)
    identity = pipeline.store.metadata("identity")
    identity["canon_hash"] = "another"
    pipeline.store.metadata("identity", identity)
    pipeline.close()
    with pytest.raises(ValueError):
        CondensedPipeline(SOURCE, tmp_path, refresh_uncommitted_canon=True)


def test_readable_brief_cannot_drop_event_card_information():
    value = draft()
    value["scenes"][0]["text"] = "遗漏全部关键内容"
    render_brief(value)
    text = value["scenes"][0]["text"]
    assert "遗漏全部关键内容" not in text
    for beat in value["scenes"][0]["beats"]:
        assert beat["action"] in text and beat["cause"] in text and beat["outcome"] in text


def test_repairs_edit_the_source_events_not_a_derived_text_copy():
    original = render_brief(draft())
    view = generation_view(original)
    assert all("text" not in scene for scene in view["scenes"])
    assert original["scenes"][0]["text"]
    assert view["scenes"][0]["beats"] == original["scenes"][0]["beats"]


def test_recently_committed_supporting_cast_does_not_need_reintroduction():
    value = draft()
    value["scenes"][0]["cast"].append("村老赵")
    assert validate_chapter(value, plan(), {}, 0)
    assert not validate_chapter(value, plan(), {}, 0, known_cast={"村老赵"})


def test_long_absent_character_history_is_retrieved_without_loading_the_whole_book(tmp_path):
    store = Store(tmp_path)
    first = draft()
    first["scenes"][0]["cast"].append("赵掌事")
    first["scenes"][0]["beats"][0]["action"] = "赵掌事交给陆烬一册旧账，约定日后核对"
    first.update(plan=plan(), context=store.context())
    store.commit(first, review(first))
    for number in range(2, 9):
        committed(store, number)
    context = store.context(["陆烬", "赵掌事"])
    assert any(item["name"] == "赵掌事" and item["first_chapter"] == 1 for item in context["character_registry"])
    assert any(item["name"] == "赵掌事" and "旧账" in item["introduction"] for item in context["introductions"])
    assert any(item["chapter"] == 1 for item in context["related_history"])
    assert len(context["recent_chapters"]) == 4
    store.close()


def test_full_month_cannot_be_recorded_as_chapter_three_days():
    value = draft(3)
    value["scenes"][0]["start"] = "陆烬满月，陈老根照看婴儿"
    normalize_timeline(value, 2, "夜晚")
    assert value["story_day"] >= 30
    assert not validate_timeline(value, 2)


def test_three_years_and_multi_day_journey_advance_the_ledger():
    value = draft(5)
    task = plan(5)
    task["events"][0]["action"] = "陆烬满三岁，跟随陈老根"
    value["scenes"][0]["time"] = "清晨"
    value["scenes"][1]["time"] = "次日正午，经过约三天寻找"
    normalize_timeline(value, 90, "深夜", plan=task)
    assert value["scenes"][0]["start_day"] >= 3 * 365
    assert value["story_day"] >= value["scenes"][0]["start_day"] + 4
    assert not validate_timeline(value, 90)


def test_timeline_normalization_is_idempotent_and_flashbacks_do_not_age_the_present():
    value = draft()
    value["scenes"][0].update(time_scope="flashback", start="陆烬满三岁时的回忆", time="数年前清晨")
    value["scenes"][1].update(daypart="午后", gap_days=0, duration_days=0)
    normalize_timeline(value, 10000, "清晨")
    first = json.dumps(value, sort_keys=True)
    normalize_timeline(value, 10000, "清晨")
    assert json.dumps(value, sort_keys=True) == first
    assert value["story_day"] == 10000


def test_scene_time_cannot_move_backwards_even_when_chapter_end_is_large():
    value = draft()
    normalize_timeline(value, 100, "清晨")
    value["scenes"][1]["start_day"] = 99
    value["story_day"] = 500
    assert validate_timeline(value, 100)


def test_malformed_scene_placeholder_is_a_repairable_contract_error():
    value = draft()
    value["scenes"][1] = "id：S2"
    normalize_timeline(value, 0)
    assert validate_timeline(value, 0)
    assert validate_chapter(value, plan(), {}, 0)


def test_chinese_calendar_numbers():
    assert chinese_number("一百二十三") == 123
    assert chinese_number("两千零三") == 2003


def test_event_id_cannot_substitute_for_required_participants():
    task = plan()
    task["events"][0]["action"] = "村民围观并恐慌"
    assert any("对应参与者" in error for error in validate_chapter(draft(), task, {}, 0))


def test_eight_true_flags_cannot_replace_event_evidence():
    with pytest.raises(ValueError, match="every required event"):
        validate_review(review(), "input", render_brief(draft()), plan())


def test_quote_binding_never_changes_a_review_decision():
    value = render_brief(draft())
    checked = review()
    beats = [beat for scene in value["scenes"] for beat in scene["beats"]]
    checked["event_evidence"] = []
    checked["milestone_evidence"] = []
    for event in plan()["events"]:
        beat = next(beat for beat in beats if event["id"] in beat["event_ids"])
        checked["event_evidence"].append({"event_id": event["id"], "covered": True, "beat_ids": [beat["id"]],
                                           "evidence": "model paraphrase", "reason": "对照动作和参与者"})
    bind_review_evidence(checked, value, plan(), [])
    assert validate_review(checked, "input", value, plan())
    assert all(item["model_evidence"] == "model paraphrase" for item in checked["event_evidence"])
    checked["event_evidence"][0]["covered"] = False
    checked["checks"]["coverage"] = False
    checked["issues"] = [{"check": "coverage", "location": "E1", "evidence": "动作缺失", "repair": "补动作"}]
    bind_review_evidence(checked, value, plan(), [])
    assert not validate_review(checked, "input", value, plan())


def test_invalidation_archives_original_and_keeps_spending(tmp_path):
    store = Store(tmp_path)
    committed(store)
    committed(store, 2)
    canon = load_canon(SOURCE)
    for number in (1, 2):
        export_chapter(store, number, canon, ProductionConfig())
    budget = DurableBudget(store, "chapter_2", ProductionConfig())
    budget.request()
    archive = store.invalidate_from(2, "required event was missing")
    assert (archive / "manuscript_before.sqlite3").is_file()
    assert (archive / "expansion/chapter_0002/manifest.json").is_file()
    assert store.prefix() == 1
    assert store.state()[("陆烬", "掌握线索")] == "第1页缺失"
    assert store.operation("chapter_2")[0] == 1
    store.extend_budget("chapter_2", 2, 60, "repair the verified defect")
    resumed = DurableBudget(store, "chapter_2", ProductionConfig())
    assert resumed.calls == 1 and resumed.limits["max_llm_calls"] == 18
    store.close()
