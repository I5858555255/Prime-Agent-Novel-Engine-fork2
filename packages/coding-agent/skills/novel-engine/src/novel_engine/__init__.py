"""Novel-Engine: deterministic-state long-form novel generation pipeline.

When loaded as a Prime-Agent Python skill, the agent invokes the functions
exposed in `novel_engine.agent_api` (e.g. `await novel_engine.generate_chapter(1)`)
from inside the persistent IPython kernel.

The standalone `production_runner.py` path is preserved unchanged (dual-mode):
this module is an additional, agent-callable entrypoint over the same
`PipelineOrchestrator`.

The agent-API re-export is wrapped so that any failure there can never break the
base package import used by the standalone production runner.
"""
__all__ = []

try:
    from novel_engine.agent_api import (
        branch_compare,
        commit,
        generate_chapter,
        generate_chapter_with_subagents,
        generate_scene_unit,
        init_state,
        review_chapter,
        self_heal,
        set_kernel_handles,
    )

    __all__ = [
        "init_state",
        "generate_chapter",
        "generate_chapter_with_subagents",
        "generate_scene_unit",
        "review_chapter",
        "self_heal",
        "branch_compare",
        "commit",
        "set_kernel_handles",
    ]
except Exception:  # pragma: no cover - keep package importable even if agent API fails
    pass
