# -*- coding: utf-8 -*-
"""Regression test for Task-11 removal.

D2 fixed the dormant second-owner problem by deleting the orchestrator's
private resume methods and replacing their two callers with direct calls
into production_runner.save_resume_state. This test pins that contract so
a future refactor can't re-introduce the old dual-write pattern.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

import inspect
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


def test_dormant_resume_methods_removed():
    """Task-11: _mark_chapter_done / _resume_state_path / resume_from_chapter
    must no longer exist on the orchestrator class."""
    assert not hasattr(PipelineOrchestrator, "_mark_chapter_done"), (
        "_mark_chapter_done is a dormant second owner; delete it."
    )
    assert not hasattr(PipelineOrchestrator, "_resume_state_path"), (
        "_resume_state_path is a dormant second owner; delete it."
    )
    assert not hasattr(PipelineOrchestrator, "resume_from_chapter"), (
        "resume_from_chapter is a dormant second owner; delete it."
    )


def test_no_orchestrator_import_of_save_resume_state():
    """The orchestrator must not top-level-import save_resume_state from
    production_runner — that would create a circular dependency. Callsites
    use inline local imports instead."""
    src = inspect.getsource(PipelineOrchestrator)
    # Check no top-level import exists (class-level __init__ imports are excluded by inspect)
import_section = inspect.getsource(sys.modules['novel_engine.pipeline.pipeline_orchestrator'])[:1000]
assert 'from novel_engine.pipeline.production_runner import' not in import_section, (
    "Orchestrator must not top-level-import from production_runner; "
    "use lazy local imports at each call site."
)
