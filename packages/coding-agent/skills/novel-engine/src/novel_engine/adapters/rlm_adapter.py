"""Adapter layer: connects PipelineOrchestrator to Prime-Agent RLM kernel.

When _KERNEL["rlm"] is present, scene groups are dispatched to RLM subagents.
When absent, degrades to the existing ThreadPoolExecutor path transparently.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


def _assemble_chapter(scene_contents: dict, assembler) -> str:
    """Build full chapter text from a {scene_num: SceneOutput} map."""
    scenes = [scene_contents[n] for n in sorted(scene_contents)]
    return assembler(scenes)


class NovelRLMAdapter:
    """Adapts PipelineOrchestrator to Prime-Agent RLM kernel.

    Public contract: run_chapter(chapter_num) returns the same dict shape as
    PipelineOrchestrator.generate_single_chapter().
    """

    def __init__(self, orchestrator: "PipelineOrchestrator"):
        self.orch = orchestrator
        self._trace: list[dict] = []

    async def run_chapter(self, chapter_num: int) -> dict:
        """Full chapter pipeline via RLM where available, sync fallback otherwise.

        Returns {"chapter": int, "success": bool, "score": float|None, "errors": list}.
        """
        rlm = _get_kernel_rlm()
        agent_message = _get_kernel_agent_message()

        if rlm is None:
            # No kernel — degrade to synchronous path (identical to pre-refactor)
            return self.orch.generate_single_chapter(chapter_num)

        # ── Phase 1: deterministic stages ──────────────────────────────
        world_state = self.orch._stage_world_sim(chapter_num)
        task_card = self.orch._stage_directing(chapter_num, world_state)
        synopsis = self.orch._stage_synopsis(task_card)

        # ── Phase 2: scene generation via RLM subagents ────────────────
        try:
            novel_text = await self._stage_write_rlm(
                chapter_num, task_card, synopsis, rlm, agent_message
            )
        except Exception as exc:
            logger.warning(
                f"RLM write failed (ch{chapter_num}): {exc}; "
                f"falling back to synchronous writer"
            )
            novel_text = self.orch._stage_write(task_card, synopsis)

        # ── Phase 3: review ────────────────────────────────────────────
        self.orch.current_novel = novel_text
        try:
            staged = self.orch._stage_review(
                chapter_num, task_card, synopsis, novel_text, world_state
            )
        except Exception as exc:
            logger.error(f"RLM review failed (ch{chapter_num}): {exc}")
            return {
                "chapter": chapter_num,
                "success": False,
                "score": None,
                "errors": [f"review: {exc}"],
            }

        score = staged["score"]
        verdict = staged["verdict"]

        # ── Phase 4: commit ────────────────────────────────────────────
        try:
            self.orch.state_machine.commit_chapter(
                chapter_num=chapter_num,
                novel_content=novel_text,
                synopsis_content=json.dumps(synopsis, ensure_ascii=False),
                outline_content=json.dumps(task_card, ensure_ascii=False),
                world_state_snapshot=world_state,
            )
        except Exception as exc:
            logger.exception(f"Commit failed (ch{chapter_num}): {exc}")
            return {
                "chapter": chapter_num,
                "success": False,
                "score": score,
                "errors": [f"commit: {exc}"],
            }

        return {
            "chapter": chapter_num,
            "success": verdict == "pass",
            "score": score,
        }

    async def _stage_write_rlm(
        self,
        chapter_num: int,
        task_card: dict,
        synopsis: dict,
        rlm,
        agent_message,
    ) -> str:
        """Dispatch scene groups to RLM subagents; fall back per-group on failure.

        Each independent scene group gets one rlm("sub-task") spawn. The subagent
        is expected to call generate_scene_unit() and send results back via
        agent_message. On any dispatch or receive failure, that group falls back
        to the synchronous ThreadPoolExecutor path.
        """
        scenes = task_card.get("scene_blueprints", [])
        groups = self.orch.writer._group_independent_scenes(scenes)
        scene_contents: dict = {}

        for group in groups:
            try:
                handle = await rlm("sub-task")
                spec = {
                    "chapter_num": chapter_num,
                    "task_card": task_card,
                    "synopsis": synopsis,
                    "scene_blueprints": group,
                    "pacing_constraints": "",
                }
                await agent_message.send(
                    content=json.dumps(spec, ensure_ascii=False),
                    receiver_role="child",
                    receiver_name=handle.name,
                )
                reply = await agent_message.recv(receiver_role="parent")
                for bp, content in reply:
                    scene_contents[bp.get("scene_num")] = content
                self._trace.append({
                    "phase": "scene_group_rlm",
                    "chapter": chapter_num,
                    "group_size": len(group),
                    "status": "ok",
                })
            except Exception as exc:
                logger.warning(
                    f"RLM group failed (ch{chapter_num}, {len(group)} scenes): {exc}; "
                    f"falling back to ThreadPool"
                )
                # Per-group fallback: use existing synchronous writer
                group_contents = self.orch.writer.generate_scenes(
                    {**task_card, "scene_blueprints": group},
                    synopsis,
                )
                for so in group_contents:
                    scene_contents[so.scene_id] = so
                self._trace.append({
                    "phase": "scene_group_fallback",
                    "chapter": chapter_num,
                    "group_size": len(group),
                    "status": "fallback",
                    "reason": str(exc),
                })

        return _assemble_chapter(scene_contents, self.orch._assemble_chapter_text)

    def trace(self) -> list[dict]:
        """Return the run-local trace entries for observability."""
        return list(self._trace)


def _get_kernel_rlm():
    """Read the RLM handle from the module-level _KERNEL dict."""
    from novel_engine.agent_api import _KERNEL
    return _KERNEL.get("rlm")


def _get_kernel_agent_message():
    """Read the agent_message handle from the module-level _KERNEL dict."""
    from novel_engine.agent_api import _KERNEL
    return _KERNEL.get("agent_message")
