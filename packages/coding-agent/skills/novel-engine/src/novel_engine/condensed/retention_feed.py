"""M2 OQ-gate: OQ1 minimal retention-feed loader (design §1 of the minimal closed loop).

The OQ1 gate's real input is a reader-analytics / delivery export (per-chapter
read-through, reread, and abandon-rate). That platform is not available offline,
so this module loads the **minimal offline stand-in**: a local JSON file of
per-chapter retention records. It is the data `retention_ingest.ingest_retention`
writes into the v0 `retention` table.

No-op discipline (mirrors the OQ-gate foundations): a missing or empty file
returns `[]` — an EXPLICIT "no feed" signal, never fabricated retention data.
Field validation mirrors `retention_ingest._validate_feed_record` (float 0..1,
rejecting bool/int; chapter int, rejecting bool) so a malformed record is
DROPPED rather than poisoned into the ingest.

Fully deterministic, no network, no LLM.
"""
from __future__ import annotations

import json
from pathlib import Path

_DIMS = ("read_through", "reread", "abandon_rate")


def _is_float(value) -> bool:
    """float but NOT bool (bool is a subclass of int; a True/False read-through
    is invalid). Mirrors retention_ingest._is_float."""
    return isinstance(value, float) and not isinstance(value, bool)


def _valid_feed_record(record: dict) -> dict | None:
    """Return a normalized feed record iff it is well-formed; None otherwise.

    chapter is a true int (not bool); read_through/reread/abandon_rate are
    floats in [0, 1] (bool/int rejected); source/captured_at are str. A missing
    source defaults to "" (an ingest-run label is supplied separately by
    `ingest_retention`); a missing captured_at defaults to "" as well."""
    if not isinstance(record, dict):
        return None
    chapter = record.get("chapter")
    if isinstance(chapter, bool) or not isinstance(chapter, int):
        return None
    for dim in _DIMS:
        val = record.get(dim)
        if not _is_float(val):
            return None
        if not (0.0 <= val <= 1.0):
            return None
    source = record.get("source")
    if source is None:
        source = ""
    if not isinstance(source, str):
        return None
    captured_at = record.get("captured_at")
    if captured_at is None:
        captured_at = ""
    if not isinstance(captured_at, str):
        return None
    return {
        "chapter": chapter,
        "read_through": record["read_through"],
        "reread": record["reread"],
        "abandon_rate": record["abandon_rate"],
        "source": source,
        "captured_at": captured_at,
    }


def load_retention_feed(path) -> list[dict]:
    """Load a minimal OQ1 retention feed from a local JSON file.

    - The file is a JSON **list** of feed records (each as above).
    - Missing file / empty file / JSON that is not a list → `[]` (EXPLICIT no-op:
      "no feed", never fabricated retention, never raises on absence).
    - Malformed records (bad chapter / out-of-range or non-float rate / bad
      source) are DROPPED so a bad entry never reaches the ingest.
    - Dedup by chapter (a later record for the same chapter overwrites an
      earlier one, matching the UPSERT-on-chapter semantics of
      `ingest_retention`; first-seen order is preserved but the latest value
      wins per chapter, for determinism).
    """
    p = Path(path)
    if not p.exists():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        # Corrupt / unreadable file is treated as "no feed", not an error:
        # the OQ1 gate must degrade to an explicit no-op, never crash the loop.
        return []
    if not isinstance(raw, list):
        return []
    # Last-seen value wins per chapter (UPSERT-on-chapter semantics), but keep
    # first-seen order for determinism.
    by_chapter: dict[int, dict] = {}
    order: list[int] = []
    for record in raw:
        normalized = _valid_feed_record(record)
        if normalized is None:
            continue
        chapter = normalized["chapter"]
        if chapter not in by_chapter:
            order.append(chapter)
        by_chapter[chapter] = normalized
    return [by_chapter[ch] for ch in order]
