# -*- coding: utf-8 -*-
"""CC P0: content-addressed phase cache (disk, cross-process).

Keyed by (chapter_id, phase_name, input_hash) where input_hash is a sha256 over
the canonical JSON of every upstream artifact PLUS prompt/model version tags.
Changing any upstream content (e.g. one scene is regenerated) changes the hash
and invalidates dependent phases automatically -- no manual dependency table.

Scope:
- reviewer  : cache by exact final chapter text (+task/synopsis/world/rubric version),
              so pure infra retries that present the identical finished text are
              not billed/scored twice. Intentional re-reviews happen after edits,
              which change the text and therefore miss.
- director  : cached only AFTER task-card validation passes; feedback retries and
              fresh runs bypass it (reset clears cache/).
- writer    : per-scene caching is already provided by the chapter journal;
              failed / slacker attempts are never cached.

All IO is best-effort: a cache error must never break generation.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

CACHE_ROOT = "cache"
PROMPT_VERSIONS: dict[str, str] = {
    "director": "director_v4",
    "review": "review_v24",  # CC round-24：注入确定性锚点 + pacing/retention 混合分
}


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def content_key(value, version: str | None = None, model: str | None = None) -> str:
    """Deterministic 64-hex sha256 over canonical value + version/model tags."""
    parts = [_canonical(value)]
    if version:
        parts.append(f"ver={version}")
    if model:
        parts.append(f"model={model}")
    return hashlib.sha256("\u0001".join(parts).encode("utf-8")).hexdigest()


def model_fingerprint(root: str | Path, phase: str) -> str:
    """Read active-profile model id(s) for a phase so switching LLM invalidates cache."""
    try:
        cfg_path = Path(root) / "config" / "llm_providers.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        prof = (cfg.get("profiles") or {}).get(cfg.get("active_profile"), {}) or {}
        models = ((prof.get("phases") or {}).get(phase) or {}).get("models") or []
        return f"{cfg.get('active_profile')}:{','.join(str(m) for m in models)}"
    except Exception:
        return ""


class PhaseCache:
    def __init__(self, root: str | Path, cache_dir: str = CACHE_ROOT):
        self.root = Path(root)
        self.cache_dir = self.root / cache_dir

    def _path(self, chapter: int, phase: str, input_hash: str) -> Path:
        return self.cache_dir / f"chapter_{int(chapter)}" / f"{phase}_{input_hash[:16]}.json"

    def get(self, chapter: int, phase: str, input_hash: str):
        """Return cached payload (deep copy) or None. Never raises."""
        try:
            path = self._path(chapter, phase, input_hash)
            if not path.exists():
                return None
            rec = json.loads(path.read_text(encoding="utf-8"))
            if rec.get("input_hash") != input_hash or rec.get("phase") != phase:
                return None
            return copy.deepcopy(rec.get("payload"))
        except Exception:
            return None

    def set(self, chapter: int, phase: str, input_hash: str, payload) -> bool:
        """Atomically persist payload. Returns False on any failure (never raises)."""
        try:
            path = self._path(chapter, phase, input_hash)
            path.parent.mkdir(parents=True, exist_ok=True)
            rec = {"chapter": int(chapter), "phase": phase,
                   "input_hash": input_hash, "payload": payload}
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(rec, ensure_ascii=False, default=str), encoding="utf-8")
            path.with_suffix(".json.tmp").replace(path)
            return True
        except Exception:
            return False

    def cached_call(self, chapter: int, phase: str, input_hash: str, producer):
        """Return cached payload if present else compute via producer() and store."""
        hit = self.get(chapter, phase, input_hash)
        if hit is not None:
            return hit, True
        value = producer()
        self.set(chapter, phase, input_hash, value)
        return value, False
