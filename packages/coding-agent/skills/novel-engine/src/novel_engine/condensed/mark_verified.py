"""mark-verified: record a successful external expansion in the package manifest.

Given an expansion package directory and the programmatic accept report JSON
(optionally plus the LLM acceptance report), rewrites manifest.json with:

    external_expansion_verified = True
    verification = {
        program_report: <sha256 of accept_report.json>,
        program_verdict: "PASS",
        program_han: <int>,
        llm_report: <sha256 | null>,
        marked_at: <iso8601>,
    }

Refuses to mark unless the program report verdict is PASS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .contracts import dumps


# Block classes that may be marked verified with a documented waiver (the
# content itself is acceptable; only a stage-forbidden term or an infant
# self-analysis phrase slipped through). Word-count / metadata / beat-coverage
# blocks are never waivable.
WAIVABLE_CHECKS = {"cognition_boundary", "infant_self_awareness"}


def mark_verified(package: Path, program_report: Path, llm_report: Path | None = None,
                 waiver: Path | None = None) -> dict:
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

    manifest["external_expansion_verified"] = True
    manifest["verification"] = verification
    manifest_path.write_text(dumps(manifest), encoding="utf-8")
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Mark an expansion package as verified")
    parser.add_argument("package", type=Path, help="expansion package directory")
    parser.add_argument("--program-report", type=Path, required=True,
                        help="accept_report.json from the programmatic checker (verdict must be PASS)")
    parser.add_argument("--llm-report", type=Path,
                        help="optional LLM acceptance report to hash alongside")
    parser.add_argument("--waiver", type=Path,
                        help="JSON waiver file documenting which waiver-able blocks are accepted; "
                             "format: {\"checks\": [\"cognition_boundary\"], \"reasons\": {...}}")
    args = parser.parse_args(argv)
    manifest = mark_verified(args.package, args.program_report, args.llm_report, args.waiver)
    print(json.dumps({"chapter": manifest["chapter"], "verification": manifest["verification"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
