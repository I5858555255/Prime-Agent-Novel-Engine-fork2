#!/usr/bin/env python3
"""Memory health check: read-only scan of StateDB, world_state, foreshadow registry, fact_changes.

Detects three classes of hard problems:
  1. Same-entity contradictory properties across StateDB and world_state JSON.
  2. Foreshadows past their planned resolve_chapter still in non-terminal status.
  3. References (in relationships or fact_changes) pointing to non-existent entities.

Also merges audit_trend trend metrics into a dedicated section.

Exit codes:
  0 — no hard problems detected
  1 — one or more hard problems detected
  2 — script usage / argument error
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Allow running this file directly (python memory_health.py) from any cwd:
# ensure the package root (novel_engine) is importable.
_SRC_ROOT = str(Path(__file__).resolve().parents[2])
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from novel_engine.scripts.audit_trend import build_trend  # noqa: E402

# ── Paths (relative to project root) ─────────────────────────────────────────

# DATA_DIR: real data lives in the novel_engine package dir (runtime/, memory/,
# config/, audit/). Scripts run from any cwd; run() stays root-relative so
# tests can inject tmp_path. main() defaults root to DATA_DIR when --root absent.
_DATA_DIR = Path(__file__).resolve().parent.parent
DB_REL = Path("runtime") / "state.db"
WORLD_STATE_DIR = Path("memory") / "world_state"
FORESHADOW_REGISTRY_REL = Path("config") / "foreshadow" / "registry.json"
FACT_CHANGES_REL = Path("runtime") / "fact_changes.jsonl"
LAST_CHAPTER_FILE = Path("runtime") / "last_success_chapter.txt"
DATA_DIR = _DATA_DIR

# Fields compared between StateDB characters and world_state/characters.json.
_CHAR_FIELDS = ("name", "realm", "location", "description")

# Fact-change types whose "target" field is an entity ID that must exist.
_ENTITY_CHANGE_TYPES = frozenset({"character", "realm", "location", "faction"})

# Foreshadow statuses that mean the item is closed (not overdue).
_FORESHADOW_TERMINAL = frozenset({"resolved", "completed", "abandoned"})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_json(path: Path) -> object:
    """Load JSON; also unwrap common wrapper keys ('reviews', 'chapters') into a list."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict):
        for key in ("reviews", "chapters"):
            val = raw.get(key)
            if isinstance(val, list):
                return val
    return raw

def _load_fact_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("change_id"):
            out.append(rec)
    return out

def _open_db(db_path: Path) -> sqlite3.Connection:
    """Open StateDB in read-only mode so the script never mutates data."""
    uri = db_path.as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _get_current_chapter(root: Path) -> int:
    """Latest successfully committed chapter, or 0 if unavailable."""
    p = root / LAST_CHAPTER_FILE
    if p.exists():
        try:
            val = int(p.read_text(encoding="utf-8").strip())
            if val > 0:
                return val
        except (ValueError, OSError):
            pass
    return 0


# ── Problem 1: Contradictory properties ───────────────────────────────────────

def _check_contradictory_properties(db: sqlite3.Connection, char_json: object) -> list[str]:
    """Compare StateDB characters against world_state/characters.json.

    Rule: for every character id present in both sources, each shared non-null
    field among name/realm/location/description must agree (string equality).
    """
    problems: list[str] = []
    if not isinstance(char_json, dict):
        return problems

    db_cur = db.cursor()
    db_cur.execute("SELECT id, name, realm, location, description FROM characters")
    db_chars: dict[str, dict] = {row["id"]: dict(row) for row in db_cur.fetchall()}

    json_chars = char_json.get("characters")
    if not isinstance(json_chars, dict):
        return problems

    for cid, jdata in json_chars.items():
        if not isinstance(jdata, dict):
            continue
        dbc = db_chars.get(cid)
        if dbc is None:
            continue
        for field in _CHAR_FIELDS:
            jval = jdata.get(field)
            dval = dbc.get(field)
            if jval is None or dval is None:
                continue
            if str(jval).strip() != str(dval).strip():
                problems.append(
                    f"  [CONTRADICTION] character={cid} field={field} "
                    f"state_db={dval!r} world_state={jval!r}"
                )
    return problems


