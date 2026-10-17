"""Chapter status tracking with atomic file writes.

Status flow: PENDING → GENERATING → REVIEWED_PASS | REVIEWED_FAIL → COMMITTED
Terminal states: COMMITTED, HALTED.

Single source of truth: runtime/chapter_status.json
Structure: {"chapters": {"1": "COMMITTED", "2": "HALTED", ...}}

All writes use tmp-file + os.replace for atomicity; read-merge-update-write
preserves other chapters' entries when updating one.
"""
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

STATUS_FILENAME = "runtime/chapter_status.json"
HALT_REASON_FILENAME = "runtime/HALT_REASON.json"

# Status constants
PENDING = "PENDING"
GENERATING = "GENERATING"
REVIEWED_PASS = "REVIEWED_PASS"
REVIEWED_FAIL = "REVIEWED_FAIL"
COMMITTED = "COMMITTED"
HALTED = "HALTED"


def _status_path(root: Path) -> Path:
    return root / STATUS_FILENAME


def _halt_path(root: Path) -> Path:
    return root / HALT_REASON_FILENAME


def _read_status_map(root: Path) -> dict:
    """Load current status map; supports legacy (string values) and new (dict) formats.

    Legacy: {"chapters": {"1": "COMMITTED", "2_extra": "{\"score\":88.4}"}}
    New:    {"chapters": {"1": {"status": "COMMITTED"}, "2": {"status": "COMMITTED", "score": 88.4}}}
    Normalizes both to new dict format, merging _extra keys into parent entries.
    """
    path = _status_path(root)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        chapters = data.get("chapters")
        if not isinstance(chapters, dict):
            return {}
        normalized: dict[str, dict] = {}
        for k, v in chapters.items():
            if k.endswith("_extra"):
                continue
            if isinstance(v, str):
                normalized[k] = {"status": v}
            elif isinstance(v, dict):
                normalized[k] = v
            else:
                normalized[k] = {"status": str(v)}
        for k, v in chapters.items():
            if k.endswith("_extra"):
                base = k[: -len("_extra")]
                try:
                    extras = json.loads(v)
                    if base in normalized:
                        normalized[base].update(extras)
                except (json.JSONDecodeError, TypeError):
                    pass
        return normalized
    except (OSError, json.JSONDecodeError, TypeError):
        return {}


def _write_status_map(root: Path, chapter_map: dict) -> None:
    """Atomically write status map via tmp + os.replace."""
    path = _status_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(
            json.dumps({"chapters": chapter_map}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(path)
    except Exception as e:
        logger.error(f"Failed to atomically write chapter status: {e}")
        try:
            path.write_text(
                json.dumps({"chapters": chapter_map}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass


def get_status(root: Path, chapter: int) -> Optional[str]:
    """Return status string for chapter, or None if not recorded."""
    chapter_map = _read_status_map(root)
    entry = chapter_map.get(str(chapter))
    if entry is None:
        return None
    if isinstance(entry, str):
        return entry
    return entry.get("status")


def set_status(root: Path, chapter: int, status: str,
               **extra) -> None:
    """Update status for one chapter atomically, preserving others.

    Writes in new dict format; merges extra fields (score, reason, etc.)
    into the chapter's entry dict.
    """
    chapter_map = _read_status_map(root)
    key = str(chapter)
    if key in chapter_map and isinstance(chapter_map[key], dict):
        chapter_map[key]["status"] = status
        chapter_map[key].update(extra)
    else:
        chapter_map[key] = {"status": status, **extra}
    _write_status_map(root, chapter_map)


def last_committed(root: Path) -> Optional[int]:
    """Return highest COMMITTED chapter number, or None."""
    chapter_map = _read_status_map(root)
    committed = []
    for k, v in chapter_map.items():
        status = v if isinstance(v, str) else v.get("status", "")
        if status == COMMITTED:
            try:
                committed.append(int(k))
            except ValueError:
                pass
    return max(committed) if committed else None


def write_halt_reason(root: Path, failed_chapter: int, reason: str,
                      detail: str = "") -> None:
    """Write HALT_REASON.json with timestamp."""
    path = _halt_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    entry = {
        "failed_chapter": failed_chapter,
        "reason": reason,
        "last_committed": last_committed(root),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "detail": detail,
    }
    try:
        tmp.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            path.write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def load_halt_reason(root: Path) -> Optional[dict]:
    """Load HALT_REASON.json if present."""
    path = _halt_path(root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_halt_reason(root: Path) -> None:
    """Remove HALT_REASON.json."""
    path = _halt_path(root)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
