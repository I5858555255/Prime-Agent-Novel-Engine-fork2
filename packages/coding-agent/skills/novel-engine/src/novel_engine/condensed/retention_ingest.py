"""M2 OQ-gate foundation: deterministic retention ingest (design §3).

The v0 `retention` table (enable_quality-gated, `CREATE TABLE retention(...)`
in store.py) is the schema-only stub. This module adds the **deterministic,
offline** ingest: it reads a `feed_records` structure (the OQ1 delivery /
reader-analytics export) and UPSERTs it into the table. **No network, no LLM.**

No-op semantics: an empty `feed_records` writes nothing and returns 0
(explicit, observable "not ready" — it never fabricates retention data).

`enable_quality` gate: the `retention` table only exists when the quality
layer is enabled (the same gated `executescript` block as `quality_verdicts`).
When the table is absent the module raises `RuntimeError('quality layer not
enabled')` — mirroring `quality_store._require_quality`.
"""
from __future__ import annotations

_DIMS = ("read_through", "reread", "abandon_rate")


def _retention_enabled(db) -> bool:
    row = db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='retention'").fetchone()
    return row is not None


def _require_retention(db) -> None:
    if not _retention_enabled(db):
        raise RuntimeError("quality layer not enabled")


def _is_float(value) -> bool:
    """float but NOT bool (bool is a subclass of int; a `True`/`False`
    read-through is invalid). Mirrors the bounded-verdict discipline: the
    field must be a real float in [0, 1]."""
    return isinstance(value, float) and not isinstance(value, bool)


def _validate_feed_record(record: dict) -> tuple[int, float, float, float, str, str]:
    """Validate one feed record; return the normalized 6-tuple to store.

    chapter is int (not bool); read_through/reread/abandon_rate are floats in
    0..1 inclusive; source/captured_at are str. Invalid -> ValueError naming
    the offending field.
    """
    chapter = record.get("chapter")
    if isinstance(chapter, bool) or not isinstance(chapter, int):
        raise ValueError(f"retention feed record 'chapter' must be int, got {type(chapter).__name__}")
    values = {}
    for dim in _DIMS:
        val = record.get(dim)
        if not _is_float(val):
            raise ValueError(f"retention feed record '{dim}' must be float, got {type(val).__name__}")
        if not (0.0 <= val <= 1.0):
            raise ValueError(f"retention feed record '{dim}' must be in [0, 1], got {val}")
        values[dim] = val
    source = record.get("source")
    if not isinstance(source, str):
        raise ValueError(f"retention feed record 'source' must be str, got {type(source).__name__}")
    captured_at = record.get("captured_at")
    if not isinstance(captured_at, str):
        raise ValueError(f"retention feed record 'captured_at' must be str, got {type(captured_at).__name__}")
    return (chapter, values["read_through"], values["reread"], values["abandon_rate"], source, captured_at)


def ingest_retention(db, feed_records: list[dict], source: str) -> int:
    """Write `feed_records` into the v0 `retention` table (UPSERT on chapter).

    - Deterministic: validates field type/range only, writes, returns the row
      count (= len(feed_records)).
    - **Explicit no-op:** `feed_records == []` -> returns 0, writes nothing,
      fabricates no data (the OQ1 "no feed" signal).
    - `enable_quality`-gated: when the `retention` table is absent (a
      disabled-layer DB) raises `RuntimeError('quality layer not enabled')`.

    The `source` argument is the ingest-run-level source label; each record's
    own `source` field is what lands in the row. Per-record values win on an
    upsert (a later feed for the same `chapter` overwrites the earlier one).
    """
    _require_retention(db)
    if not feed_records:
        return 0
    rows = [_validate_feed_record(rec) for rec in feed_records]
    with db:
        for chapter, rt, rr, ar, rec_source, captured_at in rows:
            db.execute(
                "INSERT INTO retention(chapter, read_through, reread, abandon_rate, captured_at, source) "
                "VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(chapter) DO UPDATE SET "
                "read_through=excluded.read_through, reread=excluded.reread, "
                "abandon_rate=excluded.abandon_rate, captured_at=excluded.captured_at, "
                "source=excluded.source",
                (chapter, rt, rr, ar, captured_at, rec_source))
    return len(rows)
