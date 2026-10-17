# -*- coding: utf-8 -*-
"""CC round-27：相位级模型路由配置回归。

Model-agnostic: reads the *active* profile from llm_providers.json and asserts
structural validity (phases present, non-empty primary models, review JSON mode),
so the suite stays green across provider switches instead of pinning one baseline.
"""
import json
from pathlib import Path

_CFG = Path(__file__).parents[2] / "config" / "llm_providers.json"


def _active_phases():
    cfg = json.loads(_CFG.read_text(encoding="utf-8"))
    active = cfg.get("active_profile")
    assert active in cfg.get("profiles", {}), f"active_profile {active} missing from profiles"
    return cfg["profiles"][active]["phases"]


def test_all_phases_have_primary_model():
    ph = _active_phases()
    for name in ("scenes", "outline", "director", "synopsis", "polish", "review", "refine"):
        models = ph[name]["models"]
        assert models and isinstance(models[0], str) and models[0], (name, models)


def test_review_phase_keeps_json_mode():
    rev = _active_phases()["review"]
    assert rev.get("response_format") == "json_object"
