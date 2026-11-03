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


def mark_verified(package: Path, program_report: Path, llm_report: Path | None = None) -> dict:
    manifest_path = package / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(program_report.read_text(encoding="utf-8"))
    if report.get("verdict") != "PASS" or report.get("blocking"):
        raise ValueError(f"program accept report is not PASS: {report.get('verdict')}")
    if manifest.get("external_expansion_verified"):
        raise ValueError("already marked verified")

    verification = {
        "program_report_sha256": hashlib.sha256(program_report.read_bytes()).hexdigest(),
        "program_verdict": "PASS",
        "program_han": report.get("han"),
        "llm_report_sha256": None,
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
    args = parser.parse_args(argv)
    manifest = mark_verified(args.package, args.program_report, args.llm_report)
    print(json.dumps({"chapter": manifest["chapter"], "verification": manifest["verification"]},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
