"""Adapter layer: connects PipelineOrchestrator to Prime-Agent RLM kernel.

When _KERNEL["rlm"] is present, scene groups are dispatched to RLM subagents.
When absent, degrades to the existing ThreadPoolExecutor path transparently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


def _assemble_chapter(scene_contents: dict, assembler):
    """Build full chapter text from a {scene_num: SceneOutput} map."""
    scenes = [scene_contents[n] for n in sorted(scene_contents)]
    return assembler(scenes)


class NovelRLMAdapter:
    """Adapts PipelineOrchestrator to Prime-Agent RLM kernel.
    
    The adapter intercepts the write phase and replaces synchronous
    ThreadPoolExecutor fan-out with async RLM subagent dispatch.
    All other phases remain identical to the original pipeline.
    """

    def __init__(self, orchestrator: "PipelineOrchestrator"):
        self.orch = orchestrator
        self._trace: list[dict] = []
        self._rlm_task_id = 0

    def _get_kernel(self):
        """Get RLM and agent_message from global kernel state."""
        from novel_engine.agent_api import _KERNEL
        return _KERNEL.get("rlm"), _KERNEL.get("agent_message")

    def _record_trace(self, phase: str, chapter_num: int, **kwargs):
        """Record a trace entry for observability."""
        trace_entry = {
            "chapter": chapter_num,
            "phase": phase,
            "timestamp": time.time(),
            **kwargs,
        }
        self._trace.append(trace_entry)
        logger.debug(f"Trace [{phase}] ch{chapter_num}: {kwargs}")

    async def run_chapter(self, chapter_num: int) -> dict:
        """Full chapter pipeline via RLM where available, sync fallback otherwise.
        
        Returns the same dict shape as PipelineOrchestrator.generate_single_chapter().
        """
        rlm, agent_message = self._get_kernel()
        
        if rlm is None:
            # No kernel — degrade to synchronous path
            self._record_trace("init", chapter_num, status="fallback_sync")
            return self.orch.generate_single_chapter(chapter_num)

        # ── Phase 1: World Simulation ──
        self._record_trace("world_sim", chapter_num, status="start")
        try:
            world_state = self.orch._stage_world_sim(chapter_num)
            self._record_trace("world_sim", chapter_num, status="complete")
        except Exception as e:
            self._record_trace("world_sim", chapter_num, status="error", error=str(e))
            raise

        # ── Phase 2: Director ──
        self._record_trace("directing", chapter_num, status="start")
        try:
            task_card = self.orch._stage_directing(chapter_num, world_state)
            self._record_trace("directing", chapter_num, status="complete")
        except Exception as e:
            self._record_trace("directing", chapter_num, status="error", error=str(e))
            raise

        # ── Phase 3: Synopsis ──
        self._record_trace("synopsis", chapter_num, status="start")
        try:
            synopsis = self.orch._stage_synopsis(task_card)
            self._record_trace("synopsis", chapter_num, status="complete")
        except Exception as e:
            self._record_trace("synopsis", chapter_num, status="error", error=str(e))
            raise

        # ── Phase 4: Write via RLM ──
        self._record_trace("write", chapter_num, status="start")
        try:
            novel_text = await self._stage_write_rlm(
                chapter_num, task_card, synopsis, rlm, agent_message
            )
            self._record_trace("write", chapter_num, status="complete")
        except Exception as e:
            self._record_trace("write", chapter_num, status="error", error=str(e))
            logger.warning(f"RLM write failed (ch{chapter_num}): {e}; falling back to sync")
            # Fallback to synchronous pipeline for write phase
            novel_text = self.orch._stage_write(task_card, synopsis)

        # ── Phase 5: Review ──
        self.orch.current_novel = novel_text
        self._record_trace("review", chapter_num, status="start")
        try:
            staged = self.orch._stage_review(
                chapter_num, task_card, synopsis, novel_text, world_state
            )
            self._record_trace("review", chapter_num, status="complete")
        except Exception as e:
            self._record_trace("review", chapter_num, status="error", error=str(e))
            return {
                "chapter": chapter_num,
                "success": False,
                "score": None,
                "errors": [f"review: {e}"],
            }

        score = staged.get("score", 0)
        verdict = staged.get("verdict", "fail")

        # ── Phase 6: Commit ──
        self._record_trace("commit", chapter_num, status="start")
        try:
            self.orch.state_machine.commit_chapter(
                chapter_num=chapter_num,
                novel_content=novel_text,
                synopsis_content=json.dumps(synopsis, ensure_ascii=False),
                outline_content=json.dumps(task_card, ensure_ascii=False),
                world_state_snapshot=world_state,
            )
            self._record_trace("commit", chapter_num, status="complete")
        except Exception as e:
            self._record_trace("commit", chapter_num, status="error", error=str(e))
            return {
                "chapter": chapter_num,
                "success": False,
                "score": score,
                "errors": [f"commit: {e}"],
            }

        return {
            "chapter": chapter_num,
            "success": verdict == "pass",
            "score": score,
            "rlm_used": True,
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
        
        Each independent scene group gets one rlm("sub-task") spawn.
        The subagent calls generate_scene_unit() and sends results back
        via agent_message.
        """
        scenes = task_card.get("scene_blueprints", [])
        groups = self.orch.writer._group_independent_scenes(scenes)
        scene_contents: dict = {}
        fallback_count = 0

        for group_idx, group in enumerate(groups):
            group_id = f"ch{chapter_num}_group{group_idx}"
            
            # Try RLM dispatch for this group
            try:
                self._record_trace("rlm_dispatch", chapter_num, 
                                   group_id=group_id, group_size=len(group), status="attempt")
                
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
                
                # Parse reply - should be list of (blueprint, SceneOutput) tuples
                for bp, content in reply:
                    scene_num = bp.get("scene_num")
                    if scene_num is not None:
                        scene_contents[scene_num] = content
                
                self._record_trace("rlm_dispatch", chapter_num,
                                   group_id=group_id, status="success",
                                   scenes_generated=len(scene_contents))
                
            except Exception as exc:
                logger.warning(
                    f"RLM group failed (ch{chapter_num}, group {group_idx}, "
                    f"{len(group)} scenes): {exc}; falling back to ThreadPool"
                )
                self._record_trace("rlm_dispatch", chapter_num,
                                   group_id=group_id, status="fallback",
                                   error=str(exc))
                
                # Fallback: use synchronous writer for this group
                group_task_card = dict(task_card, scene_blueprints=group)
                group_scenes = self.orch.writer.generate_scenes(
                    group_task_card, synopsis
                )
                for so in group_scenes:
                    scene_contents[so.scene_id] = so
                fallback_count += 1

        # Assemble chapter from collected scenes
        if scene_contents:
            return _assemble_chapter(scene_contents, self.orch._assemble_chapter_text)
        
        # Complete fallback if no scenes were generated
        logger.warning(f"No scenes generated for ch{chapter_num}, using sync fallback")
        return self.orch._stage_write(task_card, synopsis)

    def trace(self) -> list[dict]:
        """Return the run-local trace entries for observability."""
        return list(self._trace)

    def summary(self) -> dict:
        """Return a human-readable summary of the run."""
        return {
            "traces": len(self._trace),
            "stages": list(set(t["phase"] for t in self._trace)),
            "has_errors": any(t.get("status") == "error" for t in self._trace),
            "has_fallbacks": any(t.get("status") == "fallback" for t in self._trace),
        }
