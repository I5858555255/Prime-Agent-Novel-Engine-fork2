"""Update test_model_router.py to reflect removed planning router and base_url fix."""
import os
from pathlib import Path
from novel_engine.core.model_router import ModelRouter, TimeoutSpec
from novel_engine.core.errors import ConfigFatalError, TransientLLMError, PermanentLLMError


def _fake_client(text):
    class C:
        def chat_completion(self, messages, **k):
            return text
    return C()


def test_router_picks_first_with_capacity(tmp_path):
    cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_API_KEY",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["M1", "M2"], "response_format": None},
                }
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "llm_providers.json").write_text(
        __import__('json').dumps(cfg, ensure_ascii=False), encoding='utf-8'
    )
    os.environ["TEST_API_KEY"] = "sk-test"
    try:
        providers = {"M1": _fake_client("A"), "M2": _fake_client("B")}
        r = ModelRouter("scenes", tmp_path, providers)
        result = r.chat_completion([{"role": "user", "content": "x"}])
        assert isinstance(result, dict)
        assert result["content"] == "A"
        assert result["_model_used"] == "M1"
        assert result["_phase"] == "scenes"
    finally:
        os.environ.pop("TEST_API_KEY", None)


def test_router_falls_to_next_on_429(tmp_path, monkeypatch):
    from novel_engine.core.rate_limiter import RateLimitError
    cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_API_KEY2",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["M1", "M2"], "response_format": None},
                }
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "llm_providers.json").write_text(
        __import__('json').dumps(cfg, ensure_ascii=False), encoding='utf-8'
    )
    os.environ["TEST_API_KEY2"] = "sk-test"
    try:
        class C429:
            def chat_completion(self, messages, **k):
                raise RateLimitError("429")
        class COK:
            def chat_completion(self, messages, **k):
                return "ok"
        providers = {"M1": C429(), "M2": COK()}
        r = ModelRouter("scenes", tmp_path, providers)
        result = r.chat_completion([{"role": "user", "content": "x"}])
        assert isinstance(result, dict)
        assert result["content"] == "ok"
        assert result["_model_used"] == "M2"
    finally:
        os.environ.pop("TEST_API_KEY2", None)


def test_orchestrator_builds_routers(tmp_path, monkeypatch):
    import json
    import pipeline.pipeline_orchestrator as po_mod
    root = os.path.dirname(os.path.dirname(po_mod.__file__))
    providers_path = Path(root) / "config" / "llm_providers.json"
    if providers_path.exists():
        # Real config: set the env var the active profile actually requires
        real = json.loads(providers_path.read_text(encoding="utf-8"))
        active = real["active_profile"]
        key_env = real["profiles"][active]["api_key_env"]
        monkeypatch.setenv(key_env, "sk-test")
    else:
        monkeypatch.setenv("LLM_API_KEY", "sk-test")
        providers_path.parent.mkdir(parents=True, exist_ok=True)
        providers_path.write_text(
            __import__('json').dumps({
                "active_profile": "siliconflow",
                "profiles": {
                    "siliconflow": {
                        "base_url": "https://api.siliconflow.cn",
                        "api_key_env": "LLM_API_KEY",
                        "timeout_s": 120,
                        "max_retries": 2,
                        "default_extra_body": {},
                        "phases": {
                            "outline":  {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": None},
                            "director": {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": "json_object"},
                            "synopsis": {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": "json_object"},
                            "scenes":   {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": "json_object", "concurrency": 4},
                            "polish":   {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": None, "concurrency": 4},
                            "review":   {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": "json_object"},
                        }
                    }
                }
            }, ensure_ascii=False, indent=2),
            encoding='utf-8'
        )
    from pipeline.pipeline_orchestrator import PipelineOrchestrator
    orch = PipelineOrchestrator(project_root=root, llm_client=None)
    assert orch.writer is not None
    assert hasattr(orch, "scene_router")
    assert hasattr(orch, "polish_router")
    assert hasattr(orch, "outline_router")
    assert hasattr(orch, "review_router")
    assert hasattr(orch, "director")
    assert hasattr(orch, "synopsis_agent")
    # Verify no plan_router exists
    assert not hasattr(orch, "plan_router")


def test_missing_phase_raises_config_fatal_error(tmp_path, monkeypatch):
    """Verify that _validate_profile rejects empty phases in profile config."""
    import json
    cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "TEST_MISSING_KEY",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {}  # empty phases should fail validation
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "llm_providers.json").write_text(
        __import__('json').dumps(cfg, ensure_ascii=False), encoding='utf-8'
    )
    os.environ["TEST_MISSING_KEY"] = "sk-test"
    try:
        ModelRouter("scenes", tmp_path)
        assert False, "Should have raised ConfigFatalError for empty phases"
    except ConfigFatalError as e:
        assert "phases" in str(e).lower()
    finally:
        os.environ.pop("TEST_MISSING_KEY", None)


def test_modelrouter_loads_from_llm_providers_json(tmp_path):
    cfg = {
        "active_profile": "siliconflow",
        "profiles": {
            "siliconflow": {
                "base_url": "https://api.siliconflow.cn",
                "api_key_env": "LLM_API_KEY",
                "timeout_s": 120,
                "max_retries": 2,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": "json_object", "concurrency": 4, "stream": True, "idle_timeout_s": 100, "total_timeout_s": 650},
                    "review": {"models": ["deepseek-ai/DeepSeek-V3.2"], "response_format": "json_object", "stream": True, "idle_timeout_s": 100, "total_timeout_s": 220},
                }
            }
        }
    }
    (tmp_path / "config" / "llm_providers.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "llm_providers.json").write_text(
        __import__('json').dumps(cfg, ensure_ascii=False), encoding='utf-8'
    )
    os.environ["LLM_API_KEY"] = "sk-test"
    try:
        r = ModelRouter("scenes", tmp_path)
        assert r.models == ["deepseek-ai/DeepSeek-V3.2"]
        assert r.response_format == "json_object"
        assert r.concurrency == 4
        assert isinstance(r.timeout, TimeoutSpec)
        assert r.timeout.stream is True
        assert r.timeout.idle_s == 100
        assert r.timeout.total_s == 650

        r2 = ModelRouter("review", tmp_path)
        assert "reasoning_effort" not in r2.extra_body
        assert r2.timeout.total_s == 220
    finally:
        os.environ.pop("LLM_API_KEY", None)


def test_modelrouter_failfast_invalid_profile(tmp_path):
    cfg = {
        "active_profile": "nonexistent",
        "profiles": {}
    }
    (tmp_path / "config" / "llm_providers.json").parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "llm_providers.json").write_text(
        __import__('json').dumps(cfg, ensure_ascii=False), encoding='utf-8'
    )
    try:
        ModelRouter("scenes", tmp_path)
        assert False, "Should have raised ConfigFatalError"
    except ConfigFatalError as e:
        assert "nonexistent" in str(e)
