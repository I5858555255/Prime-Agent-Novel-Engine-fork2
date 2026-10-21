import time
import pytest
from unittest.mock import MagicMock
from novel_engine.core.llm_client import LLMClient, CircuitBreaker, compute_exponential_backoff
from novel_engine.core.errors import TransientLLMError

def test_circuit_breaker_transitions():
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=0.2)
    assert cb.state == "CLOSED"
    assert cb.allow_request() is True

    # Record 2 failures
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "CLOSED"

    # 3rd failure opens breaker
    cb.record_failure()
    assert cb.state == "OPEN"
    assert cb.allow_request() is False

    # Wait for recovery timeout
    time.sleep(0.25)
    assert cb.allow_request() is True
    assert cb.state == "HALF_OPEN"

    # Success in HALF_OPEN resets to CLOSED
    cb.record_success()
    assert cb.state == "CLOSED"
    assert cb.failure_count == 0

def test_exponential_backoff_calculation():
    for attempt in range(5):
        val = compute_exponential_backoff(attempt, base_delay=1.0, max_delay=10.0)
        assert 0.0 <= val <= 10.0

def test_llm_client_circuit_breaker_integration():
    client = LLMClient(use_mock=False)
    # Force breaker to OPEN
    client.circuit_breaker.state = "OPEN"
    client.circuit_breaker.last_state_change = time.time()

    with pytest.raises(TransientLLMError) as exc_info:
        client.chat_completion([{"role": "user", "content": "hi"}])
    assert "Circuit breaker OPEN" in str(exc_info.value)
