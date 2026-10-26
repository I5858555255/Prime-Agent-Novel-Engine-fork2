"""Offline regression coverage for pipeline call reduction and bounded execution."""
import json
import random
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest

from novel_engine.agents.scene_schema import SceneOutput
from novel_engine.agents.writer_agent import SynopsisAgent, WriterAgent
from novel_engine.core.errors import TransientLLMError
from novel_engine.core.execution import (
    AttemptBudget, BudgetExceeded, ContextExecutor, chapter_run, current_execution, stage,
)
from novel_engine.core.latency_report import summarize
from novel_engine.core.llm_client import LLMClient
from novel_engine.core.model_router import ModelRouter
from novel_engine.core.state_machine import ChapterPhase
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
from novel_engine.quality.polish_policy import select_polish_scenes
from novel_engine.quality.review_policy import review_on_demand


class Run:
    def __init__(self, root, client=None, **limits):
        self.root = root
        self.config = {"pipeline": {"attempt_budget": limits}}
        self._execution_lock = threading.RLock()
        self.llm = client

    @chapter_run
    def execute(self, chapter_num, callback):
        callback()
        return {"chapter": chapter_num, "success": True}


def client_with_transport(handler):
    client = LLMClient(api_base="https://example.invalid")
    client._local.client = httpx.Client(base_url="https://example.invalid", transport=httpx.MockTransport(handler))
    return client


def response(content="正文内容", **extra):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                   "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}, **extra})


def test_budget_atomic_across_workers():
    budget = AttemptBudget(1, max_llm_calls=5)
    def consume(_):
        try:
            budget.request()
            return True
        except BudgetExceeded:
            return False
    with ContextExecutor(max_workers=8) as pool:
        admitted = list(pool.map(consume, range(40)))
    assert sum(admitted) == budget.calls == 5


@pytest.mark.parametrize("limits", [{"max_llm_calls": 0}, {"max_wall_seconds": float('nan')}])
def test_invalid_limits(limits):
    with pytest.raises(ValueError):
        AttemptBudget(1, **limits)


def test_budget_deadline(monkeypatch):
    clock = [10.0]
    monkeypatch.setattr("novel_engine.core.execution.time.monotonic", lambda: clock[0])
    budget = AttemptBudget(1, max_wall_seconds=2)
    clock[0] = 12.0
    with pytest.raises(BudgetExceeded, match="wall-time"):
        budget.request()
    assert budget.calls == 0


def test_degenerate_retries_share_physical_budget_and_trace(tmp_path):
    transport_calls = []
    client = client_with_transport(lambda request: (transport_calls.append(request), response(""))[1])
    run = Run(tmp_path, client, max_llm_calls=2)
    result = run.execute(1, lambda: client.chat_completion([{"role": "user", "content": "test"}]))
    assert result["budget_exhausted"] and not result["success"]
    assert len(transport_calls) == 2
    report = json.loads(open(result["trace_path"]).read())
    assert report["calls"] == 2 and report["retry_calls"] == 1
    assert report["usage"]["prompt_tokens"] == 20
    assert "test" not in json.dumps(report)


def test_failed_requests_count_and_terminal_budget_survives_swallow(tmp_path):
    calls = []
    def fail(request):
        calls.append(request)
        return httpx.Response(503)
    client = client_with_transport(fail)
    run = Run(tmp_path, client, max_llm_calls=1)
    committed = []
    @stage("commit_io")
    def commit(owner):
        committed.append(True)
    def work():
        try:
            client.chat_completion([], max_retries=2)
        except Exception:
            pass
        commit(run)
    result = run.execute(3, work)
    assert result["budget_exhausted"]
    assert not committed and len(calls) == 1
    assert run.last_chapter_trace["failed_calls"] == 1


def test_nested_chapter_retry_does_not_reset_budget(tmp_path):
    run = Run(tmp_path, max_llm_calls=1)
    ids = []
    @chapter_run
    def nested(owner, chapter):
        ids.append(current_execution().run_id)
        current_execution().budget.request()
        return {"success": True}
    def work():
        nested(run, 1)
        nested(run, 1)
    result = run.execute(1, work)
    assert result["budget_exhausted"] and len(set(ids)) == 1
    assert run.last_chapter_trace["budget"]["calls"] == 1


