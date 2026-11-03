"""Provider helpers for the expansion loop (reuses the active profile config)."""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG = Path(__file__).resolve().parent.parent / "config" / "llm_providers.json"
_cache: dict | None = None


def load_provider(phase: str) -> tuple[str, str, str]:
    """Return (base_url, api_key_env, model) for the active profile's phase."""
    global _cache
    if _cache is None:
        _cache = json.loads(_CONFIG.read_text(encoding="utf-8"))
    active = _cache["profiles"][_cache["active_profile"]]
    base_url = active["base_url"]
    key_env = active["api_key_env"]
    phases = active.get("phases", {})
    model = None
    if phase in phases:
        models = phases[phase].get("models") or []
        model = models[0] if models else None
    if model is None:
        for p in phases.values():
            models = p.get("models") or []
            if models:
                model = models[0]
                break
    if model is None:
        model = active.get("default_model", "")
    return base_url, key_env, model
