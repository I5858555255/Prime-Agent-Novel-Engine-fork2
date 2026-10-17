"""Tests for ProductionTracer — P4 integration."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.adapters.observability import ProductionTracer


class TestProductionTracer:
    def test_basic_trace_flow(self, tmp_path):
        tracer = ProductionTracer(run_id="test-001", project_root=tmp_path)
        t0 = tracer.start_operation(1, "director")
        tracer.end_operation(1, "director", duration_ms=500, model="agnes-2.5-flash",
                            tokens_input=1000, tokens_output=200, cost_usd=0.01)
        path = tracer.flush()
        assert path.exists()

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2  # start + end
        start = json.loads(lines[0])
        end = json.loads(lines[1])
        assert start["phase"] == "start"
        assert end["phase"] == "end"
        assert end["model"] == "agnes-2.5-flash"
        assert end["duration_ms"] == 500

    def test_metrics_file_written(self, tmp_path):
        tracer = ProductionTracer(run_id="test-002", project_root=tmp_path)
        tracer.start_operation(1, "scene_write")
        tracer.end_operation(1, "scene_write", duration_ms=1200,
                            tokens_input=3000, tokens_output=800, cost_usd=0.03)
        tracer.mark_rlm_subagent(1, group_size=2)
        tracer.set_chapter_result(1, success=True, score=85.5, verdict="pass")
        path = tracer.flush()

        metrics = json.loads((tmp_path / "runtime" / "traces" / "test-002_metrics.json").read_text())
        assert len(metrics) == 1
        m = metrics[0]
        assert m["total_llm_calls"] == 1
        assert m["rlm_subagents_used"] == 1
        assert m["scenes_generated"] == 2
        assert m["success"] is True
        assert m["score"] == 85.5

    def test_fallback_event_tracking(self, tmp_path):
        tracer = ProductionTracer(run_id="test-003", project_root=tmp_path)
        tracer.mark_fallback(1)
        tracer.mark_fallback(1)
        tracer.set_chapter_result(1, success=False, score=None, verdict=None)
        path = tracer.flush()

        metrics = json.loads((tmp_path / "runtime" / "traces" / "test-003_metrics.json").read_text())
        assert metrics[0]["fallback_to_threadpool"] == 2

    def test_error_trace(self, tmp_path):
        tracer = ProductionTracer(run_id="test-004", project_root=tmp_path)
        tracer.end_operation(1, "review", duration_ms=3000, error="API timeout",
                            tokens_input=500, tokens_output=0, cost_usd=0.005)
        path = tracer.flush()

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        assert json.loads(lines[0])["phase"] == "error"
        assert json.loads(lines[0])["error"] == "API timeout"

    def test_summary_returns_dict(self, tmp_path):
        tracer = ProductionTracer(run_id="test-005", project_root=tmp_path)
        tracer.start_operation(1, "director")
        tracer.end_operation(1, "director", duration_ms=400, model="flash")
        tracer.start_operation(2, "director")
        tracer.end_operation(2, "director", duration_ms=450, model="flash")
        tracer.flush()

        summary = tracer.summary()
        assert summary["run_id"] == "test-005"
        assert summary["total_chapters"] == 2
        assert summary["total_traces"] == 4

    def test_chapter_summary(self, tmp_path):
        tracer = ProductionTracer(run_id="test-006", project_root=tmp_path)
        tracer.start_operation(1, "director")
        tracer.end_operation(1, "director", duration_ms=400, tokens_input=1000, cost_usd=0.01)
        tracer.mark_fix_iteration(1)
        tracer.mark_gate_run(1, count=3)
        tracer.set_chapter_result(1, success=True, score=88.0, verdict="pass")
        tracer.flush()

        ch_summary = tracer.summary(chapter_num=1)
        assert ch_summary["total_llm_calls"] == 1
        assert ch_summary["fix_iterations"] == 1
        assert ch_summary["gates_run"] == 3
        assert ch_summary["score"] == 88.0
        assert ch_summary["verdict"] == "pass"
