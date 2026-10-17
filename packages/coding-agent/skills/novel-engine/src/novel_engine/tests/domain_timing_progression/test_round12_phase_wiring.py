# -*- coding: utf-8 -*-
"""CC round-12 附带：orchestrator/router 持有的 scene client 必须带 phase=scenes，
使 LLMClient 空/极短正文退化门在场景（含定点重生）路径按 phase 生效。"""
import json
import os

from novel_engine.core.model_router import ModelRouter
from novel_engine.core.llm_client import classify_content


class _FakeClient:
    def __init__(self):
        self.phase = None

    def chat_completion(self, messages, **kwargs):
        return "x"


def _router(root, providers):
    cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "ROUND12_TEST_KEY",
                "timeout_s": 60,
                "max_retries": 1,
                "default_extra_body": {},
                "phases": {"scenes": {"models": ["M1"], "response_format": None}},
            }
        },
    }
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "llm_providers.json").write_text(
        json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    os.environ["ROUND12_TEST_KEY"] = "sk-test"
    try:
        return ModelRouter("scenes", root, providers)
    finally:
        os.environ.pop("ROUND12_TEST_KEY", None)


def test_router_propagates_phase_to_injected_clients(tmp_path):
    c = _FakeClient()
    _router(tmp_path, {"M1": c})
    assert c.phase == "scenes"


def test_scene_phase_gates_degenerate_short(tmp_path):
    c = _FakeClient()
    _router(tmp_path, {"M1": c})
    # scene 阶段：极短正文判 DEGENERATE_SHORT；同一内容在 polish/review 阶段判 VALID
    assert classify_content("短", "", phase=c.phase) == "DEGENERATE_SHORT"
    assert classify_content("82", "", phase="polish") == "VALID"
    # 空 / 纯 reasoning 不受 phase 影响
    assert classify_content("", "") == "HARD_EMPTY"
    assert classify_content("", "只思考") == "REASONING_ONLY"