def test_context_is_propagated_and_restored(tmp_path):
    run = Run(tmp_path)
    seen = []
    def work():
        with ContextExecutor(max_workers=2) as pool:
            seen.extend(pool.submit(current_execution).result() for _ in range(2))
    run.execute(1, work)
    assert len(seen) == 2 and seen[0] is seen[1]
    assert current_execution() is None and run._chapter_execution is None


def test_router_owns_transport_retries(tmp_path, monkeypatch):
    calls = []
    client = client_with_transport(lambda request: (calls.append(request), httpx.Response(503))[1])
    router = ModelRouter("scenes", tmp_path, llm_client=client)
    monkeypatch.setattr("novel_engine.core.model_router.budget_sleep", lambda *args: None)
    run = Run(tmp_path, router, max_llm_calls=2)
    with pytest.raises(TransientLLMError):
        run.execute(1, lambda: router.chat_completion([]))
    # Two router attempts, each a single HTTP request, not 2 x 3 client retries.
    assert len(calls) == 2
    assert run.last_chapter_trace["failed_calls"] == 2
    assert run.last_chapter_trace["retry_calls"] == 1


def test_synopsis_merged_default_preserves_state_changes():
    orchestrator = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orchestrator.config = {}
    orchestrator.state_machine = SimpleNamespace(transition=Mock())
    orchestrator.memory = SimpleNamespace(add_pending_change=Mock())
    orchestrator.synopsis_agent = SynopsisAgent(llm_client=Mock())
    card = {"chapter_num": 9, "core_goal": "找到遗失的信件", "scene_blueprints": [{"scene_num": 1, "goal": "开柜"}],
            "state_changes": [{"type": "character_location", "target": "C1", "new_value": "库房"}]}
    output = orchestrator._stage_synopsis(card)
    orchestrator.synopsis_agent.llm.chat_completion.assert_not_called()
    assert "开柜" in output["synopsis"]
    assert output["state_changes"] == card["state_changes"]
    orchestrator.memory.add_pending_change.assert_called_once_with(card["state_changes"][0], chapter=9)
    orchestrator.state_machine.transition.assert_called_once_with(ChapterPhase.SYNOPSIS)


def test_synopsis_opt_out():
    orchestrator = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orchestrator.config = {"pipeline": {"merge_synopsis_into_directing": False}}
    orchestrator.state_machine = SimpleNamespace(transition=Mock())
    orchestrator.memory = SimpleNamespace(add_pending_change=Mock())
    orchestrator.synopsis_agent = SimpleNamespace(generate_synopsis=Mock(return_value={"synopsis": "独立缩写"}))
    assert orchestrator._stage_synopsis({})["synopsis"] == "独立缩写"
    orchestrator.synopsis_agent.generate_synopsis.assert_called_once()


def clean_text(n=45, seed=0):
    rng = random.Random(seed)
    return "。".join("".join(chr(rng.randrange(0x4e00, 0x9fff)) for _ in range(20)) for _ in range(n)) + "。"


def test_selector_checks_quality_even_when_length_on_target(tmp_path):
    clean = SceneOutput(1, clean_text(), "")
    dirty = SceneOutput(2, "英文pollution。" + clean_text(seed=1), "", finish_reason="length")
    card = {"chapter_num": 100, "scene_blueprints": [{"scene_num": 1}, {"scene_num": 2}]}
    selected = select_polish_scenes([clean, dirty], card, tmp_path, targets={1: 1000, 2: 1000})
    assert 1 not in selected
    assert "latin_leak" in selected[2] and "finish_reason_length" in selected[2]


def test_selector_clean_scenes_make_zero_polish_calls(tmp_path):
    scene = SceneOutput(1, clean_text(), "")
    card = {"chapter_num": 100, "scene_blueprints": [{"scene_num": 1}]}
    selection = select_polish_scenes([scene], card, tmp_path, targets={1: 1000})
    client = Mock(concurrency=1)
    writer = WriterAgent(client, tmp_path)
    output, flags = writer.polish_scenes([scene], card, only_ids=set(selection))
    assert output == [scene] and flags == [False]
    client.chat_completion.assert_not_called()


POLICY = {"publication_line": 88, "soft_publication_line": 85}


def test_clear_review_is_one_call():
    once = Mock(return_value={"normalized_score": 95, "issues": []})
    result = review_on_demand(None, once, lambda review: "pass", POLICY, {})
    assert result["score"] == 95 and once.call_count == 1


