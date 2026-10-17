def test_length_is_soft_never_hard():
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    gate = PipelineOrchestrator._deterministic_quality_gate
    class Fake:
        def _bpt(self, bp):
            v = bp.get("word_count_target", 0)
            if isinstance(v, list):
                v = v[0] if v else 0
            try:
                return int(v or 0)
            except Exception:
                return 0
    task_card = {"chapter_num": 99, "scene_blueprints": [{"word_count_target": 1000}]}
    text = "字" * 2000  # 2000 > 1000*1.35+容差 → length anomaly fires
    out = gate(Fake(), text, task_card)
    assert out["passed"] is True
    assert any("长度" in s for s in out["soft_issues"])
    assert not any("长度" in s for s in out["issues"])


def test_overlong_90plus_lands_in_novel(tmp_path, monkeypatch):
    """L2 constructed proof: overlong body + high score lands in novel/, not draft/.

    Stubbing mirrors test_novel_engine.py::test_pipeline_incremental_patcher
    (mock LLM + tmp project root, hermetic, no network). _enforce is bypassed
    via monkeypatched no-op to simulate non-convergence: the overlong body
    reaches the publish gate unconverged by design.

    NOTE (deviation from brief Step 1, control-verified): a constant 90/pass
    review does NOT discriminate pre/post L1 — the pass path skips the det
    gate and quality_gate.evaluate_publish already filters "[长度]" notes, so
    it lands in novel/ either way. The length-hard veto actually bites in the
    fix loop (det["passed"] False blocks the break) plus the force-to-draft
    fallback. This proof therefore drives fix(80) -> pass(90): post-L1 the
    90 round breaks the loop on soft-only length and publishes to novel/;
    pre-L1 (length hard) the break is refused and the best is force-routed
    to draft/. Repair stubs return no-ops (non-convergent repair).
    """
    from pathlib import Path

    from novel_engine.core.llm_client import LLMClient
    from novel_engine.core.state_machine import ChapterPhase
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    root_path = Path(tmp_path)
    for d in ["config", "config/simulation", "memory/world_state", "config/foreshadow",
              "config/planning", "planning", "bible", "runtime"]:
        (root_path / d).mkdir(parents=True, exist_ok=True)
    (root_path / "config" / "runtime_config.json").write_text('{"llm": {"use_mock": true}}', encoding="utf-8")
    import json
    (root_path / "config" / "llm_providers.json").write_text(json.dumps({
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com/v1",
                "api_key_env": "TEST_API_KEY_L2",
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
    }, ensure_ascii=False), encoding="utf-8")
    import os
    os.environ["TEST_API_KEY_L2"] = "sk-test"
    (root_path / "config" / "simulation" / "rules.json").write_text('{}', encoding="utf-8")
    (root_path / "config" / "simulation" / "constraints.json").write_text('{}', encoding="utf-8")
    (root_path / "memory/world_state/characters.json").write_text('{"characters": {}}', encoding="utf-8")
    (root_path / "memory/world_state/factions.json").write_text('{"factions": {}}', encoding="utf-8")
    (root_path / "memory/world_state/power_system.json").write_text('{"current_power_balance": {}}', encoding="utf-8")
    (root_path / "config" / "foreshadow" / "registry.json").write_text('{"foreshadows": []}', encoding="utf-8")
    (root_path / "config" / "planning" / "volumes.json").write_text('{"volumes": [{"id": "V01", "chapter_range": [1, 100]}]}', encoding="utf-8")
    (root_path / "config" / "planning" / "plot_graph.json").write_text('{"nodes": []}', encoding="utf-8")
    (root_path / "bible" / "world_bible.md").write_text('', encoding="utf-8")
    (root_path / "bible" / "character_bible.md").write_text('', encoding="utf-8")
    (root_path / "bible" / "style_bible.md").write_text('', encoding="utf-8")
    (root_path / "bible" / "author_intent.md").write_text('', encoding="utf-8")
    (root_path / "planning" / "outline.md").write_text('', encoding="utf-8")

    # Fixed 9000-char overlong body vs 4000 target (high=4000*1.35+容差=5480).
    # Gates-clean by construction: unique numbered paras (no repetition),
    # ends with 。(no truncation), zero scaffolding tokens / cliches / 十年.
    seg = ("第{0:04d}段雾隐村晨雾漫过青石道韩玄抚摸旧玉佩沿山道前行"
           "藏经阁灯火未熄翻阅古籍至夜半山风穿林而过衣衫微动心绪渐定决意启程远行历练求道不负旧友所托。")
    segs = [seg.format(n) for n in range(1, 200)]
    body = "\n\n".join(segs)
    overlong = body[:8999] + "。"
    assert len(overlong) == 9000
    assert overlong.endswith("。")

    task_card = {"chapter_num": 1, "title": "雾隐村晨雾", "core_goal": "韩玄启程",
                 "scene_blueprints": [{"scene_num": 1, "word_count_target": 4000,
                                       "goal": "启程", "location": "雾隐村"}]}
    synopsis = {"chapter_num": 1,
                "synopsis": "韩玄沿雾隐村青石道前行抚摸旧玉佩翻阅古籍至夜半山风穿林心绪渐定决意启程。",
                "state_changes": [], "foreshadow_execution": []}

    def _review(score):
        verdict = "pass" if score >= 88 else "fix"
        return {"chapter_num": 1,
                "scores": {"plot_consistency": 25, "character_consistency": 20,
                           "foreshadow_execution": 20, "style_match": 15,
                           "pacing": 10, "innovation": 10},
                "total_score": score, "verdict": verdict,
                "issues": [], "praise": "首尾兼顾", "fix_scope": ""}

    calls = {"n": 0}

    def _review_seq(**kw):
        calls["n"] += 1
        return _review(90 if calls["n"] > 1 else 80)

    llm_client = LLMClient(use_mock=True)
    orch = PipelineOrchestrator(project_root=str(root_path), llm_client=llm_client)

    def _fake_directing(chapter_num, world_state):
        orch.state_machine.transition(ChapterPhase.DIRECTING)
        return task_card

    def _fake_synopsis(card):
        orch.state_machine.transition(ChapterPhase.SYNOPSIS)
        return synopsis

    try:
        monkeypatch.setattr(orch, "_stage_directing", _fake_directing)
        monkeypatch.setattr(orch, "_stage_synopsis", _fake_synopsis)
        monkeypatch.setattr(orch.writer, "generate_full_chapter", lambda *a, **k: overlong)
        monkeypatch.setattr(orch.writer, "polish_chapter",
                            lambda text, card, llm_client=None: text)
        monkeypatch.setattr(orch, "_enforce_word_count",
                            lambda novel, target_min, target_max: novel)
        monkeypatch.setattr(orch.reviewer, "review_chapter", _review_seq)
        # Non-convergent repair: nothing better available; the publish gate decides.
        monkeypatch.setattr(orch, "_patch_weak_scenes", lambda *a, **k: None)
        monkeypatch.setattr(orch, "_rewrite_weak_dimensions", lambda *a, **k: "")
        result = orch.generate_single_chapter(1)

        assert result["success"] is True
        assert result["score"] == 90
        novel_path = root_path / "chapters" / "novel" / "chapter_1.txt"
        draft_path = root_path / "chapters" / "draft" / "chapter_1.txt"
        assert novel_path.exists(), "overlong 90+ chapter must land in novel/"
        assert not draft_path.exists(), "must NOT land in draft/"
        # The overlong body itself landed (not truncated down to target).
        assert len(novel_path.read_text(encoding="utf-8")) > int(4000 * 1.35)
    finally:
        try:
            orch.close()
        except Exception:
            pass
