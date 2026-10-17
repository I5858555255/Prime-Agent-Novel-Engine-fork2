"""Factory config contract tests (W7): verify shipped config values are correct."""
import json
from pathlib import Path


def test_llm_providers_factory_contract():
    """Verify llm_providers.json has structurally valid shipped values (model-agnostic).

    Asserts the active profile is present and complete, without pinning a specific
    provider/model, so the suite stays green across provider switches
    (siliconflow → agnes → token plan).
    """
    cfg_path = Path(__file__).parent.parent / "config" / "llm_providers.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    active = cfg["active_profile"]
    assert active in cfg["profiles"], f"active_profile {active} missing from profiles"

    profile = cfg["profiles"][active]
    base_url = profile["base_url"]
    assert base_url, "base_url must be set"
    assert "/v1" not in base_url, f"base_url should not contain /v1: {base_url}"
    assert profile.get("api_key_env"), "api_key_env must be set"
    assert profile.get("family"), "family must be set"

    # All standard phases must exist and carry a non-empty primary model
    phases = profile["phases"]
    for phase_name in ["outline", "director", "synopsis", "scenes", "polish", "review", "refine"]:
        assert phase_name in phases, f"missing phase: {phase_name}"
        models = phases[phase_name]["models"]
        assert models and isinstance(models[0], str) and models[0], f"{phase_name} models: {models}"


def test_runtime_config_factory_contract():
    """Verify runtime_config.json has expected shipped quality values."""
    cfg_path = Path(__file__).parent.parent / "config" / "runtime_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    # Quality policy values
    qp = cfg.get("quality_policy", {})
    assert qp.get("chapter_target_chars") == 8000
    assert qp.get("min_ratio") == 0.85
    assert qp.get("max_ratio") == 1.3125

    # Quality section
    q = cfg.get("quality", {})
    assert q.get("publication_line") == 88
