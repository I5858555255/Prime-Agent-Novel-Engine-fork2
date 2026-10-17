import json
import os
from pathlib import Path


def test_scan_flags_english():
    from novel_engine.quality.forbidden_scanner import ForbiddenScanner
    s = ForbiddenScanner(rules_path=Path(__file__).parent.parent / "config" / "forbidden.json")
    hits = s.scan("他 crossed 双臂，神色 steady。")
    names = {h["name"] for h in hits}
    assert "english_word" in names


def test_scan_clean_passes():
    from novel_engine.quality.forbidden_scanner import ForbiddenScanner
    s = ForbiddenScanner(rules_path=Path(__file__).parent.parent / "config" / "forbidden.json")
    assert s.scan("他缓缓闭上双眼，气息归于沉静。") == []


def test_defects_add_and_consume(tmp_path):
    from novel_engine.quality.defects_store import DefectsStore
    ds = DefectsStore(str(tmp_path / "defects.json"))
    ds.add(12, "below_min_ch", "score=70")
    assert len(ds.pending()) == 1
    assert ds.consume(12) is True
    assert ds.pending() == []
    ds.add(13, "drift", "realm regression")
    ds.mark_known(13)
    assert ds.all()[-1]["known"] is True


def test_failover_switches_after_trigger():
    from novel_engine.core.llm_client import LLMClient
    from novel_engine.core.llm_failover import FailoverLLMClient

    primary = LLMClient(use_mock=True)
    fallback = LLMClient(use_mock=True)
    fo = FailoverLLMClient(primary, fallback, trigger=2, max_backoff=1, log_prefix="t")
    class Broken:
        def chat_completion(self, *a, **k):
            raise RuntimeError("boom")
    fo.primary = Broken()
    msgs = [{"role": "user", "content": "hi"}]
    for _ in range(3):
        try:
            fo.chat_completion(msgs)
        except RuntimeError:
            pass
    assert fo._using_fallback is True


def test_orchestrator_builds_failover_clients(tmp_path):
    import json
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfg = {
        "llm": {"model": "Qwen/Qwen3.5-4B", "api_base": "https://api.siliconflow.cn", "use_mock": True},
        "review_llm": {"model": "agnes-2.5-flash", "api_base": "https://apihub.agnes-ai.com", "use_mock": True},
        "fallback_llm": {"model": "deepseek-ai/DeepSeek-V3.2", "api_base": "https://api.siliconflow.cn", "use_mock": True},
        "autonomy": {"failover_trigger_consecutive_errors": 3, "max_backoff_seconds": 1},
    }
    (tmp_path / "config" / "runtime_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    # Write minimal llm_providers.json so ModelRouter can init
    providers_cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_API_KEY_FOR_AUTO",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["test-model"], "response_format": "json_object"},
                    "polish": {"models": ["test-model"], "response_format": None},
                    "outline": {"models": ["test-model"], "response_format": None},
                    "review": {"models": ["test-model"], "response_format": "json_object"},
                    "director": {"models": ["test-model"], "response_format": "json_object"},
                    "synopsis": {"models": ["test-model"], "response_format": "json_object"},
                }
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").write_text(json.dumps(providers_cfg), encoding="utf-8")
    os.environ["TEST_API_KEY_FOR_AUTO"] = "sk-test"
    try:
        from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
        orch = PipelineOrchestrator(project_root=str(tmp_path), llm_client=None)
        assert orch.writer is not None
    finally:
        os.environ.pop("TEST_API_KEY_FOR_AUTO", None)


def test_recovery_fails_and_records_gap(tmp_path, monkeypatch):
    import json, os, time
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfg = {
        "llm": {"use_mock": True}, "review_llm": {"use_mock": True},
        "fallback_llm": {"use_mock": True},
        "quality": {"min_chapter_score": 82, "fix_threshold": 60, "publication_line": 88},
        "autonomy": {"chapter_regen_max_retries": 1},
    }
    (tmp_path / "config" / "runtime_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    providers_cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_API_KEY_AUTO2",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["test-model"], "response_format": "json_object"},
                    "polish": {"models": ["test-model"], "response_format": None},
                    "outline": {"models": ["test-model"], "response_format": None},
                    "review": {"models": ["test-model"], "response_format": "json_object"},
                    "director": {"models": ["test-model"], "response_format": "json_object"},
                    "synopsis": {"models": ["test-model"], "response_format": "json_object"},
                }
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").write_text(json.dumps(providers_cfg), encoding="utf-8")
    os.environ["TEST_API_KEY_AUTO2"] = "sk-test"
    # Mock time.sleep in LLM provider to avoid blocking during retries
    import novel_engine.core.llm_provider as lp_mod
    monkeypatch.setattr(lp_mod.time, "sleep", lambda x: None)
    try:
        from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
        from novel_engine.core.llm_client import MockLLMClient
        orch = PipelineOrchestrator(project_root=str(tmp_path), llm_client=MockLLMClient())
        # force reviewer to always return a below-min score so recovery cannot succeed
        orch.reviewer.review_chapter = lambda *a, **k: {"total_score": 70, "normalized_score": 58.3, "verdict": "fix", "issues": [], "scores": {}}
        recorded = []
        orch.defects = type("D", (), {"add": lambda self, c, k, d: recorded.append((c, k, d))})()
        ok = orch._recover_chapter(5, {}, {}, {}, 82)
        assert ok is False
        assert any(k == "below_min_ch" for (_, k, _) in recorded)
    finally:
        os.environ.pop("TEST_API_KEY_AUTO2", None)