# ── Problem 2: Expired foreshadows ───────────────────────────────────────────

def _check_expired_foreshadows(
    db: sqlite3.Connection, registry: object, current_chapter: int
) -> list[str]:
    """Foreshadows whose resolve_chapter < current_chapter and status is not terminal.

    Sources merged: registry.json entries and StateDB foreshadows table
    (runtime-planted foreshadows absent from the registry are included).
    """
    problems: list[str] = []
    if current_chapter <= 0:
        return problems

    reg_list: list[dict] = []
    if isinstance(registry, dict):
        raw = registry.get("foreshadows")
        if isinstance(raw, list):
            reg_list = [fs for fs in raw if isinstance(fs, dict)]

    seen: set[str] = set()
    for fs in reg_list:
        fs_id = str(fs.get("id", "")).strip()
        if not fs_id or fs_id in seen:
            continue
        seen.add(fs_id)
        resolve_ch = fs.get("resolve_chapter")
        status = str(fs.get("status", "")).strip().lower()
        if not isinstance(resolve_ch, int) or resolve_ch <= 0:
            continue
        if resolve_ch < current_chapter and status not in _FORESHADOW_TERMINAL:
            problems.append(
                f"  [EXPIRED_FORESHADOW] id={fs_id} resolve_chapter={resolve_ch} "
                f"status={status!r} (current_chapter={current_chapter})"
            )

    db_cur = db.cursor()
    db_cur.execute("SELECT id, resolve_chapter, status FROM foreshadows")
    for row in db_cur.fetchall():
        fs_id = row["id"]
        if not fs_id or fs_id in seen:
            continue
        resolve_ch = row["resolve_chapter"]
        status = str(row["status"] or "").strip().lower()
        if not isinstance(resolve_ch, int) or resolve_ch <= 0:
            continue
        if resolve_ch < current_chapter and status not in _FORESHADOW_TERMINAL:
            problems.append(
                f"  [EXPIRED_FORESHADOW] id={fs_id} resolve_chapter={resolve_ch} "
                f"status={status!r} (runtime-only, not in registry; current_chapter={current_chapter})"
            )
    return problems


# ── Problem 3: Dangling references ───────────────────────────────────────────

def _check_dangling_references(db: sqlite3.Connection, fact_entries: list[dict]) -> list[str]:
    """References to entity IDs that do not exist in characters/factions.

    Checks:
      - relationships.char_from / char_to must be character ids.
      - fact_changes entries with an entity-bearing type whose target must exist
        in characters or factions.
    """
    problems: list[str] = []
    db_cur = db.cursor()
    db_cur.execute("SELECT id, name FROM characters")
    known_chars: set[str] = set()
    for row in db_cur.fetchall():
        known_chars.add(row["id"])
        if row["name"]:
            known_chars.add(row["name"])
    db_cur.execute("SELECT id, name FROM factions")
    known_factions: set[str] = set()
    for row in db_cur.fetchall():
        known_factions.add(row["id"])
        if row["name"]:
            known_factions.add(row["name"])

    db_cur.execute("SELECT char_from, char_to, relation_type FROM relationships")
    for row in db_cur.fetchall():
        r = dict(row)
        for side in ("char_from", "char_to"):
            val = str(r.get(side, "")).strip()
            if val and val not in known_chars:
                problems.append(
                    f"  [DANGLING_REF] relationships {side}={val!r} "
                    f"not in characters table (relation={r.get('relation_type')!r})"
                )

    for entry in fact_entries:
        target = str(entry.get("target", "")).strip()
        change_type = str(entry.get("type", "")).strip()
        if not target:
            continue
        is_entity_ref = (
            change_type in _ENTITY_CHANGE_TYPES
            or change_type.startswith("character_")
            or change_type.startswith("faction_")
        )
        if is_entity_ref and target not in known_chars and target not in known_factions:
            chapter = entry.get("chapter")
            ch_label = f"ch{chapter}" if chapter else "unknown_ch"
            problems.append(
                f"  [DANGLING_REF] fact_changes [{ch_label}] type={change_type!r} "
                f"target={target!r} not in characters or factions"
            )
    return problems


