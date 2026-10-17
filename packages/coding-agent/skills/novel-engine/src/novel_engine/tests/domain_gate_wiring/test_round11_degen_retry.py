# -*- coding: utf-8 -*-
"""CC round-11：HTTP 200 但内容为空/退化 的跨温度即时重试（LLMClient 层）。"""
import json

import httpx

from novel_engine.core.llm_client import (
    LLMClient, TimeoutSpec, classify_content, DEGEN_EXHAUSTED_FLAG,
)


def _sse(content: str = "", reasoning: str = "", finish: str = "stop") -> str:
    delta = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
    if finish:
        delta["finish_reason"] = finish
    return f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\n"


def _client(captured_bodies: list[dict], contents: list[tuple]) -> LLMClient:
    """contents: [(content, reasoning_content), ...]，每次请求消费一个。"""
    class _Transport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request):
            try:
                captured_bodies.append(json.loads(request.content.decode("utf-8")))
            except Exception:
                captured_bodies.append({})
            c, r = contents[len(captured_bodies) - 1]
            body = (_sse(content=c, reasoning=r) + "data: [DONE]\n\n").encode("utf-8")
            return httpx.Response(
                200, content=body, headers={"content-type": "text/event-stream"},
                request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"))

    c = LLMClient(api_base="http://127.0.0.1:9999", model="m", use_mock=False, temperature=0.7)
    c._local.client = httpx.Client(
        transport=_Transport(), base_url="http://127.0.0.1:9999")
    return c


_SPEC = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
_GOOD = "这是一段足够长的有效中文场景正文，包含具体动作与人物互动，字数显然超过最小阈值二十个汉字。"


def test_classify_content_buckets():
    assert classify_content("", "") == "HARD_EMPTY"
    assert classify_content("   ", "") == "HARD_EMPTY"
    assert classify_content("", "模型思考了一堆") == "REASONING_ONLY"
    assert classify_content("短", "", phase="scene_write") == "DEGENERATE_SHORT"
    # 非场景阶段的短输出按 VALID，不误判
    assert classify_content("82", "", phase="review") == "VALID"
    assert classify_content(_GOOD, "", phase="scene_write") == "VALID"


def test_empty_then_valid_retries_with_rising_temperature():
    bodies: list[dict] = []
    c = _client(bodies, [("", ""), (_GOOD, "")])
    c.phase = "scene_write"
    res = c.chat_completion([{"role": "user", "content": "x"}], timeout_spec=_SPEC)
    assert res["content"] == _GOOD
    assert len(bodies) == 2  # 第一次空 → 跨温度重试一次成功
    assert bodies[0]["temperature"] == 0.7
    assert bodies[1]["temperature"] in (0.85, 1.0)  # 升温序列
    assert bodies[1]["temperature"] > bodies[0]["temperature"]
    assert "client_nonce" in bodies[1]  # 重试带 cache-bust


def test_reasoning_only_is_retried():
    bodies: list[dict] = []
    c = _client(bodies, [("", "只思考没正文"), (_GOOD, "")])
    c.phase = "director"  # 纯 reasoning 无正文在任何 phase 都重试
    res = c.chat_completion([{"role": "user", "content": "x"}], timeout_spec=_SPEC)
    assert res["content"] == _GOOD
    assert len(bodies) == 2


def test_exhausted_returns_flagged_empty_not_raise():
    bodies: list[dict] = []
    c = _client(bodies, [("", ""), ("", ""), ("", "")])
    c.phase = "scene_write"
    res = c.chat_completion([{"role": "user", "content": "x"}], timeout_spec=_SPEC)
    assert res["content"] == ""
    assert res[DEGEN_EXHAUSTED_FLAG] == "HARD_EMPTY"
    assert len(bodies) == 3  # base + 2 次升温，预算耗尽
    assert [b["temperature"] for b in bodies] == [0.7, 0.85, 1.0]


def test_short_non_scene_phase_not_retried():
    # review 阶段返回极短合法结果（如分数），不应因短正文触发重试
    bodies: list[dict] = []
    c = _client(bodies, [("82", "")])
    c.phase = "review"
    res = c.chat_completion([{"role": "user", "content": "x"}], timeout_spec=_SPEC)
    assert res["content"] == "82"
    assert DEGEN_EXHAUSTED_FLAG not in res
    assert len(bodies) == 1


def test_retry_disabled_returns_empty_immediately():
    bodies: list[dict] = []
    c = _client(bodies, [("", "")])
    c.phase = "scene_write"
    res = c.chat_completion([{"role": "user", "content": "x"}],
                            timeout_spec=_SPEC, retry_on_error=False)
    assert res["content"] == ""
    assert len(bodies) == 1
