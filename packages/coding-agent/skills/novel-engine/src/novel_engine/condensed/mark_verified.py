"""mark-verified: record a successful external expansion in the package manifest.

Given an expansion package directory and the programmatic accept report JSON
(optionally plus the LLM acceptance report), rewrites manifest.json with:

    external_expansion_verified = True
    verification = {
        program_report_sha256, program_verdict, program_han,
        llm_report_sha256, waiver, verdict_record_hash (when a store is given),
        marked_at,
    }

Refuses to mark unless the program report verdict is PASS. When a `store` is
provided, the verdict leaf is verified via `quality_store.verdict_for` and a
`verdict_record_hash` is stamped; refusal happens ONLY on an integrity
failure (a tampered leaf), never on a quality_notes-only report.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .contracts import dumps
from . import quality_store


# Block classes that may be marked verified with a documented waiver (the
# content itself is acceptable; only a stage-forbidden term or an infant
# self-analysis phrase slipped through). Word-count / metadata / beat-coverage
# blocks are never waivable.
WAIVABLE_CHECKS = {"cognition_boundary", "infant_self_awareness"}


def mark_verified(package: Path, program_report: Path, llm_report: Path | None = None,
                  waiver: Path | None = None, store=None, chapter: int | None = None) -> dict:
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(program_report.read_text(encoding="utf-8"))
    blocking = report.get("blocking", [])
    verdict = "PASS" if not blocking else "BLOCK"
    waiver_record = None
    if blocking:
        non_waivable = [b for b in blocking if b.get("check") not in WAIVABLE_CHECKS]
        if non_waivable:
            raise ValueError(
                "program accept report has non-waivable blocking items: "
                f"{[b.get('check') for b in non_waivable]}; cannot mark verified")
        if waiver is None or not waiver.exists():
            raise ValueError("waiver-able blocking items require an explicit waiver file; refusing to mark")
        waiver_doc = json.loads(waiver.read_text(encoding="utf-8"))
        blocked_checks = {b.get("check") for b in blocking}
        missing = blocked_checks - set(waiver_doc.get("checks", []))
        if missing:
            raise ValueError(f"waiver file does not cover blocked checks: {sorted(missing)}")
        waiver_record = {
            "sha256": hashlib.sha256(waiver.read_bytes()).hexdigest(),
            "checks": sorted(blocked_checks),
            "reasons": {k: v for k, v in (waiver_doc.get("reasons") or {}).items() if k in blocked_checks},
        }
    # Soft-class quality_notes NEVER trigger refusal (R28): a report that only
    # has `quality_notes` (and zero blocking) stamps through unchanged. `store`
    # verification refuses ONLY on an integrity failure (verdict_for raising
    # ValueError) — not on a quality_notes-only report.
    verdict_record_hash: str | None = None
    if store is not None:
        ch = chapter if chapter is not None else manifest.get("chapter")
        if ch is None:
            raise ValueError("mark_verified with store requires a chapter")
        leaf = quality_store.verdict_for(store.db, store, ch)
        if leaf is None or leaf.get("verdict_error") is not None:
            verdict_record_hash = None
        else:
            verdict_record_hash = leaf["quality_verdict_hash"]

    if manifest.get("external_expansion_verified"):
        raise ValueError("already marked verified")

    verification = {
        "program_report_sha256": hashlib.sha256(program_report.read_bytes()).hexdigest(),
        "program_verdict": "PASS" if not blocking else "WAIVED",
        "program_han": report.get("han"),
        "llm_report_sha256": None,
        "waiver": waiver_record,
        "marked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if llm_report and llm_report.exists():
        verification["llm_report_sha256"] = hashlib.sha256(llm_report.read_bytes()).hexdigest()
    if store is not None:
        verification["verdict_record_hash"] = verdict_record_hash

    manifest["external_expansion_verified"] = True
    manifest["verification"] = verification
    manifest_path.write_text(dumps(manifest), encoding="utf-8")
    return manifest


def note_quality(package: Path, verdict_leaf: dict, out_path: Path | None = None) -> dict:
    """Stamp the manifest's soft-class `quality_note` field from a verdict leaf.

    Stamps `manifest["quality_note"]` (a soft class, separate from the
    compliance `verification` + `external_expansion_verified`); does NOT touch
    the compliance block. A verdict_error leaf still stamps (the note records
    the error, not a score). Waiver logic UNCHANGED (compliance-only); a
    quality_note NEVER needs a waiver. `out_path=None` rewrites
    `<package>/manifest.json` in place; a Path writes to that path instead.
    """
    manifest_path = out_path if out_path is not None else package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    leaf = dict(verdict_leaf or {})
    note: dict = {
        "quality_verdict_hash": leaf.get("quality_verdict_hash"),
        "judge_model": leaf.get("judge_model"),
        "prompt_ver": leaf.get("prompt_ver"),
        "knob_set_hash": leaf.get("knob_set_hash"),
        "verdict_error": leaf.get("verdict_error"),
        "stamped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if leaf.get("verdict_error") is None:
        note["scores"] = {k: leaf.get("verdict", {}).get(k) for k in ("爽点", "钩子", "压抑")}
        note["rationale"] = leaf.get("verdict", {}).get("rationale")
    manifest["quality_note"] = note
    manifest_path.write_text(dumps(manifest), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    from novel_engine.condensed.store import Store
    parser = argparse.ArgumentParser(description="Mark an expansion package as verified")
    parser.add_argument("package", type=Path, help="expansion package directory")
    parser.add_argument("--program-report", type=Path, required=True,
                        help="accept_report.json from the programmatic checker (verdict must be PASS)")
    parser.add_argument("--llm-report", type=Path,
                        help="optional LLM acceptance report to hash alongside")
    parser.add_argument("--waiver", type=Path,
                        help="JSON waiver file documenting which waiver-able blocks are accepted; "
                             "format: {\"checks\": [\"cognition_boundary\"], \"reasons\": {...}}")
    parser.add_argument("--store-root", type=Path, default=None,
                        help="output root of a Store (read-only verification path); when present, "
                             "mark_verified calls quality_store.verdict_for(chapter) and refuses to "
                             "stamp ONLY on an integrity failure. Stamps "
                             "verification.verdict_record_hash (the leaf hash, or null on a "
                             "verdict_error leaf).")
    parser.add_argument("--chapter", type=int, default=None,
                        help="chapter to verify against the store (defaults to manifest.chapter)")
    args = parser.parse_args(argv)
    store = Store(args.store_root) if args.store_root is not None else None
    try:
        manifest = mark_verified(args.package, args.program_report, args.llm_report, args.waiver,
                                 store=store, chapter=args.chapter)
    finally:
        if store is not None:
            store.close()
    print(json.dumps({"chapter": manifest["chapter"], "verification": manifest["verification"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
