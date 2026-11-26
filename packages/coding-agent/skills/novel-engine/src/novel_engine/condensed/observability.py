"""Observability v0: trace ids, expansion-call telemetry, budget guard.

v0 scope (spec items 6/7): trace correlation for the expansion layer + a
clean quota-halt readout that replaces the round-2 mystery freeze. The
`telemetry` table is created lazily (CREATE TABLE IF NOT EXISTS) so both new
and existing DBs work. M2 (prompt cache, drift_window/drift_alert, paging) is
NOT in this module.

Two budget-guard surfaces, both reading the real `usage` table (never
`telemetry`, so the guard and `status` can't diverge):
- `budget_guard(db, cap, alert_pct)` -> dict readout: `halt` at 100%,
  `alert` at `alert_pct` (writes a cap-approach telemetry row). This is what
  the `runner.expand` branch consumes (a clean "quota" halt, no mystery freeze).
- `budget_guard_raises(db, cap, alert_pct)` -> raises `QuotaExceeded` at 100%.
  This is the spec's "raises QuotaExceeded -> clean 'quota' halt" contract; a
  caller that wants the raise form (instead of inspecting the dict) uses it.
"""
from __future__ import annotations

import datetime as _dt
import uuid

from .quality_store import new_trace_id
from .volume_knobs import load_quality_config


class QuotaExceeded(RuntimeError):
    """The monthly token cap is reached (100%). The run halts cleanly with a
    clear "quota" reason instead of the round-2 mystery 30-second freeze.

    Carries the `budget_guard` readout dict (used / cap / pct / cap_reached) so
    the halt event is self-describing. A `QuotaExceeded` is a *run-level*
    failure.json event, NEVER a verdict leaf value (the critic never runs after
    a quota halt — spec data contract)."""

    def __init__(self, guard: dict):
        self.guard = guard
        super().__init__(f"monthly token cap reached: {guard.get('used')} >= {guard.get('cap')} ({guard.get('pct')}%)")


def _estimate_tokens(text: str) -> int:
    """Conservative token estimate (CJK≈1 token/char, ASCII≈4 chars/token),
    mirroring core/llm_client.py `_estimate_usage`."""
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) > 0x2E7F)
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def _ensure_telemetry(db) -> None:
    db.execute(
        "CREATE TABLE IF NOT EXISTS telemetry("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, trace_id TEXT NOT NULL, run_id TEXT NOT NULL, "
        "kind TEXT NOT NULL, tokens INTEGER NOT NULL DEFAULT 0, model TEXT, prompt_ver TEXT, "
        "payload TEXT, captured_at TEXT NOT NULL)")


def record_expansion_call(db, trace_id: str, run_id: str, total_tokens: int,
                          model: str, prompt_ver: str | None = None,
                          usage_estimated: bool = False) -> None:
    """Record one expansion LLM call: a `telemetry` row (kind='expansion')
    AND a `usage` row so the budget guard + `status` both see it.

    `run_id` must be unique per call (usage.run_id is the PK). `captured_at`
    is set to now (UTC ISO) so the row counts in the current monthly window.
    """
    _ensure_telemetry(db)
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    with db:
        db.execute(
            "INSERT INTO telemetry(trace_id, run_id, kind, tokens, model, prompt_ver, payload, captured_at) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (trace_id, run_id, "expansion", int(total_tokens), model, prompt_ver,
             "1" if usage_estimated else "0", now))
        db.execute(
            "INSERT INTO usage(run_id,total_tokens,reported_requests,captured_at) VALUES(?,?,?,?) "
            "ON CONFLICT(run_id) DO UPDATE SET total_tokens=MAX(usage.total_tokens,excluded.total_tokens), "
            "captured_at=excluded.captured_at",
            (run_id, int(total_tokens), 1, now))


def budget_guard(db, cap_tokens: int, alert_pct: int = 90) -> dict:
    """Clean quota readout: monthly SUM(usage.total_tokens) (current calendar
    month by captured_at prefix + NULL-legacy rows) via quality_store's window
    logic; alert at `alert_pct`, halt at 100%. Pure read — the only optional
    write is a budget-alert telemetry row at the alert boundary."""
    from .quality_store import budget_status
    status = budget_status(db, cap_tokens)
    used = status["used"]
    if cap_tokens and used >= cap_tokens:
        return {"halt": True, "reason": "quota", "used": used, "cap": cap_tokens, "pct": 100}
    pct = 100.0 * used / cap_tokens if cap_tokens else 0.0
    out = {"halt": False, "used": used, "cap": cap_tokens, "pct": round(pct, 1), "alert": False}
    if alert_pct and pct >= alert_pct:
        out["alert"] = True
        _ensure_telemetry(db)
        with db:
            db.execute(
                "INSERT INTO telemetry(trace_id, run_id, kind, tokens, model, prompt_ver, payload, captured_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("", "budget-alert-" + uuid.uuid4().hex, "budget_alert", 0, None, None,
                 f'{{"pct": {out["pct"]}, "alert_pct": {alert_pct}}}',
                 _dt.datetime.now(_dt.timezone.utc).isoformat()))
    return out


def budget_guard_configured(db) -> dict:
    """budget_guard with the cap/alert_pct from quality_config.json."""
    cfg = load_quality_config()
    return budget_guard(db, int(cfg.get("token_cap", 0)), int(cfg.get("alert_pct", 90)))


def budget_guard_raises(db, cap_tokens: int, alert_pct: int = 90) -> dict:
    """Raise-form budget guard: returns the `budget_guard` readout when under
    the cap, raises `QuotaExceeded` at 100%. This is the spec's "raises
    QuotaExceeded -> the runner halts cleanly with reason 'quota'" contract.
    (The dict-returning `budget_guard` is what the `runner.expand` branch
    consumes directly; this helper is for callers that want the raise form.)"""
    guard = budget_guard(db, cap_tokens, alert_pct)
    if guard.get("halt"):
        raise QuotaExceeded(guard)
    return guard


def budget_guard_raises_configured(db) -> dict:
    """budget_guard_raises with the cap/alert_pct from quality_config.json."""
    cfg = load_quality_config()
    return budget_guard_raises(db, int(cfg.get("token_cap", 0)), int(cfg.get("alert_pct", 90)))
