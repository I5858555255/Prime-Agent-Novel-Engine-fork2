"""Streaming LLM client tests (pkg2b2): mock SSE, idle/total timeout, error classification."""
import json
import math
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import httpx

from novel_engine.core.llm_client import LLMClient, TimeoutSpec
from novel_engine.core.errors import TransientLLMError, PermanentLLMError, ConfigFatalError
from novel_engine.core.model_router import ModelRouter, TimeoutSpec as RouterTimeoutSpec


# ── SSE helper ────────────────────────────────────────────────────────────────

def _sse_chunk(content: str = "", reasoning: str = "",
               finish_reason: str = None, usage: dict = None) -> str:
    """Build one SSE data line."""
    delta: dict = {}
    if content:
        delta["content"] = content
    if reasoning:
        delta["reasoning_content"] = reasoning
    if finish_reason:
        delta["finish_reason"] = finish_reason
    parts: list[str] = []
    if delta:
        parts.append(f"data: {json.dumps({'choices': [{'delta': delta}]})}\n\n")
    if usage:
        parts.append(f"data: {json.dumps({'choices': [], 'usage': usage})}\n\n")
    return "".join(parts)


def _build_sse_stream(chunks: list[str]) -> bytes:
    """Concatenate chunks into a raw bytes response body."""
    return "\n".join(chunks).encode("utf-8")


def _make_mock_response(sse_body: bytes, status_code: int = 200) -> httpx.Response:
    resp = httpx.Response(
        status_code=status_code,
        content=sse_body,
        headers={"content-type": "text/event-stream"},
        request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"),
    )
    return resp


def _make_client(responses: list[httpx.Response]) -> LLMClient:
    """Create an LLMClient with a mock httpx transport that cycles through responses."""
    call_count = [0]
    
    class MockTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            idx = call_count[0] % len(responses)
            call_count[0] += 1
            return responses[idx]
    
    transport = MockTransport()
    # Create client with mock transport
    mock_httpx_client = httpx.Client(transport=transport, base_url="http://127.0.0.1:9999")
    
    client = LLMClient(api_base="http://127.0.0.1:9999", model="test-model", use_mock=False)
    # Replace the local client directly
    client._local.client = mock_httpx_client
    return client


# ── C1.4.1 正常多 chunk ───────────────────────────────────────────────────────

def test_streaming_normal_multi_chunk():
    """Content + reasoning_content 正确累加、finish_reason、末块 usage 落位."""
    chunks = [
        _sse_chunk(content="Hello"),
        _sse_chunk(content=" world"),
        _sse_chunk(reasoning="[thinking]"),
        _sse_chunk(finish_reason="stop", usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    ]
    sse = _build_sse_stream(chunks)
    client = _make_client([_make_mock_response(sse)])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    result = client.chat_completion([], timeout_spec=spec)
    assert result["content"] == "Hello world"
    assert result["reasoning_content"] == "[thinking]"
    assert result["finish_reason"] == "stop"
    assert result["_usage"]["completion_tokens"] == 5


# ── C1.4.2 块间隔超过 idle → TransientLLMError ─────────────────────────────

@patch("time.sleep")
def test_streaming_idle_timeout(mock_sleep):
    """相邻两个数据块间隔超过 idle_s → 中止."""
    t0 = [time.monotonic()]
    def fake_monotonic():
        # 全局递增：每次尝试的块间隔都超过 idle_s（真实服务端会持续不发块）
        fake_monotonic.call_count += 1
        return t0[0] + (fake_monotonic.call_count - 1) * 200
    fake_monotonic.call_count = 0
    with patch("novel_engine.core.llm_client.time.monotonic", side_effect=fake_monotonic):
        chunks = [_sse_chunk(content="a"), _sse_chunk(content="b")]
        sse = _build_sse_stream(chunks)
        client = _make_client([_make_mock_response(sse)])
        spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
        with pytest.raises(TransientLLMError, match="idle"):
            client.chat_completion([], timeout_spec=spec)


# ── C1.4.3 总兜底超时 ────────────────────────────────────────────────────────

def test_streaming_total_cap():
    """持续慢速且极小 total cap → TransientLLMError."""
    chunks = [_sse_chunk(content="x" * 10000)]
    sse = _build_sse_stream(chunks)
    client = _make_client([_make_mock_response(sse)])
    spec = TimeoutSpec(stream=True, idle_s=1000, total_s=1)
    state = {"n": 0}

    def _fake_clock():
        state["n"] += 1
        return float(state["n"] - 1) * 5.0

    with patch("time.sleep"), patch("novel_engine.core.llm_client.time.monotonic", side_effect=_fake_clock):
        with pytest.raises(TransientLLMError, match="total"):
            client.chat_completion([], max_tokens=3, timeout_spec=spec)


# ── C1.4.4 429 后重试成功（带/不带 Retry-After）─────────────────────────────

def test_streaming_retry_on_429_with_retry_after():
    """首次 429（带 Retry-After）后 200 → 成功."""
    resp_429 = httpx.Response(
        429, content=b'{"error":"rate limited"}',
        headers={"Retry-After": "1"},
        request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"),
    )
    chunks = [_sse_chunk(content="ok", finish_reason="stop",
                         usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})]
    resp_200 = _make_mock_response(_build_sse_stream(chunks))
    client = _make_client([resp_429, resp_200])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    with patch("time.sleep", return_value=None):
        result = client.chat_completion([], timeout_spec=spec)
    assert result["content"] == "ok"


