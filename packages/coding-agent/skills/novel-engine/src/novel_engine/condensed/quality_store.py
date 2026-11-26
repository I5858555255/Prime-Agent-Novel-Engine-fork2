"""Versioned, hash-chained quality verdict leaves (Fork 3a).

The verdict is a leaf off a chapter, not a mutation of it: a new
`(chapter, version)` row chained onto `chapters.hash`. `prose_sha256` binds
each leaf to the exact text it scored; re-measure is an explicit batched
`re_verify` that writes new versions, never a silent re-score (NG6).

The module receives the `Store.db` handle (no separate connection) and is
gated by `enable_quality` in quality_config.json: when the layer is disabled,
the quality tables do not exist and the store functions raise RuntimeError.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from .contracts import digest, dumps


def new_trace_id() -> str:
    """One opaque UUID per chapter life (condensed row -> expansion calls -> verdict)."""
    return uuid.uuid4().hex


def _quality_enabled(db) -> bool:
    row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='quality_verdicts'").fetchone()
    return row is not None


def _require_quality(db) -> None:
    if not _quality_enabled(db):
        raise RuntimeError("quality layer not enabled")


def _latest_row(db, chapter):
    row = db.execute(
        "SELECT version, verdict, knob_set_hash, judge_model, prompt_ver, prose_sha256, verdict_error, "
        "quality_verdict_hash, parent_hash, trace_id, accept_check_version "
        "FROM quality_verdicts WHERE chapter=? ORDER BY version DESC LIMIT 1",
        (chapter,)).fetchone()
    if not row:
        return None
    return {"version": row[0], "verdict": json.loads(row[1]), "knob_set_hash": row[2],
            "judge_model": row[3], "prompt_ver": row[4], "prose_sha256": row[5],
            "verdict_error": json.loads(row[6]) if row[6] else None,
            "quality_verdict_hash": row[7], "parent_hash": row[8], "trace_id": row[9],
            "accept_check_version": row[10]}


def _stored_hash(leaf, prose_sha256) -> str:
    """Recompute quality_verdict_hash the way record_verdict stored it.

    The hash binds the verdict record + prose identity (or, for a verdict_error
    leaf, the error itself instead of the prose). prose_sha256 is a hash input,
    never a field inside the verdict JSON.
    """
    verdict = dict(leaf["verdict"])
    if leaf.get("verdict_error") is not None:
        hash_input = {"verdict": verdict, "verdict_error": leaf["verdict_error"]}
    else:
        hash_input = {"verdict": verdict, "prose_sha256": prose_sha256}
    return digest(hash_input)


def record_verdict(db, store, chapter, verdict, prose_sha256, trace_id=None,
                   knob_set_hash=None, judge_model=None, prompt_ver=None) -> str:
    """Record a verdict leaf for a chapter; return its quality_verdict_hash.

    Inserts version=1 when the chapter has no leaf yet. Idempotent on the
    latest version: when the latest leaf already carries the same verdict
    content (same verdict + same prose binding), its existing hash is returned
    without writing a duplicate. When the content differs from the latest leaf
    (e.g. a re-judge after prose re-expansion) a new version is appended — the
    chain is additive, never re-minted. prose_sha256 is NULL on a verdict_error
    leaf and excluded from the hash in that case.

    `judge_model`/`prompt_ver`/`knob_set_hash` default to the values carried by
    the verdict itself (a verdict record carries its judge metadata), falling
    back to "stub"/"v1"/"" only when the verdict omits them — so the leaf's
    judge_model reflects the model that actually produced the verdict.
    """
    _require_quality(db)
    if trace_id is None:
        trace_id = new_trace_id()
    verdict = dict(verdict)
    verdict_error = verdict.get("verdict_error")
    bound_prose = None if verdict_error is not None else prose_sha256
    if judge_model is None:
        judge_model = verdict.get("judge_model", "stub")
    if prompt_ver is None:
        prompt_ver = verdict.get("prompt_ver", "v1")
    if knob_set_hash is None:
        knob_set_hash = verdict.get("knob_set_hash", "")
    chapter_row = store.chapter(chapter)
    parent_hash = chapter_row["hash"] if chapter_row else "GENESIS"
    latest = _latest_row(db, chapter)
    with db:
        if latest is not None and latest["verdict"] == verdict and latest["verdict_error"] == verdict_error \
                and ((latest["prose_sha256"] or None) == (bound_prose or None)):
            return latest["quality_verdict_hash"]
        version = (latest["version"] + 1) if latest is not None else 1
        leaf = {"version": version, "verdict": verdict, "knob_set_hash": knob_set_hash,
                "judge_model": judge_model, "prompt_ver": prompt_ver,
                "prose_sha256": bound_prose, "verdict_error": verdict_error,
                "quality_verdict_hash": "", "parent_hash": parent_hash, "trace_id": trace_id,
                "accept_check_version": 1}
        leaf["quality_verdict_hash"] = _stored_hash(leaf, bound_prose)
        db.execute(
            "INSERT INTO quality_verdicts(chapter,version,verdict,knob_set_hash,judge_model,prompt_ver,"
            "prose_sha256,verdict_error,quality_verdict_hash,parent_hash,trace_id,accept_check_version) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (chapter, version, dumps(verdict), knob_set_hash, judge_model, prompt_ver,
             bound_prose, dumps(verdict_error) if verdict_error is not None else None,
             leaf["quality_verdict_hash"], parent_hash, trace_id, 1))
    return leaf["quality_verdict_hash"]


def verdict_for(db, store, chapter):
    """Latest verdict leaf for a chapter, or None when the chapter has no leaf.

    Verifies the stored quality_verdict_hash against the recomputed digest and
    the parent_hash against the chapter's current hash; any mismatch (a
    re-committed/invalidated chapter or a tampered leaf) is a ValueError.
    """
    _require_quality(db)
    leaf = _latest_row(db, chapter)
    if leaf is None:
        return None
    if leaf["quality_verdict_hash"] != _stored_hash(leaf, leaf["prose_sha256"]):
        raise ValueError(f"quality_verdict_hash mismatch for chapter {chapter}")
    chapter_row = store.chapter(chapter)
    if leaf["parent_hash"] != (chapter_row["hash"] if chapter_row else "GENESIS"):
        raise ValueError(f"parent_hash mismatch for chapter {chapter}")
    out = dict(leaf)
    out["chapter"] = chapter
    return out


def re_verify(db, store, chapters, new_model):
    """Explicit batched re-measure (NG6): write version=N+1 leaves only for the
    chapters whose latest leaf was judged by a different model.

    `chapters` is an iterable of chapter numbers. The caller (M2 repair loop /
    model-upgrade job) is responsible for producing the re-scored verdict; this
    v0 helper materializes the version bump when `new_model` differs from the
    leaf's judge_model, re-binding the same verdict content under the new model
    (idempotent no-op when the leaf already carries new_model). Returns the list
    of (chapter, new_version) pairs written.
    """
    _require_quality(db)
    written = []
    for chapter in chapters:
        latest = _latest_row(db, chapter)
        if latest is None or latest["judge_model"] == new_model:
            continue
        next_version = latest["version"] + 1
        with db:
            db.execute(
                "INSERT INTO quality_verdicts(chapter,version,verdict,knob_set_hash,judge_model,prompt_ver,"
                "prose_sha256,verdict_error,quality_verdict_hash,parent_hash,trace_id,accept_check_version) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (chapter, next_version, dumps(latest["verdict"]), latest["knob_set_hash"], new_model,
                 latest["prompt_ver"], latest["prose_sha256"],
                 dumps(latest["verdict_error"]) if latest["verdict_error"] is not None else None,
                 _stored_hash({**latest, "version": next_version}, latest["prose_sha256"]),
                 latest["parent_hash"], latest["trace_id"], latest["accept_check_version"]))
        written.append((chapter, next_version))
    return written


def validate_quality_chain(db, store) -> list[dict]:
    """Recompute every leaf hash; [] means clean.

    Raises ValueError on any quality_verdict_hash / parent_hash / prose_sha256
    mismatch. Called by `runner.verify`; returns per-leaf problems (empty list
    when the whole chain validates).
    """
    _require_quality(db)
    problems = []
    for (chapter,) in db.execute("SELECT DISTINCT chapter FROM quality_verdicts ORDER BY chapter"):
        for row in db.execute(
                "SELECT version, verdict, prose_sha256, verdict_error, quality_verdict_hash, parent_hash "
                "FROM quality_verdicts WHERE chapter=? ORDER BY version", (chapter,)):
            leaf = {"version": row[0], "verdict": json.loads(row[1]), "prose_sha256": row[2],
                    "verdict_error": json.loads(row[3]) if row[3] else None,
                    "quality_verdict_hash": row[4], "parent_hash": row[5]}
            if leaf["quality_verdict_hash"] != _stored_hash(leaf, leaf["prose_sha256"]):
                problems.append({"chapter": chapter, "version": row[0], "problem": "quality_verdict_hash mismatch"})
                continue
            chapter_row = store.chapter(chapter)
            if leaf["parent_hash"] != (chapter_row["hash"] if chapter_row else "GENESIS"):
                problems.append({"chapter": chapter, "version": row[0], "problem": "parent_hash mismatch"})
    if any(p["problem"] == "quality_verdict_hash mismatch" for p in problems):
        raise ValueError(f"quality chain integrity failure: {problems}")
    return problems


def budget_status(db, cap) -> dict:
    """Monthly token budget readout against cap: SUM(usage.total_tokens) over
    the current calendar month (by the YYYY-MM date prefix of captured_at) plus
    all legacy rows whose captured_at is NULL. NULL captured_at rows are
    legacy and always count.
    """
    now = datetime.now(timezone.utc)
    month_prefix = now.strftime("%Y-%m")
    row = db.execute(
        "SELECT SUM(CASE WHEN captured_at IS NOT NULL AND substr(captured_at,1,7) = ? THEN total_tokens END), "
        "SUM(CASE WHEN captured_at IS NULL THEN total_tokens END) FROM usage",
        (month_prefix,)).fetchone()
    if row[0] is not None or row[1] is not None:
        monthly, legacy = row[0] or 0, row[1] or 0
    else:
        total = db.execute("SELECT COALESCE(SUM(total_tokens),0) FROM usage").fetchone()[0]
        monthly, legacy = total, 0
    used = int(monthly) + int(legacy)
    pct = round(100.0 * used / cap) if cap else None
    return {"used": used, "cap": cap, "pct": pct, "legacy_included": bool(legacy), "cap_reached": bool(cap and used >= cap)}


def record_override(db, chapter, dim, new_value, rationale=""):
    """Write a knob_overrides staging row (un-chained; the M2 commit_override
    verb folds it into the chain)."""
    _require_quality(db)
    override_hash = digest({"chapter": chapter, "dim": dim, "new_value": new_value, "rationale": rationale})
    with db:
        db.execute(
            "INSERT OR REPLACE INTO knob_overrides(chapter,dim,new_value,rationale,override_hash,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (chapter, dim, new_value, rationale or None, override_hash,
             datetime.now(timezone.utc).isoformat()))
    return override_hash


KNOB_DIMS = ("爽点", "钩子", "压抑")


def _require_knob_dim(dim) -> None:
    if dim not in KNOB_DIMS:
        raise ValueError(f"invalid knob dim {dim!r}; must be one of {list(KNOB_DIMS)}")


def _require_knob_value(new_value) -> None:
    if not isinstance(new_value, int) or isinstance(new_value, bool) or not (1 <= new_value <= 5):
        raise ValueError(f"new_value must be an int 1..5, got {new_value!r}")


def commit_override(db, store, chapter, dim, new_value, rationale="") -> str:
    """Fold a record_override staging row into the chain as a knob_commits leaf.

    - Validates dim in KNOB_DIMS and 1<=new_value<=5 (the v0-audit security
      note: unvalidated operator text must not reach the chain).
    - Requires the (chapter, dim) staging row to exist (record first); else
      ValueError.
    - Writes a knob_commits leaf whose parent_hash = chapters.hash for that
      chapter (GENESIS when the chapter row is missing) and
      knob_commit_hash = digest({chapter, dim, new_value, rationale,
      override_hash, parent_hash, trace_id}).
    - Marks the staging row's committed_at (consumption, no re-fold).
    - Idempotent on (chapter, dim, override_hash): if that leaf is already
      committed, the existing knob_commit_hash is returned and no second row
      is written.
    Returns the knob_commit_hash.
    """
    _require_quality(db)
    _require_knob_dim(dim)
    _require_knob_value(new_value)
    staging = db.execute(
        "SELECT override_hash FROM knob_overrides WHERE chapter=? AND dim=?",
        (chapter, dim)).fetchone()
    if staging is None:
        raise ValueError(f"no knob_overrides staging row for chapter {chapter} dim {dim!r}; run record_override first")
    override_hash = staging[0]
    trace_id = new_trace_id()
    # Idempotent on the (chapter, dim, override_hash) leaf: re-folding the same
    # committed override returns the existing hash, never a duplicate row.
    existing = db.execute(
        "SELECT knob_commit_hash FROM knob_commits WHERE chapter=? AND dim=? AND override_hash=?",
        (chapter, dim, override_hash)).fetchone()
    if existing is not None:
        return existing[0]
    chapter_row = store.chapter(chapter)
    parent_hash = chapter_row["hash"] if chapter_row else "GENESIS"
    knob_commit_hash = digest({
        "chapter": chapter, "dim": dim, "new_value": new_value,
        "rationale": rationale, "override_hash": override_hash,
        "parent_hash": parent_hash, "trace_id": trace_id})
    now = datetime.now(timezone.utc).isoformat()
    with db:
        db.execute(
            "INSERT INTO knob_commits(chapter,dim,new_value,rationale,override_hash,"
            "knob_commit_hash,parent_hash,trace_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (chapter, dim, new_value, rationale or None, override_hash,
             knob_commit_hash, parent_hash, trace_id, now))
        db.execute(
            "UPDATE knob_overrides SET committed_at=? WHERE chapter=? AND dim=?",
            (now, chapter, dim))
    return knob_commit_hash


def knob_commits_for(db, chapter) -> list[dict]:
    """Read-only: every folded knob override leaf for `chapter`, newest first."""
    _require_quality(db)
    rows = db.execute(
        "SELECT id, dim, new_value, rationale, override_hash, knob_commit_hash, "
        "parent_hash, trace_id, created_at FROM knob_commits WHERE chapter=? ORDER BY id DESC",
        (chapter,)).fetchall()
    return [{"id": r[0], "dim": r[1], "new_value": r[2], "rationale": r[3],
             "override_hash": r[4], "knob_commit_hash": r[5], "parent_hash": r[6],
             "trace_id": r[7], "created_at": r[8]} for r in rows]


def validate_knob_commits(db, store) -> list[dict]:
    """Recompute every knob_commits leaf hash + parent link; [] = clean.

    Raises ValueError on any knob_commit_hash / parent_hash mismatch (a tampered
    leaf). The leaf hash is recomputed exactly the way commit_override stored it
    (digest over the 7 contract fields, trace_id read back from the row)."""
    _require_quality(db)
    problems = []
    for row in db.execute(
            "SELECT id, chapter, dim, new_value, rationale, override_hash, "
            "knob_commit_hash, parent_hash, trace_id FROM knob_commits ORDER BY chapter, id"):
        (leaf_id, chapter, dim, new_value, rationale, override_hash,
         stored_hash, parent_hash, trace_id) = row
        expected = digest({"chapter": chapter, "dim": dim, "new_value": new_value,
                           "rationale": rationale, "override_hash": override_hash,
                           "parent_hash": parent_hash, "trace_id": trace_id})
        if stored_hash != expected:
            problems.append({"chapter": chapter, "id": leaf_id, "problem": "knob_commit_hash mismatch"})
            continue
        chapter_row = store.chapter(chapter)
        if parent_hash != (chapter_row["hash"] if chapter_row else "GENESIS"):
            problems.append({"chapter": chapter, "id": leaf_id, "problem": "parent_hash mismatch"})
    if any(p["problem"] == "knob_commit_hash mismatch" for p in problems):
        raise ValueError(f"knob commit integrity failure: {problems}")
    return problems


# =============================================================================
# M2-10: migration_ledger — a formal, hash-chained schema-migration record.
# =============================================================================
# v0 used a `chapters.schema_version` column + a `quality_schema_version`
# metadata marker row as the migration signal. M2-10 adds a proper, auditable,
# replayable ledger: one row per applied schema migration, chained by
# `ledger_hash` (parent_hash = the previous row's ledger_hash, or GENESIS).
# The v0 marker rows are preserved (dual-signal, back-compat); new
# schema-version upgrades go through the ledger. The ledger is a GLOBAL
# schema-history record (NOT per-chapter), so `Store.invalidate_from` must NOT
# delete it.
# ledger_hash = digest({migration_id, from_version, to_version, applied_at,
#                       description, parent_hash}). An empty description is
# canonicalized to None (stored NULL, hashed as None) so the stored column and
# the hash input always agree. record_migration is idempotent on migration_id.


def _ledger_enabled(db) -> bool:
    row = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='migration_ledger'").fetchone()
    return row is not None


def _require_ledger(db) -> None:
    if not _ledger_enabled(db):
        raise RuntimeError("migration_ledger not available (enable_quality is off)")


def _last_ledger_hash(db) -> str:
    """The current chain head: the row with the highest to_version (migrations
    are version-ordered 0->1->2...), its ledger_hash, or GENESIS when empty."""
    row = db.execute("SELECT ledger_hash FROM migration_ledger ORDER BY to_version DESC LIMIT 1").fetchone()
    return row[0] if row else "GENESIS"


def _ledger_hash(migration_id, from_version, to_version, applied_at, description, parent_hash) -> str:
    return digest({"migration_id": migration_id, "from_version": from_version, "to_version": to_version,
                   "applied_at": applied_at, "description": description, "parent_hash": parent_hash})


def record_migration(db, migration_id, from_version, to_version, description="") -> str:
    """Append one migration_ledger row chained onto the ledger head; return its
    ledger_hash.

    Idempotent on `migration_id`: re-recording an id already present returns
    the existing row's ledger_hash without writing a duplicate. A new id chains
    off the current head (parent_hash = the last row's ledger_hash, or GENESIS).

    The ledger_hash is computed over the EXACT values stored in the row, so it
    self-validates: an empty description is stored as NULL and hashed as None
    (never as ""), keeping the column and the hash input identical.
    """
    _require_ledger(db)
    existing = db.execute("SELECT ledger_hash FROM migration_ledger WHERE migration_id=?",
                          (migration_id,)).fetchone()
    if existing:
        return existing[0]
    applied_at = datetime.now(timezone.utc).isoformat()
    stored_description = description or None  # None == empty, canonical for hash + column
    parent_hash = _last_ledger_hash(db)
    ledger_hash = _ledger_hash(migration_id, from_version, to_version, applied_at,
                               stored_description, parent_hash)
    with db:
        db.execute(
            "INSERT INTO migration_ledger(migration_id,from_version,to_version,applied_at,description,"
            "ledger_hash,parent_hash) VALUES(?,?,?,?,?,?,?)",
            (migration_id, from_version, to_version, applied_at, stored_description,
             ledger_hash, parent_hash))
    return ledger_hash


def migrations(db) -> list[dict]:
    """Read-only: every migration record, ordered by to_version (the chain
    order). Returns a list of dicts with all columns."""
    _require_ledger(db)
    out = []
    for row in db.execute(
            "SELECT migration_id, from_version, to_version, applied_at, description, ledger_hash, parent_hash "
            "FROM migration_ledger ORDER BY to_version"):
        out.append({"migration_id": row[0], "from_version": row[1], "to_version": row[2],
                    "applied_at": row[3], "description": row[4], "ledger_hash": row[5],
                    "parent_hash": row[6]})
    return out


def ledger_head(db) -> int:
    """The current schema version = the latest to_version; 0 when the ledger is
    empty. (A freshly-opened `enable_quality` store auto-backfills the v0
    bootstrap row, so in practice its head starts at 1; 0 is only observed on
    an empty/unmigrated ledger.)"""
    _require_ledger(db)
    row = db.execute("SELECT MAX(to_version) FROM migration_ledger").fetchone()
    return int(row[0]) if row[0] is not None else 0


def validate_migration_ledger(db) -> list[dict]:
    """Recompute every row's ledger_hash + parent link; [] means clean.

    Raises ValueError on any ledger_hash mismatch or a broken parent chain
    (a row whose parent_hash does not equal the previous row's ledger_hash,
    or GENESIS for the first)."""
    _require_ledger(db)
    problems = []
    expected_parent = "GENESIS"
    for row in db.execute(
            "SELECT migration_id, from_version, to_version, applied_at, description, ledger_hash, parent_hash "
            "FROM migration_ledger ORDER BY to_version"):
        migration_id, from_version, to_version, applied_at, description, stored_hash, parent_hash = row
        recomputed = _ledger_hash(migration_id, from_version, to_version, applied_at, description,
                                   expected_parent)
        if stored_hash != recomputed:
            problems.append({"migration_id": migration_id, "problem": "ledger_hash mismatch"})
        if parent_hash != expected_parent:
            problems.append({"migration_id": migration_id, "problem": "parent_hash mismatch"})
        expected_parent = stored_hash
    if any(p["problem"] == "ledger_hash mismatch" for p in problems):
        raise ValueError(f"migration_ledger integrity failure: {problems}")
    if any(p["problem"] == "parent_hash mismatch" for p in problems):
        raise ValueError(f"migration_ledger parent-chain failure: {problems}")
    return problems