def test_continuity_auditor_detects_change():
    from novel_engine.quality.continuity_auditor import ContinuityAuditor
    a = {"world": {"realms": {"xian": {"name": "仙界", "description": "old"}}}}
    b = {"world": {"realms": {"xian": {"name": "仙界", "description": "new"}}}}
    aud = ContinuityAuditor()
    res = aud.audit("xian", a, b)
    assert res["passed"] is False
    assert any("description" in c for c in res["contradictions"])


def test_forbidden_gate_flags_violation(tmp_path, monkeypatch):
    import json, os, time
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    rules = {"rules": [{"id": "no_secret_xy", "type": "regex", "pattern": "秘密XY",
                        "severity": "block"}]}
    (tmp_path / "config" / "forbidden.json").write_text(json.dumps(rules), encoding="utf-8")
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({"llm": {"use_mock": True}, "review_llm": {"use_mock": True},
                    "fallback_llm": {"use_mock": True}}), encoding="utf-8")
    providers_cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_FORBIDDEN_KEY",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["test-model"], "response_format": "json_object"},
                    "polish": {"models": ["test-model"], "response_format": None},
                    "outline": {"models": ["test-model"], "response_format": None},
                    "review": {"models": ["test-model"], "response_format": "json_object"},
                    "director": {"models": ["test-model"], "response_format": "json_object"},
                    "synopsis": {"models": ["test-model"], "response_format": "json_object"},
                }
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").write_text(json.dumps(providers_cfg), encoding="utf-8")
    os.environ["TEST_FORBIDDEN_KEY"] = "sk-test"
    import novel_engine.core.llm_provider as lp_mod
    monkeypatch.setattr(lp_mod.time, "sleep", lambda x: None)
    try:
        from novel_engine.quality.forbidden_scanner import ForbiddenScanner
        from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
        from novel_engine.core.llm_client import MockLLMClient
        orch = PipelineOrchestrator(project_root=str(tmp_path), llm_client=MockLLMClient())
        orch.forbidden = ForbiddenScanner(rules_path=str(tmp_path / "config" / "forbidden.json"))
        viol = orch._forbidden_violations("角色说出了秘密XY", "")
        assert len(viol) >= 1
    finally:
        os.environ.pop("TEST_FORBIDDEN_KEY", None)


def test_run_full_unattended_mini(tmp_path, monkeypatch):
    import json, os, types, time
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    cfg = {
        "llm": {"use_mock": True}, "review_llm": {"use_mock": True}, "fallback_llm": {"use_mock": True},
        "quality": {"min_chapter_score": 82, "fix_threshold": 60, "publication_line": 88},
        "autonomy": {"chapter_regen_max_retries": 1, "remediation_max_rounds": 1,
                     "remediation_per_chapter_retries": 1, "audit_interval_chapters": 1},
    }
    (tmp_path / "config" / "runtime_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    providers_cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_RUN_FULL_KEY",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["test-model"], "response_format": "json_object"},
                    "polish": {"models": ["test-model"], "response_format": None},
                    "outline": {"models": ["test-model"], "response_format": None},
                    "review": {"models": ["test-model"], "response_format": "json_object"},
                    "director": {"models": ["test-model"], "response_format": "json_object"},
                    "synopsis": {"models": ["test-model"], "response_format": "json_object"},
                }
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").write_text(json.dumps(providers_cfg), encoding="utf-8")
    os.environ["TEST_RUN_FULL_KEY"] = "sk-test"
    import novel_engine.core.llm_provider as lp_mod
    monkeypatch.setattr(lp_mod.time, "sleep", lambda x: None)
    try:
        (tmp_path / "task_card.md").write_text("# Task\n写小说", encoding="utf-8")
        (tmp_path / "synopsis.md").write_text("简介", encoding="utf-8")
        (tmp_path / "world_state.json").write_text("{}", encoding="utf-8")
        from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
        from novel_engine.core.llm_client import MockLLMClient
        orch = PipelineOrchestrator(project_root=str(tmp_path), llm_client=MockLLMClient())
        novel = types.SimpleNamespace(content="这是正文，没有违规。", metadata={})
        orch.writer.generate_full_chapter = lambda *a, **k: novel
        orch.writer.polish_chapter = lambda n, *a, **k: n
        orch.reviewer.review_chapter = lambda *a, **k: {"total_score": 90, "normalized_score": 75.0, "verdict": "pass", "issues": [], "scores": {}}
        report = orch.run_full_unattended(total_chapters=3, task_card="写第N章", synopsis="简介", world_state={})
        assert report["status"] == "published", report
        assert report["pending_defects"] == 0
        rp = tmp_path / "audit" / "production_report.json"
        assert rp.exists()
    finally:
        os.environ.pop("TEST_RUN_FULL_KEY", None)

