import time
from core.rate_limiter import ModelRateLimiter, RateLimitError


def test_acquire_blocks_until_window_rolls(monkeypatch):
    """Use fake monotonic clock and no-op sleep to avoid real blocking."""
    fake_now = [100.0]
    def fake_monotonic():
        return fake_now[0]
    # Patch both monotonic and sleep in the rate_limiter module
    import novel_engine.core.rate_limiter as rl_mod
    monkeypatch.setattr(rl_mod.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(rl_mod.time, "sleep", lambda x: None)  # no-op sleep

    lim = ModelRateLimiter(rpm=2, tpm=1000)
    lim.acquire(400)
    lim.acquire(400)
    # At this point: requests=2, tokens=800, win_start=100
    assert lim._requests == 2.0
    assert lim._tokens == 800.0

    # Advance fake time past 60s window so it rolls over
    fake_now[0] = 160.0
    lim.acquire(400)
    # Window rolled: requests reset to 1, tokens to 400
    assert lim._requests == 1.0
    assert lim._tokens == 400.0
    assert lim._win_start == 160.0


def test_acquire_single_estimate_over_tpm_does_not_hang(monkeypatch):
    """A single estimate larger than the tpm window must be admitted (clamped),
    never spin forever — that previously stalled unattended production."""
    import novel_engine.core.rate_limiter as rl_mod
    monkeypatch.setattr(rl_mod.time, "monotonic", lambda: 100.0)
    calls = {"n": 0}
    def _no_sleep(_):
        calls["n"] += 1
        assert calls["n"] < 5, "acquire spun instead of admitting an over-window estimate"
    monkeypatch.setattr(rl_mod.time, "sleep", _no_sleep)

    lim = ModelRateLimiter(rpm=20, tpm=10000)  # tpm already shrunk by prior 429s
    lim.acquire(12000)  # estimate exceeds the whole window
    assert lim._requests == 1.0  # admitted, not blocked forever


def test_report_429_shrinks_tpm():
    lim = ModelRateLimiter(rpm=1000, tpm=100000)
    lim.report_429()
    assert lim.tpm == 75000


def test_llm_client_raises_rate_limit():
    import httpx
    from core.llm_client import LLMClient, RateLimitError

    class _FakeResp:
        status_code = 429
        text = "rate limited"
        def raise_for_status(self):
            raise httpx.HTTPStatusError("rate", request=None, response=self)
        def json(self):
            return {}

    class _FakeClient:
        def post(self, *args, **kwargs):
            return _FakeResp()

    c = LLMClient()
    c._local.client = _FakeClient()
    raised = False
    from unittest.mock import patch as _mpatch
    try:
        with _mpatch("time.sleep"):
            c.chat_completion([{"role": "user", "content": "x"}])
    except RateLimitError:
        raised = True
    assert raised, "expected RateLimitError"
