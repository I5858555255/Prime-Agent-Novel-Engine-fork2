"""Novel-Engine: condensed-manuscript production for long-form novels.

This package now ships the condensed pipeline only: deterministic-state
chapter briefs (StateDB facts + timeline + hash chain) plus portable
external-LLM expansion packages. The legacy prose pipeline
(agents/pipeline/quality/engine/adapters) has been removed.

When loaded as a Prime-Agent Python skill, the agent invokes:
    await novel_engine.generate_condensed_chapter(1, output_root=...)
    await novel_engine.condensed_status(output_root=...)
"""
__all__ = []

from novel_engine.condensed.api import condensed_status, generate_condensed_chapter

__all__.extend(["generate_condensed_chapter", "condensed_status"])
