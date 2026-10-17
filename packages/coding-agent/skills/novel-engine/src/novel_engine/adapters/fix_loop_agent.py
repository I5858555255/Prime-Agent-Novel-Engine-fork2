"""Agent-driven fix loop router for novel production.

When Prime-Agent RLM kernel is available, the fix loop can leverage
agent reasoning to decide remediation strategy instead of hardcoded
deterministic branching.

Decision flow (per fix-loop iteration):
  1. Collect review issues + deterministic gate issues
  2. Present structured diagnostic to agent
  3. Agent returns recommended action: patch | rewrite_scene | rewrite_chapter | stop
  4. Router executes the recommended action
  5. Loop continues or terminates based on agent decision
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

    This is a thin wrapper around the domain tools that structures
    the diagnostic context for Prime-Agent reasoning. When no RLM
    kernel is present, it returns a default recommendation to let
    the existing deterministic path run.
    """

    def __init__(self, orchestrator: "PipelineOrchestrator"):
        self.orch = orchestrator

    def build_diagnostic(self, chapter_num: int, current: str,
                         best_state: "_gates.BestCandidateState",
                         iteration: int, result: dict,
                         task_card: dict, world_state: dict,
                         _reaction_fix_budget: int) -> FixDiagnostic:
        """Collect all quality signals into a structured diagnostic."""
        staged = self.orch._stage_review(
            chapter_num, task_card,
            self.orch._frozen_synopsis.get(chapter_num) or {},
            current, world_state,
        )
        score = staged.get("score", 0)
        review = staged.get("review", {})
        issues = review.get("issues", [])

        policy = self.orch._policy
        has_high = any(
            b for b, _ in (
                self.orch._review_issue_is_blocking(policy, iss)
                for iss in issues
            )
        )

        det = self.orch._deterministic_quality_gate(current, task_card)
        det_issues = det.get("issues", [])
        soft = det.get("soft_issues", [])

        min_ch = int(policy["publication_line"])
        soft_line = int(policy.get("soft_publication_line", min_ch - 3))
        _lsr_cfg = policy.get("low_score_rewrite") or {}
        _lsr_floor = float(_lsr_cfg.get("floor", min_ch - 28))
        low_score = score < _lsr_floor

        return FixDiagnostic(
            chapter_num=chapter_num,
            current_score=score,
            best_score=best_state.best_score,
            has_high_issues=has_high,
            deterministic_issues=det_issues,
            soft_issues=soft,
            review_issues=issues,
            reaction_pending=bool(det.get("reaction_hits")) and _reaction_fix_budget > 0,
            low_score_eligible=low_score,
            no_improve_count=best_state.no_improve,
            max_iterations=int(self.orch.config.get("pipeline", {}).get("max_chapter_fix_rounds", 2)),
            iteration=iteration,
        )

    def recommend(self, diagnostic: FixDiagnostic) -> FixRecommendation:
        """Return a remediation recommendation based on deterministic rules.

        In Phase 3, this will be extended to call rlm() for agent reasoning.
        Currently implements rule-based defaults consistent with the existing
        deterministic logic.
        """
        d = diagnostic

        # Terminal conditions — no more action needed
        if d.current_score >= 88 and not d.has_high_issues:
            return FixRecommendation(
                action="proceed",
                reasoning=f"score={d.current_score} >= 88, no high issues — pass",
            )

        if d.no_improve_count >= 3:
            return FixRecommendation(
                action="stop",
                reasoning=f"no improvement for {d.no_improve_count} iterations — terminate",
            )

        # Low score — escalate to whole-chapter rewrite
        if d.low_score_eligible and d.iteration == 0:
            return FixRecommendation(
                action="rewrite_chapter",
                reasoning=f"score={d.current_score} below floor — escalate to whole-chapter rewrite",
            )

        # Reaction consistency pending — patch specific scenes
        if d.reaction_pending:
            return FixRecommendation(
                action="patch",
                reasoning="reaction consistency hit — targeted scene patch",
                target_scenes=[],
            )

        # High issues in review — patch weak scenes
        if d.has_high_issues:
            return FixRecommendation(
                action="patch",
                reasoning=f"{len(d.review_issues)} high-severity issues — patch weak scenes",
                target_scenes=[],
            )

        # Deterministic hard issues — fulltext fix
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

        # Gray band — proceed with flag
        if d.current_score >= 85:
            return FixRecommendation(
                action="proceed",
                reasoning=f"gray-band score={d.current_score} — accept with flag",
            )

        # Default: patch
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
                }, ensure_ascii=False),
                receiver_role="child",
                receiver_name=handle.name,
            )
            reply = await agent_message.recv(receiver_role="parent")
            if reply:
                recommendation = json.loads(reply[0][1].scene_text if hasattr(reply[0][1], 'scene_text') else str(reply[0][1]))
                return FixRecommendation(
                    action=recommendation.get("action", "patch"),
                    reasoning=recommendation.get("reasoning", "agent decision"),
                    target_scenes=recommendation.get("target_scenes", []),
                    directive=recommendation.get("directive", ""),
                )
        except Exception as exc:
            logger.warning(f"RLM fix decision failed (ch{diagnostic.chapter_num}): {exc}; using deterministic")
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
