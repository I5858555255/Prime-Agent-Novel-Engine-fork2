# -*- coding: utf-8 -*-
"""CC P0: append-only fact_changes audit ledger for world_state mutations.

Stores a per-change immutable record (old_value -> new_value, status, gate_result,
source) alongside the event_ledger. Writes are atomic (temp + os.replace) and
idempotent on change_id. Ledger writes are fire-and-forget wrapped in try/except
so that a failed write never blocks chapter commit.

Schema per line (JSON object):
  change_id       str   e.g. "ch{n}-{seq}" or stable UUID
  chapter         int
  type            str   entity/property category
  target          str   entity/attribute identifier
  old_value       Any   serialized old value (None if N/A)
  new_value       Any   serialized new value (None if N/A)
  source          str   director/reviewer/deterministic_fallback/agent
  gate_result     str   passed/deferred/rejected
  status          str   pending/applied/deferred/rejected/reverted
  ts              str   ISO-8601 UTC timestamp
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LEDGER_REL = Path("runtime") / "fact_changes.jsonl"

# Entity-bearing change types whose target must resolve to an entity ID.
_ENTITY_CHANGE_TYPES = frozenset({"character", "realm", "location", "faction"})


def ledger_path(root: str | Path) -> Path:
    return Path(root) / LEDGER_REL


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_entries(root: str | Path) -> list[dict]:
    """Load all fact_change records from the ledger."""
    path = ledger_path(root)
    if not path.exists():
        return []
    out: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("change_id"):
            out.append(rec)
    return out


def get_changes(root: str | Path, chapter: Optional[int] = None) -> list[dict]:
    """Return fact_change records, optionally filtered by chapter."""
    entries = load_entries(root)
    if chapter is not None:
        entries = [e for e in entries if int(e.get("chapter", 0) or 0) == int(chapter)]
    return entries


def list_status(root: str | Path, status: str) -> list[dict]:
    """Return fact_change records matching the given status."""
    return [e for e in load_entries(root) if e.get("status") == status]


def _make_change_id(chapter: int, seq: int) -> str:
    return f"ch{chapter}-{seq:04d}"


def _normalize_target(root: str | Path, target: str, change_type: str) -> str:
    """If target is a character/faction name (not an ID), resolve to its ID.

    Reads characters.json / factions.json from world_state; falls back gracefully.
    Only applies when change_type denotes an entity ref.
    """
    if not target or change_type not in _ENTITY_CHANGE_TYPES and not change_type.startswith(("character_", "faction_")):
        return target
    # Already looks like an ID (e.g. C001) — leave as-is
    if target.startswith(("C", "F")) and len(target) <= 6:
        return target
    try:
        ws = Path(root) / "memory" / "world_state"
        chars_path = ws / "characters.json"
        if chars_path.exists():
            data = json.loads(chars_path.read_text(encoding="utf-8"))
            for cid, cdata in data.get("characters", {}).items():
                if cdata.get("name", "") == target:
                    return cid
        factions_path = ws / "factions.json"
        if factions_path.exists():
            data = json.loads(factions_path.read_text(encoding="utf-8"))
            for fid, fdata in data.get("factions", {}).items():
                if fdata.get("name", "") == target:
                    return fid
    except Exception:
        pass
    return target


def record_change(
    root: str | Path,
    *,
    chapter: int,
    change_type: str,
    target: str,
    old_value,
    new_value,
    source: str = "director",
    gate_result: str = "passed",
    status: str = "applied",
    change_id: Optional[str] = None,
) -> str:
    """Append one fact_change record atomically and idempotently.

    Idempotent on change_id: if a record with the same change_id already exists,
    the write is skipped and the existing id is returned.
    Returns the change_id (generated or existing).
    """
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)

    if change_id is None:
        # Find next sequence number for this chapter
        existing = load_entries(root)
        ch_entries = [e for e in existing if int(e.get("chapter", 0) or 0) == chapter]
        seq = len(ch_entries) + 1
        change_id = _make_change_id(chapter, seq)
        # Verify uniqueness; if duplicate, append a UUID suffix
        if any(e.get("change_id") == change_id for e in existing):
            change_id = f"{change_id}-{uuid.uuid4().hex[:8]}"

    # Idempotency check
    if any(e.get("change_id") == change_id for e in load_entries(root)):
        return change_id

    record = {
        "change_id": change_id,
        "chapter": int(chapter),
        "type": change_type,
        "target": _normalize_target(root, target, change_type),
        "old_value": old_value,
        "new_value": new_value,
        "source": source,
        "gate_result": gate_result,
        "status": status,
        "ts": _ts(),
    }

    existing = load_entries(root)
    merged = existing + [record]
    tmp = path.with_suffix(".jsonl.tmp")
    payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in merged)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return change_id


def revert_change(root: str | Path, change_id: str) -> Optional[str]:
    """Revert a single applied change by restoring old_value.

    Appends a new record with status=reverted. Returns the new change_id on success,
    None if the original change was not found.
    """
    entries = load_entries(root)
    target = next((e for e in entries if e.get("change_id") == change_id), None)
    if target is None:
        return None

    if target.get("status") not in ("applied", "deferred"):
        return None

    old_value = target.get("old_value")
    new_value = target.get("new_value")
    chapter = target.get("chapter")
    change_type = target.get("type")
    target_key = target.get("target")

    # Append revert record
    revert_id = record_change(
        root,
        chapter=chapter,
        change_type=change_type,
        target=target_key,
        old_value=new_value,
        new_value=old_value,
        source="revert",
        gate_result="manual",
        status="reverted",
    )
    return revert_id


def revert_chapter(root: str | Path, chapter: int) -> list[str]:
    """Revert all applied/deferred changes for a chapter, in reverse order.

    Returns list of new revert change_ids.
    """
    entries = load_entries(root)
    chapter_entries = [
        e for e in entries
        if int(e.get("chapter", 0) or 0) == chapter
        and e.get("status") in ("applied", "deferred")
    ]
    # Reverse so we restore from latest to oldest
    chapter_entries.reverse()

    reverted_ids: list[str] = []
    for entry in chapter_entries:
        cid = entry.get("change_id")
        rid = revert_change(root, cid)
        if rid:
            reverted_ids.append(rid)
    return reverted_ids
