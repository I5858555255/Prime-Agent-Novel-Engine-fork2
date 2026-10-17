import json
import statistics
import os
import sys
import argparse
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_BASE = os.path.dirname(_HERE)            # .../src/novel_engine
sys.path.insert(0, _BASE)                 # so `import pipeline` resolves
sys.path.insert(0, os.path.dirname(_BASE))  # .../src, so `import novel_engine` resolves

from pipeline.pipeline_orchestrator import PipelineOrchestrator


def _score_from_result(result):
    """Prefer the direct API: _run_with_retry returns a result dict with 'score'."""
    if isinstance(result, dict):
        s = result.get("score")
        if isinstance(s, (int, float)):
            return int(s)
    return None


def _score_for(orch, ch):
    """Fallback: parse the latest matching line from the production log."""
    log = os.path.join(_BASE, "runtime", "logs", "production.log")
    if os.path.exists(log):
        txt = open(log, encoding="utf-8").read()
        for line in reversed(txt.splitlines()):
            m = re.search(rf"Review completed for chapter {ch}: score=(\d+)", line)
            if m:
                return int(m.group(1))
    return None


def run_ab(n: int, start: int, cfg_path: str = None):
    orch = PipelineOrchestrator(project_root=_BASE)
    scores = []
    for ch in range(start, start + n):
        result = orch._run_with_retry(ch)
        s = _score_from_result(result)
        if s is None:
            s = _score_for(orch, ch)
        if s is not None:
            scores.append(s)
    return statistics.mean(scores) if scores else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, nargs="?", default=10)
    ap.add_argument("start", type=int, nargs="?", default=100)
    ap.add_argument("--dry", action="store_true", help="construct orchestrator and print intended range, no generation")
    args = ap.parse_args()
    if args.dry:
        orch = PipelineOrchestrator(project_root=_BASE)
        print(f"Would run A/B: chapters {args.start}..{args.start + args.n - 1} (orchestrator built OK)")
        return
    print("AB avg score:", run_ab(args.n, args.start))


if __name__ == "__main__":
    main()