# ── Main ──────────────────────────────────────────────────────────────────────

def run(root: Path) -> tuple[str, int]:
    """Run all health checks. Returns (report_text, exit_code). Read-only."""
    root = Path(root)
    lines: list[str] = []
    hard_count = 0

    def report(section: str, problems: list[str]) -> None:
        nonlocal hard_count
        if not problems:
            lines.append(f"## {section}: CLEAN")
            return
        lines.append(f"## {section}: {len(problems)} PROBLEM(S)")
        lines.extend(problems)
        hard_count += len(problems)

    # --- Load sources -------------------------------------------------------
    db_path = root / DB_REL
    char_json = _load_json(root / WORLD_STATE_DIR / "characters.json")
    registry = _load_json(root / FORESHADOW_REGISTRY_REL)
    fact_entries = _load_fact_entries(root / FACT_CHANGES_REL)
    current_chapter = _get_current_chapter(root)

    # --- Check 1: Contradictory properties ---------------------------------
    if db_path.exists():
        db = _open_db(db_path)
        try:
            report(
                "1. Contradictory Properties (StateDB vs world_state)",
                _check_contradictory_properties(db, char_json),
            )
            # --- Check 2: Expired foreshadows -------------------------------
            report(
                "2. Expired Foreshadows (resolve_chapter passed, not terminal)",
                _check_expired_foreshadows(db, registry, current_chapter),
            )
            # --- Check 3: Dangling references --------------------------------
            report(
                "3. Dangling References (target not in characters/factions)",
                _check_dangling_references(db, fact_entries),
            )
        finally:
            db.close()
    else:
        gaps = []
        if not (root / WORLD_STATE_DIR / "characters.json").exists():
            gaps.append("characters.json missing")
        lines.append(f"## 1-3: SKIPPED (state.db not found at {DB_REL}; {', '.join(gaps)})")

    # --- Audit trend metrics ------------------------------------------------
    try:
        trend = build_trend(root)
        lines.append("")
        lines.append("## 4. Audit Trend Metrics")
        lines.append(f"  Chapters reviewed: {trend.get('total_chapters_reviewed', 0)}")
        lines.append(f"  Chapters with issues: {trend.get('chapters_with_issues', 0)}")
        lines.append(f"  Total issues recorded: {trend.get('total_issues_recorded', 0)}")
        lines.append(f"  Forced drafts: {trend.get('forced_draft_count', 0)}")
        lines.append(f"  Recurring chapters: {trend.get('recurring_chapters_count', 0)}")
        unique_kinds = trend.get("unique_issue_kinds", [])
        if unique_kinds:
            lines.append(f"  Issue kinds: {', '.join(unique_kinds[:20])}")
        sample = trend.get("per_chapter_sample", {})
        if sample:
            lines.append("  Per-chapter sample (first 10):")
            for ch, info in sorted(sample.items(), key=lambda kv: int(kv[0] or 0))[:10]:
                lines.append(
                    f"    ch{ch}: issues={info.get('total_issues', 0)} "
                    f"avg_score={info.get('avg_score')} "
                    f"forced={info.get('forced_draft', False)}"
                )
    except Exception as exc:  # noqa: BLE001 - trend section must never break the check
        lines.append(f"## 4. Audit Trend Metrics: ERROR ({exc})")

    # --- Footer --------------------------------------------------------------
    lines.append("=" * 60)
    if hard_count == 0:
        lines.append("RESULT: CLEAN - no hard problems detected")
    else:
        lines.append(f"RESULT: {hard_count} HARD PROBLEM(S) DETECTED")
    lines.append(f"Current chapter: {current_chapter}")

    return "\n".join(lines), (1 if hard_count > 0 else 0)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Memory health check for novel-engine (read-only)"
    )
    parser.add_argument("--root", default=None, help="Project root (default: DATA_DIR)")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Return exit code without printing the report",
    )
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else DATA_DIR
    report_text, code = run(root)
    if not args.dry_run:
        print(report_text)
    return code
if __name__ == "__main__":
    sys.exit(main())