def test_streaming_retry_on_429_without_retry_after():
    """429 无 Retry-After 也正常重试."""
    resp_429 = httpx.Response(429, content=b'{"error":"too many requests"}',
                              request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"))
    chunks = [_sse_chunk(content="y", finish_reason="stop",
                         usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})]
    resp_200 = _make_mock_response(_build_sse_stream(chunks))
    client = _make_client([resp_429, resp_200])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    with patch("time.sleep", return_value=None):
        result = client.chat_completion([], timeout_spec=spec)
    assert result["content"] == "y"


# ── C1.4.5 400 → PermanentLLMError 不重试不换模型 ───────────────────────────

def test_streaming_400_permanent():
    """400 直接上抛 PermanentLLMError."""
    resp_400 = httpx.Response(400, content=b'{"error":"bad request"}',
                              request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"))
    client = _make_client([resp_400])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    with pytest.raises(PermanentLLMError):
        client.chat_completion([], timeout_spec=spec)


# ── C1.4.6 401 / 缺 key / 未知 profile → ConfigFatalError ─────────────────────

def test_streaming_401_config_fatal():
    """401 返回 ConfigFatalError."""
    resp_401 = httpx.Response(401, content=b'{"error":"unauthorized"}',
                              request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"))
    client = _make_client([resp_401])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    with pytest.raises(ConfigFatalError):
        client.chat_completion([], timeout_spec=spec)


def test_modelrouter_missing_key_raises_config_fatal(tmp_path):
    """active_profile 环境变量缺失 → ConfigFatalError."""
    cfg = {
        "active_profile": "test",
        "profiles": {
            "test": {
                "base_url": "https://test.example.com",
                "api_key_env": "MISSING_KEY_XYZ",
                "timeout_s": 60,
                "max_retries": 0,
                "default_extra_body": {},
                "phases": {"outline": {"models": ["M1"], "response_format": None}},
            }
        }
    }
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "llm_providers.json").write_text(json.dumps(cfg), encoding="utf-8")
    os.environ.pop("MISSING_KEY_XYZ", None)
    with pytest.raises(ConfigFatalError):
        ModelRouter("outline", tmp_path)


# ── C1.4.7 末块无 usage → 估算 usage ────────────────────────────────────────

def test_streaming_no_usage_estimates():
    """末块无 usage 字段 → 返回估算的数值型 usage."""
    chunks = [_sse_chunk(content="hi", finish_reason="stop")]
    sse = _build_sse_stream(chunks)
    client = _make_client([_make_mock_response(sse)])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    result = client.chat_completion([], max_tokens=100, timeout_spec=spec)
    usage = result["_usage"]
    assert isinstance(usage["prompt_tokens"], int)
    assert isinstance(usage["completion_tokens"], int)
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


# ── C1.4.8 stream=False 非流式路径仍可用 ────────────────────────────────────

def test_non_streaming_path_still_works():
    """stream=False → 非流式返回相同 dict 形状."""
    normal_resp = httpx.Response(
        200,
        content=json.dumps({
            "choices": [{"message": {"content": "direct", "role": "assistant"},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }).encode(),
        request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"),
    )
    client = _make_client([normal_resp])
    spec = TimeoutSpec(stream=False, idle_s=10, total_s=1000)
    result = client.chat_completion([], timeout_spec=spec)
    assert result["content"] == "direct"
    assert result["finish_reason"] == "stop"
    assert result["_usage"]["completion_tokens"] == 2


# ── C1.4.9 多模型故障转移 ───────────────────────────────────────────────────

def test_multimodel_fallback_on_transient():
    """第一候选 Transient 耗尽 → 切到第二候选成功."""
    resp_err = httpx.Response(503, content=b"service unavailable",
                              request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"))
    chunks = [_sse_chunk(content="from_m2", finish_reason="stop",
                         usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2})]
    resp_ok = _make_mock_response(_build_sse_stream(chunks))
    client = _make_client([resp_err, resp_ok])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    with patch("time.sleep", return_value=None):
        result = client.chat_completion([], timeout_spec=spec)
    assert result["content"] == "from_m2"


def test_multimodel_all_fail():
    """所有候选都失败 → 上抛 TransientLLMError."""
    err_resp = httpx.Response(502, content=b"bad gateway",
                              request=httpx.Request("POST", "http://127.0.0.1:9999/v1/chat/completions"))
    client = _make_client([err_resp, err_resp])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    with patch("time.sleep"), pytest.raises(TransientLLMError):
        client.chat_completion([], timeout_spec=spec)


# ── C1.4.10 出厂配置契约 ─────────────────────────────────────────────────────

def test_factory_config_timeout_specs():
    """active profile 各 phase 的 stream/idle_timeout_s/total_timeout_s 可被 TimeoutSpec 解析且结构有效.

    Model-agnostic: asserts structural validity (boolean stream, positive timeouts)
    instead of pinning provider-specific timeout values.
    """
    cfg_path = Path(__file__).parent.parent / "config" / "llm_providers.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    profile = cfg["profiles"][cfg["active_profile"]]
    for phase in ["outline", "director", "synopsis", "scenes", "polish", "review", "refine"]:
        phase_cfg = profile["phases"].get(phase, {})
        spec = RouterTimeoutSpec.from_phase_cfg(phase_cfg, phase)
        assert isinstance(spec.stream, bool), f"{phase} stream"
        assert spec.idle_s > 0, f"{phase} idle"
        assert spec.total_s > 0, f"{phase} total"
        assert spec.connect_s > 0, f"{phase} connect"


# ── C1.4.10 扩展 ──────────────────────────────────────────────────────────────

def test_effective_total_capped_by_max_tokens():
    """effective_total 受 max_tokens 约束：min(total_s, ceil(max_tokens/3)+60)."""
    spec = TimeoutSpec(stream=True, idle_s=100, total_s=650)
    assert spec.effective_total(None) == 650
    assert spec.effective_total(100) == min(650, math.ceil(100 / 3) + 60)
    assert spec.effective_total(2000) == min(650, math.ceil(2000 / 3) + 60)


def test_streaming_top_level_finish_reason():
    """finish_reason 位于 choices[0] 顶层（非 delta）也能识别（length 截断信号）。"""
    final = (
        'data: '
        + json.dumps({"choices": [{"finish_reason": "length", "delta": {}}],
                       "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        + '\n\n'
    )
    sse = _build_sse_stream([_sse_chunk(content="x"), final])
    client = _make_client([_make_mock_response(sse)])
    spec = TimeoutSpec(stream=True, idle_s=10, total_s=1000)
    result = client.chat_completion([], timeout_spec=spec)
    assert result["finish_reason"] == "length"
