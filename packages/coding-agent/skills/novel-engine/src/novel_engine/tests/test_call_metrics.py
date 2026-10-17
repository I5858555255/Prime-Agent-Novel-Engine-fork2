"""Tests for core/call_metrics.py — thread-safe accumulator."""
import threading
from pathlib import Path
from novel_engine.core.call_metrics import (
    reset, record_call, snapshot, load_pricing, _Accum, PhaseMetrics
)


def test_reset_clears_all_counters():
    reset()
    record_call(phase="scenes", model="m1", success=True, failover=False,
                prompt_tokens=100, completion_tokens=50, reasoning_tokens=10,
                latency_s=0.5, cost_usd=0.01)
    snap = snapshot()
    assert snap["calls"] == 1
    reset()
    snap = snapshot()
    assert snap["calls"] == 0
    assert snap["total_tokens"] == 0
    assert snap["cost_usd"] == 0.0


def test_record_success_and_failure():
    reset()
    record_call(phase="review", model="m2", success=True, failover=False,
                prompt_tokens=200, completion_tokens=80, reasoning_tokens=20,
                latency_s=0.3, cost_usd=0.02)
    record_call(phase="review", model="m2", success=False, failover=False,
                prompt_tokens=0, completion_tokens=0, reasoning_tokens=0,
                latency_s=0.1, cost_usd=0.0)
    snap = snapshot()
    assert snap["calls"] == 2
    assert snap["successes"] == 1
    assert snap["failures"] == 1
    assert snap["prompt_tokens"] == 200
    assert snap["completion_tokens"] == 80
    assert snap["reasoning_tokens"] == 20
    assert snap["total_tokens"] == 280
    assert snap["cost_usd"] == 0.02


def test_per_phase_breakdown():
    reset()
    record_call(phase="scenes", model="m1", success=True, failover=False,
                prompt_tokens=50, completion_tokens=30, reasoning_tokens=0,
                latency_s=0.1, cost_usd=0.005)
    record_call(phase="outline", model="m1", success=True, failover=False,
                prompt_tokens=40, completion_tokens=20, reasoning_tokens=5,
                latency_s=0.08, cost_usd=0.004)
    snap = snapshot()
    assert "scenes" in snap["per_phase"]
    assert "outline" in snap["per_phase"]
    assert snap["per_phase"]["scenes"]["calls"] == 1
    assert snap["per_phase"]["outline"]["calls"] == 1
    assert snap["per_phase"]["scenes"]["total_tokens"] == 80  # 50+30
    assert snap["per_phase"]["outline"]["total_tokens"] == 60  # 40+20


def test_concurrent_records_thread_safe():
    reset()
    N = 1000
    def worker():
        for _ in range(N):
            record_call(phase="scenes", model="m1", success=True, failover=False,
                        prompt_tokens=10, completion_tokens=5, reasoning_tokens=0,
                        latency_s=0.01, cost_usd=0.001)
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    snap = snapshot()
    assert snap["calls"] == N * 4
    assert snap["successes"] == N * 4
    assert snap["prompt_tokens"] == 10 * N * 4


def test_load_pricing_from_cost_sandbox():
    # load_pricing expects the PROJECT root (where config/ is a subdirectory)
    # Path: novel_engine/tests/test_call_metrics.py -> parent (tests) -> parent (novel_engine) = project root
    root = Path(__file__).parent.parent
    inp, outp = load_pricing(root)
    # cost_sandbox.json has input_per_1m=0.05, output_per_1m=0.15
    assert inp == 0.05 / 1_000_000
    assert outp == 0.15 / 1_000_000


def test_load_pricing_falls_back_on_missing_file():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        inp, outp = load_pricing(root)
        assert inp == 0.0
        assert outp == 0.0