def test_borderline_disagreement_is_not_published():
    once = Mock(side_effect=[{"normalized_score": score, "issues": []} for score in (87, 60, 95)])
    result = review_on_demand(None, once, lambda review: "fix", POLICY, {})
    assert once.call_count == 3 and result["review_unstable"]
    cached = review_on_demand(result["review"], once, lambda review: "fix", POLICY, {})
    assert cached["review_unstable"] and once.call_count == 3


def test_deterministic_fail_does_not_call_reviewer(tmp_path):
    orchestrator = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orchestrator.root = tmp_path
    orchestrator.config = {}
    orchestrator.state_machine = SimpleNamespace(current_phase=ChapterPhase.REVIEW)
    orchestrator.writer = SimpleNamespace(last_scenes=[])
    orchestrator._deterministic_quality_gate = Mock(return_value={"passed": False, "issues": ["[截断] bad"]})
    orchestrator._forbidden_violations = Mock(return_value=[])
    orchestrator.reviewer = Mock()
    result = orchestrator._stage_review(1, {}, {}, "断句", {})
    assert result["verdict"] == "fix" and result["review"]["deterministic_only"]
    orchestrator.reviewer.review_chapter.assert_not_called()


def test_review_round_and_scene_limits():
    budget = AttemptBudget(1, max_review_rounds=1, max_scene_attempts=1)
    budget.review()
    with pytest.raises(BudgetExceeded, match="review_rounds"):
        budget.review()
    budget = AttemptBudget(1, max_scene_attempts=1)
    budget.scene(4)
    with pytest.raises(BudgetExceeded, match="scene 4"):
        budget.scene(4)


def test_report_percentiles_keep_failures_separate(tmp_path):
    run = Run(tmp_path)
    run.execute(1, lambda: None)
    report = summarize(list((tmp_path / "audit" / "latency").glob("*.json")))
    assert report["chapters"]["passed"]["samples"] == 1
    assert report["chapters"]["failed"]["wall_s"]["p95"] is None


def test_report_does_not_count_successful_draft_as_publication(tmp_path):
    paths = []
    for index, (success, published) in enumerate(((True, False), (True, True), (False, False))):
        path = tmp_path / f"chapter_{index}.json"
        path.write_text(json.dumps({"success": success, "published": published, "wall_s": index + 1,
                                    "events": [], "calls": 1, "failed_calls": 0, "retry_calls": 0}))
        paths.append(path)
    groups = summarize(paths)["chapters"]
    assert groups["passed"]["samples"] == 2
    assert groups["published"]["samples"] == 1
    assert groups["published"]["wall_s"]["p95"] == 2
    assert groups["unpublished"]["samples"] == 2


def test_successful_polish_preserves_scene_contract(tmp_path):
    prose = clean_text()
    class Echo:
        concurrency = 1
        def chat_completion(self, messages, **kwargs):
            return {"content": prose, "finish_reason": "stop"}
    writer = WriterAgent(Echo(), tmp_path)
    original = SceneOutput(1, prose, "钩子", ["动作"], beats_covered=["动作"], structured=True)
    scenes, flags = writer.polish_scenes([original], {}, only_ids={1})
    assert flags == [True]
    assert scenes[0].beats_covered == ["动作"] and scenes[0].structured
    assert original.scene_text == prose


def test_malformed_review_fails_closed():
    once = Mock(return_value={"normalized_score": 96, "raw_total": 0})
    with pytest.raises(ValueError, match="invalid raw vote"):
        review_on_demand(None, once, lambda review: "pass", POLICY, {})
    assert once.call_count == 1


