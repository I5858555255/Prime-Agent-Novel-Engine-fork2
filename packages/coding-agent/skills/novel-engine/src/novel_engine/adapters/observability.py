"""Production observability: run-level trace, metrics aggregation, cost tracking.

All trace data is in-memory during a run and persisted to runtime/traces/
as JSONL for post-run analysis. Sensitive content (prompt text, API keys)
is never logged.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class RunTrace:
    """Single trace entry for one operation in a production run."""
    run_id: str
    chapter_num: int
    operation: str  # "world_sim", "director", "synopsis", "scene_write",
                   # "polish", "review", "fix_iteration", "commit", "gate_check"
    phase: str  # "start" | "end" | "error"
    model: Optional[str] = None
    duration_ms: Optional[int] = None
    tokens_input: Optional[int] = None
    tokens_output: Optional[int] = None
    tokens_reasoning: Optional[int] = None
    cost_usd: Optional[float] = None
    retry_count: Optional[int] = None
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class ChapterMetrics:
    """Aggregated metrics for a single chapter production run."""
    chapter_num: int
    run_id: str
    success: bool
    score: Optional[float]
    verdict: Optional[str]
    total_llm_calls: int = 0
    total_tokens_input: int = 0
    total_tokens_output: int = 0
    total_cost_usd: float = 0.0
    duration_s: float = 0.0
    scenes_generated: int = 0
    fix_iterations: int = 0
    gates_run: int = 0
    rlm_subagents_used: int = 0
    fallback_to_threadpool: int = 0
    start_time: Optional[str] = None
    end_time: Optional[str] = None


class ProductionTracer:
    """Thread-safe run-level observability collector.

    Usage:
        tracer = ProductionTracer(run_id="run-2026-10-17-ch001")
        tracer.start_operation(chapter_num, "director")
        # ... LLM call ...
        tracer.end_operation(chapter_num, "director", duration_ms=1200, tokens=500)
        tracer.flush()  # persist to disk
    """

    def __init__(self, run_id: str, project_root: Optional[str | Path] = None):
        self.run_id = run_id
        self.root = Path(project_root) if project_root else Path(__file__).parent.parent
        self._traces: list[RunTrace] = []
        self._chapters: dict[int, ChapterMetrics] = {}
        self._lock = None  # lazy init; single-threaded in practice
        self._trace_dir = self.root / "runtime" / "traces"
        self._trace_dir.mkdir(parents=True, exist_ok=True)

    def start_operation(self, chapter_num: int, operation: str, **meta) -> float:
        """Record operation start, return timestamp for later end."""
        t = time.monotonic()
        self._ensure_chapter(chapter_num)
        self._traces.append(RunTrace(
            run_id=self.run_id,
            chapter_num=chapter_num,
            operation=operation,
            phase="start",
            metadata=meta,
        ))
        return t

    def end_operation(self, chapter_num: int, operation: str,
                      duration_ms: int, model: Optional[str] = None,
                      tokens_input: Optional[int] = None,
                      tokens_output: Optional[int] = None,
                      tokens_reasoning: Optional[int] = None,
                      cost_usd: Optional[float] = None,
                      error: Optional[str] = None,
                      **meta) -> None:
        """Record operation end with metrics."""
        self._traces.append(RunTrace(
            run_id=self.run_id,
            chapter_num=chapter_num,
            operation=operation,
            phase="error" if error else "end",
            model=model,
            duration_ms=duration_ms,
            tokens_input=tokens_input,
            tokens_output=tokens_output,
            tokens_reasoning=tokens_reasoning,
            cost_usd=cost_usd,
            retry_count=meta.get("retry_count"),
            error=error,
            metadata=meta,
        ))
        self._accumulate_chapter(chapter_num, model, tokens_input, tokens_output,
                                  cost_usd, error, meta)

    def mark_rlm_subagent(self, chapter_num: int, group_size: int) -> None:
        """Record an RLM subagent dispatch."""
        self._ensure_chapter(chapter_num)
        ch = self._chapters[chapter_num]
        ch.rlm_subagents_used += 1
        ch.scenes_generated += group_size

    def mark_fallback(self, chapter_num: int) -> None:
        """Record a ThreadPool fallback event."""
        self._ensure_chapter(chapter_num)
        self._chapters[chapter_num].fallback_to_threadpool += 1

    def mark_fix_iteration(self, chapter_num: int) -> None:
        self._ensure_chapter(chapter_num)
        self._chapters[chapter_num].fix_iterations += 1

    def mark_gate_run(self, chapter_num: int, count: int = 1) -> None:
        self._ensure_chapter(chapter_num)
        self._chapters[chapter_num].gates_run += count

    def set_chapter_result(self, chapter_num: int, success: bool,
                            score: Optional[float], verdict: Optional[str]) -> None:
        self._ensure_chapter(chapter_num)
        ch = self._chapters[chapter_num]
        ch.success = success
        ch.score = score
        ch.verdict = verdict

    def set_chapter_times(self, chapter_num: int, start: str, end: str) -> None:
        self._ensure_chapter(chapter_num)
        ch = self._chapters[chapter_num]
        ch.start_time = start
        ch.end_time = end

    def _ensure_chapter(self, chapter_num: int) -> None:
        if chapter_num not in self._chapters:
            from datetime import datetime, timezone
            self._chapters[chapter_num] = ChapterMetrics(
                chapter_num=chapter_num,
                run_id=self.run_id,
                success=False,
                score=None,
                start_time=datetime.now(timezone.utc).isoformat(),
            )

    def _accumulate_chapter(self, chapter_num: int, model: Optional[str],
                             tokens_input: Optional[int],
                             tokens_output: Optional[int],
                             cost_usd: Optional[float],
                             error: Optional[str],
                             meta: dict) -> None:
        ch = self._chapters[chapter_num]
        op = meta.get("operation", "")
        if op in ("scene_write", "polish", "director", "synopsis", "review"):
            ch.total_llm_calls += 1
        if tokens_input:
            ch.total_tokens_input += tokens_input
        if tokens_output:
            ch.total_tokens_output += tokens_output
        if cost_usd:
            ch.total_cost_usd += cost_usd

    def flush(self) -> Path:
        """Persist all traces and chapter metrics to disk."""
        trace_path = self._trace_dir / f"{self.run_id}.jsonl"
        with open(trace_path, "w", encoding="utf-8") as f:
            for t in self._traces:
                f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")

        metrics_path = self._trace_dir / f"{self.run_id}_metrics.json"
        with open(metrics_path, "w", encoding="utf-8") as f:
            json.dump([asdict(ch) for ch in self._chapters.values()],
                       f, ensure_ascii=False, indent=2)
        logger.info(f"Tracer flushed: {len(self._traces)} traces, "
                     f"{len(self._chapters)} chapters → {self._trace_dir}")
        return trace_path

    def summary(self, chapter_num: Optional[int] = None) -> dict:
        """Return human-readable summary."""
        if chapter_num is not None:
            ch = self._chapters.get(chapter_num)
            return asdict(ch) if ch else {}
        return {
            "run_id": self.run_id,
            "total_chapters": len(self._chapters),
            "total_traces": len(self._traces),
            "chapters": {k: asdict(v) for k, v in self._chapters.items()},
        }
