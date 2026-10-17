"""Domain tool wrappers for Prime-Agent integration.

Each tool has explicit input/output contracts and delegates to existing
novel_engine internals. No new LLM calls are introduced by these wrappers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _build_orch_readonly(project_root: Optional[str | Path] = None):
    """Build a read-only orchestrator instance for tool access.

    use_mock=True ensures no live API calls are made during tool execution.
    The orchestrator is instantiated per-call to avoid shared mutable state.
    """
    from novel_engine.core.llm_client import MockLLMClient
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    return PipelineOrchestrator(project_root=project_root, llm_client=MockLLMClient())


# ---------------------------------------------------------------------------
# Read-only tools
# ---------------------------------------------------------------------------

def get_world_context(chapter_num: int, project_root: Optional[str | Path] = None) -> dict:
    """Get world state snapshot relevant to the given chapter.

    Input:  chapter_num (int), project_root (optional)
    Output: dict with keys: characters, factions, power_system
    """
    orch = _build_orch_readonly(project_root)
    ws = orch.simulator.build_world_state_for_chapter(chapter_num)
    return {
        "characters": ws.get("characters", {}),
        "factions": ws.get("factions", {}),
        "power_system": ws.get("power_system", {}),
    }


def get_character_state(char_name: str, project_root: Optional[str | Path] = None) -> dict:
    """Get current state of a single character from StateDB.

    Input:  char_name (str)
    Output: dict with realm, location, relationships, current_chapter
    """
    from novel_engine.engine.db import StateDB
    root = Path(project_root) if project_root else Path(__file__).parent.parent
    db = StateDB(project_root=root)
    result = db.execute_custom_query(
        "SELECT * FROM characters WHERE name = ? LIMIT 1", (char_name,)
    )
    return result[0] if result else {}


def get_timeline_context(chapter_num: int, project_root: Optional[str | Path] = None) -> dict:
    """Get timeline anchors and elapsed time for the chapter.

    Input:  chapter_num (int)
    Output: dict with anchors, elapsed_since_prev, jump flags
    """
    from novel_engine.timeline.timeline_anchors import get_prev_anchor, load_anchors
    anchors = load_anchors()
    prev = get_prev_anchor(chapter_num)
    return {
        "chapter_num": chapter_num,
        "anchors": anchors,
        "previous_anchor": prev,
    }


def get_foreshadowing_state(chapter_num: int, project_root: Optional[str | Path] = None) -> dict:
    """Get active foreshadowing clues and their fulfillment status.

    Input:  chapter_num (int)
    Output: dict with active_clues, fulfilled, pending
    """
    from novel_engine.engine.db import StateDB
    root = Path(project_root) if project_root else Path(__file__).parent.parent
    db = StateDB(project_root=root)
    clues = db.query_active_foreshadows(chapter_num)
    return {"clues": clues, "chapter_num": chapter_num}


def get_chapter_context(chapter_num: int, project_root: Optional[str | Path] = None) -> dict:
    """Get the task card and synopsis for a chapter (if already generated).

    Input:  chapter_num (int)
    Output: dict with task_card, synopsis, or empty if not yet generated
    """
    root = Path(project_root) if project_root else Path(__file__).parent.parent
    outline_path = root / "chapters" / "outline" / f"chapter_{chapter_num}.json"
    synopsis_path = root / "chapters" / "synopsis" / f"chapter_{chapter_num}.txt"

    task_card = {}
    synopsis = {}
    if outline_path.exists():
        task_card = json.loads(outline_path.read_text(encoding="utf-8"))
    if synopsis_path.exists():
        synopsis = {"synopsis": synopsis_path.read_text(encoding="utf-8")}

    return {"chapter_num": chapter_num, "task_card": task_card, "synopsis": synopsis}


# ---------------------------------------------------------------------------
# Write tools (draft-stage only; committed chapters are immutable)
# ---------------------------------------------------------------------------

def write_or_update_scene(
    chapter_num: int,
    scene_num: int,
    scene_text: str,
    beats: list[str],
    project_root: Optional[str | Path] = None,
) -> dict:
    """Write or update a single scene in the chapter journal (draft stage).

    Input:  chapter_num (int), scene_num (int), scene_text (str), beats (list[str])
    Output: dict with scene_id, status ("written"|"updated"), char_count
    """
    from novel_engine.pipeline.chapter_journal import append_scene
    root = Path(project_root) if project_root else Path(__file__).parent.parent
    append_scene(
        root, chapter_num,
        {
            "scene_id": scene_num,
            "scene_text": scene_text,
            "hook": "",
            "beats": beats,
        },
    )
    return {
        "scene_id": scene_num,
        "status": "written",
        "char_count": len(scene_text),
    }


def validate_continuity(
    chapter_num: int,
    novel_text: str,
    task_card: dict,
    project_root: Optional[str | Path] = None,
) -> dict:
    """Run deterministic continuity checks on novel text.

    Input:  chapter_num, novel_text, task_card
    Output: dict with passed (bool), issues (list of str)
    """
    from novel_engine.pipeline import gates
    root = Path(project_root) if project_root else Path(__file__).parent.parent
    policy_path = root / "config" / "quality_thresholds.json"
    policy = {}
    if policy_path.exists():
        policy = json.loads(policy_path.read_text(encoding="utf-8"))

    # Run deterministic gates
    issues = []
    if hasattr(gates, "check_timeline_gate"):
        result = gates.check_timeline_gate(chapter_num, novel_text, task_card)
        if not result.get("passed", True):
            issues.extend(result.get("issues", []))
    if hasattr(gates, "check_continuity_gate"):
        result = gates.check_continuity_gate(novel_text, task_card)
        if not result.get("passed", True):
            issues.extend(result.get("issues", []))

    return {"passed": len(issues) == 0, "issues": issues, "chapter_num": chapter_num}


def run_quality_checks(
    chapter_num: int,
    novel_text: str,
    task_card: dict,
    synopsis: dict,
    world_state: dict,
    project_root: Optional[str | Path] = None,
) -> dict:
    """Run the full review pipeline and return structured results.

    This is a read-only assessment — it does NOT modify any state.
    Uses MockLLMClient to avoid API costs during tool evaluation.
    """
    from novel_engine.core.llm_client import MockLLMClient
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    orch = PipelineOrchestrator(
        project_root=project_root,
        llm_client=MockLLMClient(),
    )
    staged = orch._stage_review(
        chapter_num, task_card, synopsis, novel_text, world_state
    )
    return {
        "chapter_num": chapter_num,
        "score": staged.get("score"),
        "verdict": staged.get("verdict"),
        "issues": staged.get("review", {}).get("issues", []),
    }


def get_review_feedback(chapter_num: int, project_root: Optional[str | Path] = None) -> dict:
    """Read the review journal for a chapter (if it exists).

    Input:  chapter_num (int)
    Output: dict with score, verdict, review details, or empty if not reviewed
    """
    from novel_engine.pipeline.review_journal import load_review_journal
    root = Path(project_root) if project_root else Path(__file__).parent.parent
    journal = load_review_journal(root, chapter_num)
    return journal if journal else {}


def apply_targeted_repair(
    chapter_num: int,
    scene_num: int,
    fix_directive: str,
    project_root: Optional[str | Path] = None,
) -> dict:
    """Re-generate a single scene with a fix directive.

    Input:  chapter_num, scene_num, fix_directive (str)
    Output: dict with new_scene_text, char_count, old_char_count
    """
    orch = _build_orch_readonly(project_root)
    # Load task card and synopsis
    outline_path = orch.root / "chapters" / "outline" / f"chapter_{chapter_num}.json"
    synopsis_path = orch.root / "chapters" / "synopsis" / f"chapter_{chapter_num}.txt"

    if not outline_path.exists():
        return {"error": f"chapter {chapter_num} outline not found"}

    task_card = json.loads(outline_path.read_text(encoding="utf-8"))
    synopsis_text = ""
    if synopsis_path.exists():
        synopsis_text = synopsis_path.read_text(encoding="utf-8")

    # Find the blueprint for this scene
    bps = task_card.get("scene_blueprints", [])
    bp = None
    for b in bps:
        if b.get("scene_num") == scene_num:
            bp = b
            break

    if bp is None:
        return {"error": f"scene {scene_num} not found in task card"}

    old_text = ""
    journal_path = orch.root / "runtime" / f"chapter_{chapter_num}_scenes.json"
    if journal_path.exists():
        try:
            scenes = json.loads(journal_path.read_text(encoding="utf-8"))
            for s in scenes:
                if s.get("scene_id") == scene_num:
                    old_text = s.get("scene_text", "")
                    break
        except Exception:
            pass

    new_output = orch.writer.generate_scene(
        task_card=task_card,
        scene_blueprint=bp,
        chapter_synopsis=synopsis_text,
        fix_directive=fix_directive,
    )
    new_text = getattr(new_output, "scene_text", "") or ""

    return {
        "scene_num": scene_num,
        "old_char_count": len(old_text),
        "new_char_count": len(new_text),
        "new_scene_text": new_text,
    }


def save_checkpoint(
    chapter_num: int,
    novel_content: str,
    project_root: Optional[str | Path] = None,
) -> dict:
    """Create a checkpoint for the given chapter content.

    Input:  chapter_num, novel_content (str)
    Output: dict with checkpoint_hash, timestamp
    """
    from novel_engine.core.checkpoint import CheckpointManager
    root = Path(project_root) if project_root else Path(__file__).parent.parent
    mgr = CheckpointManager(root)
    cp = mgr.create_checkpoint(
        chapter=chapter_num,
        novel_content=novel_content,
        synopsis_content="",
        outline_content="",
        world_state_snapshot={},
    )
    return {"checkpoint_hash": cp.get("novel_hash", ""), "chapter": chapter_num}