def test_orchestrator_same_text_review_uses_cache(tmp_path):
    from novel_engine.pipeline.phase_cache import PhaseCache
    orchestrator = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orchestrator.root = tmp_path
    orchestrator.config = {"llm": {"use_mock": True}}
    orchestrator._cached_policy = POLICY
    orchestrator.state_machine = SimpleNamespace(current_phase=ChapterPhase.REVIEW)
    orchestrator._deterministic_quality_gate = Mock(return_value={"passed": True, "issues": []})
    orchestrator._forbidden_violations = Mock(return_value=[])
    orchestrator._review_read_chapters = set()
    orchestrator._phase_cache = PhaseCache(tmp_path)
    orchestrator.reviewer = SimpleNamespace(
        review_chapter=Mock(return_value={"normalized_score": 95}), grade_review=lambda review: "pass")
    first = orchestrator._stage_review(10, {}, {}, "清晰完整的正文。", {})
    second = orchestrator._stage_review(10, {}, {}, "清晰完整的正文。", {})
    assert first["score"] == second["score"] == 95
    assert orchestrator.reviewer.review_chapter.call_count == 1
    orchestrator._stage_review(10, {}, {}, "清晰完整的正文。新增动作。", {})
    assert orchestrator.reviewer.review_chapter.call_count == 2


