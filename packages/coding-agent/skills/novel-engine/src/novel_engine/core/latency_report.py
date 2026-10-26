"""Summarize chapter traces: python -m novel_engine.core.latency_report AUDIT_DIR."""
import argparse
import json
import math
from pathlib import Path


def percentiles(values):
    xs = sorted(values)
    return {f"p{p}": xs[max(0, math.ceil(p / 100 * len(xs)) - 1)] if xs else None
            for p in (50, 90, 95, 99)}


def summarize(paths):
    reports = [json.loads(Path(p).read_text(encoding="utf-8")) for p in paths]
    groups = {}
    for name, records in (("all", reports), ("passed", [r for r in reports if r["success"]]),
                          ("failed", [r for r in reports if not r["success"]]),
                          ("published", [r for r in reports if r.get("published") is True]),
                          ("unpublished", [r for r in reports if r.get("published") is False])):
        groups[name] = {"samples": len(records), "wall_s": percentiles([r["wall_s"] for r in records])}
    stages = {}
    for report in reports:
        per_run = {}
        for event in report["events"]:
            if event["kind"] == "stage":
                per_run[event["name"]] = per_run.get(event["name"], 0) + event["wall_s"]
        for name, wall in per_run.items():
            stages.setdefault(name, []).append(wall)
    return {"chapters": groups, "stages_s": {n: percentiles(v) for n, v in stages.items()},
            "calls": sum(r["calls"] for r in reports),
            "failed_calls": sum(r["failed_calls"] for r in reports),
            "retry_calls": sum(r["retry_calls"] for r in reports)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    paths = sorted(args.directory.glob("chapter_*.json"))
    if not paths:
        parser.error("no chapter traces found")
    print(json.dumps(summarize(paths), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
