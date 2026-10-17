"""Thread-safe API-call accumulator for unified cost tracking.

All LLM calls across phases funnel through llm_client.chat_completion which
records each logical attempt here. production_runner reads the final snapshot
for the production report; pipeline_orchestrator can also inspect per-chapter
counts via snapshot().

Cost calculation: record_call defaults cost_usd to None, in which case the
accumulator computes it from module-level pricing rates (loaded via
reset(project_root) or load_pricing()). reasoning_tokens are NOT double-counted:
they are tracked separately in the reasoning_tokens field and included in the
output-side completion cost only where the API usage already bundles them into
completion_tokens (the common OpenAI-compatible case). When the caller passes an
explicit cost_usd (existing unit tests), that value is used verbatim.
"""
import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

LOCK = threading.Lock()

# Per-phase counters inside a single snapshot.
@dataclass
class PhaseMetrics:
    calls: int = 0
    successes: int = 0
    failures: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

# Module-level accumulator. Initialized lazily on first record_call().
_accumulator: Optional["_Accum"] = None

# Module-level pricing rates (per token), loaded on reset(root).
_input_rate: float = 0.0
_output_rate: float = 0.0


class _Accum:
    __slots__ = ("calls", "successes", "failures", "per_phase",
                 "prompt_tokens", "completion_tokens", "reasoning_tokens",
                 "total_tokens", "cost_usd")

    def __init__(self) -> None:
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self.per_phase: dict[str, PhaseMetrics] = {}
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.total_tokens = 0
        self.cost_usd = 0.0

    def reset(self) -> None:
        self.calls = 0
        self.successes = 0
        self.failures = 0
        self.per_phase.clear()
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.cost_usd = 0.0

    def record(self, *, phase: str, model: str, success: bool,
               failover: bool, prompt_tokens: int, completion_tokens: int,
               reasoning_tokens: int, latency_s: float,
               cost_usd: Optional[float]) -> None:
        # Compute cost from pricing rates when caller did not supply an explicit value.
        # Note: reasoning_tokens are tracked separately and not double-counted here.
        # The API's completion_tokens usually already include reasoning_tokens when
        # returned (OpenAI-compatible convention); if they do not, the caller should
        # pass an explicit cost_usd or adjust completion_tokens upstream.
        if cost_usd is None:
            cost_usd = prompt_tokens * _input_rate + completion_tokens * _output_rate
        self.calls += 1
        if success:
            self.successes += 1
        else:
            self.failures += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.reasoning_tokens += reasoning_tokens
        self.total_tokens += prompt_tokens + completion_tokens
        self.cost_usd += cost_usd
        pm = self.per_phase.get(phase)
        if pm is None:
            pm = PhaseMetrics()
            self.per_phase[phase] = pm
        pm.calls += 1
        if success:
            pm.successes += 1
        else:
            pm.failures += 1
        pm.prompt_tokens += prompt_tokens
        pm.completion_tokens += completion_tokens
        pm.reasoning_tokens += reasoning_tokens
        pm.total_tokens += prompt_tokens + completion_tokens
        pm.cost_usd += cost_usd

    def snapshot(self) -> dict:
        return {
            "calls": self.calls,
            "successes": self.successes,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "per_phase": {
                name: {
                    "calls": pm.calls,
                    "successes": pm.successes,
                    "failures": pm.failures,
                    "prompt_tokens": pm.prompt_tokens,
                    "completion_tokens": pm.completion_tokens,
                    "reasoning_tokens": pm.reasoning_tokens,
                    "total_tokens": pm.total_tokens,
                    "cost_usd": round(pm.cost_usd, 6),
                }
                for name, pm in self.per_phase.items()
            },
        }


def _get_accum() -> _Accum:
    global _accumulator
    if _accumulator is None:
        _accumulator = _Accum()
    return _accumulator


def reset(root: Optional[Path] = None) -> None:
    """Clear the process-wide accumulator. Call at batch start.

    When *root* is provided, pricing rates are loaded from the project's
    llm_providers.json / cost_sandbox.json so that subsequent record_call()
    invocations without an explicit cost_usd will compute costs automatically.
    Passing None leaves rates at 0 (matching pre-change test behaviour).
    """
    global _accumulator, _input_rate, _output_rate
    with LOCK:
        _accumulator = _Accum()
        if root is not None:
            _input_rate, _output_rate = load_pricing(root)


def record_call(*, phase: str, model: str, success: bool, failover: bool,
                prompt_tokens: int, completion_tokens: int,
                reasoning_tokens: int, latency_s: float,
                cost_usd: Optional[float] = None) -> None:
    """Record one logical LLM call (each retry is one record).

    cost_usd defaults to None: when None the accumulator computes the cost
    from the module-level pricing rates (set via reset(root)). Callers that
    supply an explicit value (e.g. unit tests) keep that value verbatim.
    """
    with LOCK:
        acc = _get_accum()
        acc.record(phase=phase, model=model, success=success, failover=failover,
                   prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                   reasoning_tokens=reasoning_tokens, latency_s=latency_s,
                   cost_usd=cost_usd)


def snapshot() -> dict:
    """Return a copy of the current accumulator state."""
    with LOCK:
        acc = _get_accum()
        return acc.snapshot()


def load_pricing(root: Path) -> tuple[float, float]:
    """Load active provider pricing from llm_providers.json.

    Returns (input_per_token, output_per_token). Falls back to cost_sandbox
    or (0, 0) on any error.
    """
    providers_path = root / "config" / "llm_providers.json"
    sandbox_path = root / "config" / "cost_sandbox.json"
    try:
        if providers_path.exists():
            data = json.loads(providers_path.read_text(encoding="utf-8"))
            active = data.get("active_profile", "")
            profiles = data.get("profiles") or {}
            profile = profiles.get(active, {})
            # Look for pricing fields at profile level or inside phases
            pricing = profile.get("pricing") or {}
            if pricing:
                input_per_1m = float(pricing.get("input_per_1m_tokens", 0))
                output_per_1m = float(pricing.get("output_per_1m_tokens", 0))
                if input_per_1m > 0 or output_per_1m > 0:
                    return (input_per_1m / 1_000_000, output_per_1m / 1_000_000)
    except Exception:
        pass
    try:
        if sandbox_path.exists():
            data = json.loads(sandbox_path.read_text(encoding="utf-8"))
            p = data.get("currency_conversion", {}).get("api_pricing", {})
            inp = float(p.get("input_per_1m_tokens", p.get("input_per_1k_tokens", 0) * 1000))
            outp = float(p.get("output_per_1m_tokens", p.get("output_per_1k_tokens", 0) * 1000))
            if inp > 0 or outp > 0:
                return (inp / 1_000_000, outp / 1_000_000)
    except Exception:
        pass
    return (0.0, 0.0)
