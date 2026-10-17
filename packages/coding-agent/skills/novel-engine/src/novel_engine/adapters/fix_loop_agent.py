"""Agent-driven fix loop router for novel production.

This module provides decision-making logic for the fix loop,
determining whether to patch, rewrite, or stop based on quality signals.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)


@dataclass
class FixDiagnostic:
    """Structured diagnostic summary for agent decision-making."""
    chapter_num: int
    current_score: float
    best_score: float
    has_high_issues: bool
    deterministic_issues: list[str]
    soft_issues: list[str]
    review_issues: list[dict]
    reaction_pending: bool
    low_score_eligible: bool
    no_improve_count: int
    max_iterations: int
    iteration: int


@dataclass
class FixRecommendation:
    """Agent's recommended remediation action."""
    action: str  # "patch" | "rewrite_scene" | "rewrite_chapter" | "stop" | "proceed"
    reasoning: str
    target_scenes: list[int] = field(default_factory=list)
    directive: str = ""


class FixLoopAgent:
    """Analyzes fix-loop state and recommends remediation strategy.
    
    In Phase 3, this can be extended to use RLM subagents for reasoning.
    Currently implements rule-based defaults consistent with the existing
    deterministic logic, providing a clean interface for future expansion.
    """

    def __init__(self, orchestrator: Optional["PipelineOrchestrator"] = None):
        self.orch = orchestrator
        self._trace: list[dict] = []

    def build_diagnostic(self, chapter_num: int, current: str,
                         best_state: "_gates.BestCandidateState",
                         iteration: int, result: dict,
                         task_card: dict, world_state: dict,
                         _reaction_fix_budget: int) -> FixDiagnostic:
        """Collect all quality signals into a structured diagnostic."""
        staged = self.orch._stage_review(
            chapter_num, task_card,
            self.orch._frozen_synopsis.get(chapter_num) if self.orch else {},
            current, world_state,
        ) if self.orch else {"score": 0, "review": {}}
        
        score = staged.get("score", 0)
        review = staged.get("review", {})
        issues = review.get("issues", [])

        policy = self.orch._policy if self.orch else {}
        has_high = any(
            b for b, _ in (
                self.orch._review_issue_is_blocking(policy, iss) if self.orch else (False, False)
                for iss in issues
            )
        )

        det = self.orch._deterministic_quality_gate(current, task_card) if self.orch else {"issues": [], "soft_issues": []}
        det_issues = det.get("issues", [])
        soft = det.get("soft_issues", [])

        min_ch = int(policy.get("publication_line", 88)) if policy else 88
        _lsr_cfg = policy.get("low_score_rewrite") or {} if policy else {}
        _lsr_floor = float(_lsr_cfg.get("floor", min_ch - 28))
        low_score = score < _lsr_floor

        return FixDiagnostic(
            chapter_num=chapter_num,
            current_score=score,
            best_score=best_state.best_score if best_state else 0,
            has_high_issues=has_high,
            deterministic_issues=det_issues,
            soft_issues=soft,
            review_issues=issues,
            reaction_pending=bool(det.get("reaction_hits")) and _reaction_fix_budget > 0,
            low_score_eligible=low_score,
            no_improve_count=best_state.no_improve if best_state else 0,
            max_iterations=int((self.orch.config.get("pipeline", {}) or {}).get("max_chapter_fix_rounds", 2)) if self.orch else 2,
            iteration=iteration,
        )

    def recommend(self, diagnostic: FixDiagnostic) -> FixRecommendation:
        """Return a remediation recommendation based on deterministic rules.
        
        Rules (in priority order):
        1. If score >= 88 and no high issues → proceed (pass)
        2. If no improvement for 3+ iterations → stop
        3. If low score eligible at iteration 0 → rewrite_chapter
        4. If reaction pending → patch (targeted scene fix)
        5. If high issues in review → patch
        6. If deterministic hard issues → rewrite_scene
        7. If gray band (85-88) → proceed with flag
        8. Default → patch
        """
        d = diagnostic

        # Rule 1: Pass condition
        if d.current_score >= 88 and not d.has_high_issues:
            return FixRecommendation(
                action="proceed",
                reasoning=f"score={d.current_score} >= 88, no high issues — pass",
            )

        # Rule 2: Early stop
        if d.no_improve_count >= 3:
            return FixRecommendation(
                action="stop",
                reasoning=f"no improvement for {d.no_improve_count} iterations — terminate",
            )

        # Rule 3: Low score escalation
        if d.low_score_eligible and d.iteration == 0:
            return FixRecommendation(
                action="rewrite_chapter",
                reasoning=f"score={d.current_score} below floor — escalate to whole-chapter rewrite",
            )

        # Rule 4: Reaction consistency
        if d.reaction_pending:
            return FixRecommendation(
                action="patch",
                reasoning="reaction consistency hit — targeted scene patch",
                target_scenes=[],
            )

        # Rule 5: High review issues
        if d.has_high_issues:
            return FixRecommendation(
                action="patch",
                reasoning=f"{len(d.review_issues)} high-severity issues — patch weak scenes",
                target_scenes=[],
            )

        # Rule 6: Deterministic hard issues
        hard_det = [i for i in d.deterministic_issues
                    if any(i.startswith(p) for p in
                           ("[称谓]", "[canon", "[泄漏]", "[命名矛盾]",
                            "[跨场危机]", "[伏笔极性矛盾]"))]
        if hard_det:
            return FixRecommendation(
                action="rewrite_scene",
                reasoning=f"{len(hard_det)} deterministic hard issues — scene-level rewrite",
                target_scenes=[],
            )

        # Rule 7: Gray band
        if d.current_score >= 85:
            return FixRecommendation(
                action="proceed",
                reasoning=f"gray-band score={d.current_score} — accept with flag",
            )

        # Rule 8: Default
        return FixRecommendation(
            action="patch",
            reasoning=f"default: score={d.current_score} — targeted patch",
        )

    async def recommend_via_rlm(self, diagnostic: FixDiagnostic,
                                 rlm, agent_message) -> FixRecommendation:
        """Use Prime-Agent RLM to reason about the fix strategy.
        
        Falls back to deterministic recommendation on any failure.
        """
        try:
            prompt = self._build_rlm_prompt(diagnostic)
            handle = await rlm("fix-decision")
            await agent_message.send(
                content=json.dumps({
                    "type": "fix_diagnostic",
                    "diagnostic": {
                        "chapter_num": diagnostic.chapter_num,
                        "current_score": diagnostic.current_score,
                        "best_score": diagnostic.best_score,
                        "has_high_issues": diagnostic.has_high_issues,
                        "deterministic_issues": diagnostic.deterministic_issues,
                        "soft_issues": diagnostic.soft_issues,
                        "review_issues_count": len(diagnostic.review_issues),
                        "reaction_pending": diagnostic.reaction_pending,
                        "low_score_eligible": diagnostic.low_score_eligible,
                        "no_improve_count": diagnostic.no_improve_count,
                        "iteration": diagnostic.iteration,
                        "max_iterations": diagnostic.max_iterations,
                    },
                    "prompt": prompt,
                }, ensure_ascii=False),
                receiver_role="child",
                receiver_name=handle.name,
            )
            
            reply = await agent_message.recv(receiver_role="parent")
            
            # Parse reply
            if reply:
                reply_data = reply[0][1] if hasattr(reply[0][1], 'scene_text') else reply[0][1]
                if isinstance(reply_data, dict):
                    return FixRecommendation(
                        action=reply_data.get("action", "patch"),
                        reasoning=reply_data.get("reasoning", "agent decision"),
                        target_scenes=reply_data.get("target_scenes", []),
                        directive=reply_data.get("directive", ""),
                    )
            
        except Exception as e:
            logger.warning(f"RLM fix decision failed (ch{diagnostic.chapter_num}): {e}; using deterministic")
        
        return self.recommend(diagnostic)

    def _build_rlm_prompt(self, diagnostic: FixDiagnostic) -> str:
        """Build a prompt for the RLM subagent to reason about fix strategy."""
        issues_summary = "\n".join(
            f"  - {iss.get('dimension', '?')}: {iss.get('description', '')[:100]}"
            for iss in diagnostic.review_issues[:5]
        )
        return (
            f"Chapter {diagnostic.chapter_num} fix decision.\n"
            f"Score: {diagnostic.current_score} (best: {diagnostic.best_score})\n"
            f"Iteration: {diagnostic.iteration}/{diagnostic.max_iterations}\n"
            f"High issues: {diagnostic.has_high_issues}\n"
            f"Reaction pending: {diagnostic.reaction_pending}\n"
            f"Deterministic hard issues: {len(diagnostic.deterministic_issues)}\n"
            f"Review issues:\n{issues_summary}\n"
            f"Recommend action: patch | rewrite_scene | rewrite_chapter | stop | proceed"
        )

    def _record_trace(self, phase: str, chapter_num: int, **kwargs):
        """Record a trace entry for observability."""
        trace_entry = {
            "chapter": chapter_num,
            "phase": f"fix_{phase}",
            "timestamp": __import__('time').time(),
            **kwargs,
        }
        self._trace.append(trace_entry)
        logger.debug(f"FixTrace [{phase}] ch{chapter_num}: {kwargs}")

    def trace(self) -> list[dict]:
        """Return the run-local trace entries."""
        return list(self._trace)