def test_commit_started_before_deadline_can_finish_atomically(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("novel_engine.core.execution.time.monotonic", lambda: clock[0])
    run = Run(tmp_path, max_wall_seconds=2)
    @stage("commit_io")
    def commit(owner):
        clock[0] = 3.0
    result = run.execute(1, lambda: commit(run))
    assert result["success"] and not result.get("budget_exhausted")


def test_provider_does_not_retry_terminal_budget(monkeypatch):
    from novel_engine.core.llm_provider import LLMProvider
    client = SimpleNamespace(chat_completion=Mock(side_effect=BudgetExceeded("exhausted")))
    sleep = Mock()
    monkeypatch.setattr("novel_engine.core.llm_provider.budget_sleep", sleep)
    with pytest.raises(BudgetExceeded):
        LLMProvider(client).complete([{"role": "user", "content": "正文"}])
    assert client.chat_completion.call_count == 1
    sleep.assert_not_called()


def test_failover_does_not_switch_on_terminal_budget(monkeypatch):
    from novel_engine.core.llm_failover import FailoverLLMClient
    primary = SimpleNamespace(chat_completion=Mock(side_effect=BudgetExceeded("exhausted")))
    fallback = SimpleNamespace(chat_completion=Mock(), model="fallback")
    sleep = Mock()
    monkeypatch.setattr("novel_engine.core.llm_failover.budget_sleep", sleep)
    client = FailoverLLMClient(primary, fallback, trigger=1)
    with pytest.raises(BudgetExceeded):
        client.chat_completion([])
    fallback.chat_completion.assert_not_called()
    sleep.assert_not_called()


def test_director_craft_requests_density_contract_and_preserves_it(tmp_path):
    from novel_engine.agents.chapter_director import ChapterDirector
    from novel_engine.quality.opening_density import assess_opening_density
    director = ChapterDirector.__new__(ChapterDirector)
    director._phase_start_ts = 0
    skeleton = [{"scene_num": i, "location": "雾隐村村口"} for i in range(1, 5)]
    calls = []
    def answer(prompt, max_tokens, system_prompt):
        for key in ("external_event", "irreversible_state_delta", "world_anomaly_signal"):
            assert key in prompt and key in system_prompt
        assert "第1章前两个场景" in prompt
        calls.append(prompt)
        return {"scene_blueprints": [dict(bp, external_event="村民提灯赶往村口查看", irreversible_state_delta="众人发现迷雾中的婴孩", world_anomaly_signal="村口石块浮起并发出微光" if i == 0 else "") for i, bp in enumerate(skeleton)]}, "stop", {}
    director._call_with_meta = answer
    scenes = director._call_scene_craft(1, {}, "", skeleton)
    assert len(calls) == 1
    assert not assess_opening_density(scenes, 1, False)["hard_violations"]


def test_director_feedback_survives_reduced_prompt(tmp_path):
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    director.root = tmp_path
    director._phase_start_ts = 0
    feedback = "前两场必须明确规划村民可见的异象"
    calls = []
    def answer(prompt, max_tokens, system_prompt):
        assert feedback in prompt
        calls.append(prompt)
        if len(calls) == 1:
            return {"scene_blueprints": []}, "length", {}
        return {"scene_blueprints": [{"scene_num": i} for i in range(1, 5)]}, "stop", {}
    director._call_with_meta = answer
    context = {"relevant_plot_nodes": [], "constraints": "", "bible": {"world": "", "character": "", "style": ""}}
    assert len(director._call_scene_skeleton(1, context, "", [feedback])) == 4
    assert len(calls) == 2


@pytest.mark.parametrize("resumed", [False, True])
@pytest.mark.parametrize("candidate_kind", ["clean", "degenerate", "latin"])
def test_expansion_uses_refine_concurrency(tmp_path, monkeypatch, resumed, candidate_kind):
    import time
    from novel_engine.pipeline import pipeline_orchestrator as module
    orchestrator = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orchestrator.root = tmp_path
    orchestrator.config = {"llm": {"use_mock": False}}
    orchestrator.state_machine = SimpleNamespace(transition=Mock())
    orchestrator.outline_router = SimpleNamespace(concurrency=1)
    orchestrator.refine_router = SimpleNamespace(concurrency=2)
    orchestrator.polish_router = SimpleNamespace(concurrency=1)
    scenes = [SceneOutput(i, clean_text(n=95, seed=i), "") for i in range(1, 5)]
    writer = SimpleNamespace(last_scenes=scenes, generate_full_chapter=Mock(return_value=""),
                             polish_scenes=lambda scenes, *args, **kwargs: (scenes, [False] * len(scenes)))
    orchestrator.writer = writer
    if resumed:
        monkeypatch.setattr(module, "completed_scene_ids", lambda root, chapter: {1, 2, 3, 4})
        orchestrator._load_journal_scenes = lambda chapter: scenes
    orchestrator._journal_validated_scenes = Mock()
    orchestrator._validate_and_regen_scenes = Mock()
    orchestrator._apply_sequence_order = Mock()
    orchestrator._write_polish_flags = Mock()
    orchestrator._forbidden_violations = Mock(return_value=[])
    orchestrator._deterministic_quality_gate = Mock(return_value={"passed": True, "issues": []})
    for name in ("_run_boundary_gate", "_run_continuity_gate", "_run_scope_gate", "_run_latin_leak_gate",
                 "_run_pov_interiority_gate", "_run_constraint_compliance_gate", "_run_density_gate",
                 "_run_scene_progression_gate", "_apply_length_floor", "_run_cross_scene_repeat_gate",
                 "_run_boundary_reprise_gate", "_run_punctuation_health_gate"):
        setattr(orchestrator, name, lambda chapter, card, scenes, text: text)
    def late_topup(chapter, card, current_scenes, text):
        current_scenes[0].scene_text += "深夜有人敲响了门。"
        return "\n\n".join(scene.scene_text for scene in current_scenes)
    orchestrator._apply_length_floor = late_topup
    active = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(2)
    def expand(*args, **kwargs):
        nonlocal active, peak
        assert "本章时间线锚点" in args[0] and "全程夜间" in args[0]
        assert "不得离开村口" in args[0]
        with lock:
            active += 1
            peak = max(peak, active)
        try:
            barrier.wait(timeout=2)
            time.sleep(0.01)
            if candidate_kind == "degenerate":
                return "雾气不断翻涌。" * 600
            if candidate_kind == "latin":
                return clean_text(n=125) + " foreign。"
            return clean_text(n=125)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(module, "call_llm", expand)
    card = {"chapter_num": 100, "timeline_anchor": {"time": "全程夜间"},
            "scene_blueprints": [{"scene_num": i, "constraints": "不得离开村口"} for i in range(1, 5)]}
    orchestrator._stage_write(card, {"synopsis": ""})
    assert peak == 2
    if candidate_kind == "clean":
        assert all(len(s.scene_text) > 2500 for s in writer.last_scenes)
    else:
        assert all(len(s.scene_text) < 2500 for s in writer.last_scenes)
        assert all("foreign" not in s.scene_text and "雾气不断翻涌。" * 3 not in s.scene_text for s in writer.last_scenes)
    from novel_engine.pipeline.chapter_journal import load_authoritative_scenes
    saved = load_authoritative_scenes(tmp_path, 100)
    assert saved[0]["scene_text"].endswith("深夜有人敲响了门。")


@pytest.mark.parametrize("metadata", [{}, {"chapter_num": 0}, {"chapter_num": 999}])
def test_director_chapter_identity_is_owned_by_caller(metadata):
    from novel_engine.agents.chapter_director import ChapterDirector
    director = ChapterDirector.__new__(ChapterDirector)
    card = director._merge_task_cards([{"scene_num": 1}], metadata, 7)
    assert card["chapter_num"] == 7
    assert metadata.get("chapter_num") != 7
