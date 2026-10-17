"""DEPRECATED — MemoryRetriever — chapter-level consistency checker (P0-2).

Reads the chapter journal (with entities_json injected by P0-1) and the
plot_graph.json node list to produce:
  • facts      : list of {subject, predicate, object, confidence, evidence}
  • timeline   : list of {story_time, duration_minutes, location_id, participants}
  • foreshadows: list of {node_id, description, chapter_target, unresolved: True}

All returned items are "unresolved" by default; a downstream promoter may
promote selected facts/links to ``confirmed`` after manual/reviewer sign-off.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

NOVEL_ROOT = Path(__file__).resolve().parents[1]

CHAPTERS_DIR = NOVEL_ROOT / "chapters"
# Fixed: correct path to plot_graph.json (was duplicated path before)
PLOT_GRAPH_PATH = NOVEL_ROOT / "config" / "planning" / "plot_graph.json"

# Module-level candidate list, loaded once at import (CJK 2-6 char phrases only)
_PLOT_ENTITY_CANDIDATES = None


def _load_journal_chapter(root: Path, chapter: int) -> list[dict]:
    """Load journal scenes for *chapter* using the P0-1 enhanced loader."""
    from novel_engine.pipeline.chapter_journal import load_scenes
    return load_scenes(root, chapter)


def _load_plot_graph() -> Dict[str, Any]:
    """Load the global plot_graph once."""
    if PLOT_GRAPH_PATH.exists():
        with PLOT_GRAPH_PATH.open(encoding="utf-8") as f:
            return json.load(f)
    return {"nodes": []}


def _load_plot_nodes() -> list:
    """Load plot_graph.json nodes and extract entity candidates (CJK 2-6 chars).

    Filters out long descriptive phrases (8+ chars) to keep person/name/location
    granularity (e.g., "陆烬", "陈老根", "雾隐村"). Only phrases 2-6 characters
    long are kept as candidates for substring matching against scene_text.
    """
    global _PLOT_ENTITY_CANDIDATES
    if _PLOT_ENTITY_CANDIDATES is not None:
        return _PLOT_ENTITY_CANDIDATES
    
    import json
    plot_graph_path = Path(
        r"D:\AI\Prime-Agent-Novel-Engine\Prime-Agent-Novel-Engine\packages\coding-agent\skills\novel-engine"
    ) / "src" / "novel_engine" / "config" / "planning" / "plot_graph.json"
    if plot_graph_path.exists():
        with plot_graph_path.open(encoding="utf-8") as f:
            data = json.load(f)
        nodes = data.get("nodes", [])
        candidates = []
        for node in nodes:
            desc = node.get("description", "")
            # Only keep CJK sequences 2-6 characters long (person/name/location scope)
            # Discard longer phrases which are likely descriptive sentences
            cjk_phrases = re.findall(r'[\u4e00-\u9fff]{2,6}', desc)
            for phrase in cjk_phrases:
                if phrase not in candidates:
                    candidates.append(phrase)
        _PLOT_ENTITY_CANDIDATES = candidates
        return candidates
    return []


def _get_scene_entities_json(scene: dict, chapter: int, root: Path, plot_nodes: list) -> list:
    """Get entities_json for a scene, with real-time fallback if field missing.
    
    If scene has entities_json (new format), use it directly.
    If not (old journal format), call _make_entities_json with plot_nodes
    to inject entities from plot graph candidates.
    """
    entities = scene.get("entities_json")
    if entities:
        # New format: return as-is (ensure it's a list)
        try:
            parsed = json.loads(entities) if isinstance(entities, str) else entities
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        # Fall through to real-time extraction
    
    # Old format or corrupted: real-time extraction from plot graph candidates
    from novel_engine.pipeline.chapter_journal import _make_entities_json
    # Pass plot_nodes for candidate matching
    return _make_entities_json(scene, plot_nodes)


def _extract_facts_from_scene(scene: dict, chapter_idx: int, root: Path, plot_nodes: list) -> List[Dict[str, Any]]:
    """Very simple fact extraction from a single scene dict (with entities_json).
    
    If scene lacks entities_json, performs real-time extraction from plot graph
    candidates to ensure facts are never empty for old-format journals.
    """
    facts = []
    # Use the enhanced getter that handles old/new journal formats
    entities = _get_scene_entities_json(scene, chapter_idx, root, plot_nodes)
    
    if not entities:
        return facts

    for ent in entities:
        name = ent.get("canonical_name", "")
        if not name:
            continue
        # Very naive confidence: promotion candidate if beats non-empty else baseline
        beats = scene.get("beats", [])
        confidence = 0.8 if beats else 0.5
        facts.append(
            {
                "subject": name,
                "predicate": "appears_in",
                "object": f"chapter_{chapter_idx}",
                "confidence": confidence,
                "evidence": f"scene_id={scene.get('scene_id')}, beats={len(beats)}",
            }
        )
    return facts


def _extract_timeline_from_plot(nodes: List[Dict[str, Any]], chapter: int) -> List[Dict[str, Any]]:
    """Pull timeline-relevant nodes that match the chapter or earlier.
    
    Relaxed matching: returns nodes with chapter_target <= chapter (本章及之前
    已发生的剧情节点),按 chapter_target 升序返回更多结果。
    """
    timeline = []
    # Collect matching nodes
    matching = [n for n in nodes if n.get("chapter_target", 999) <= chapter]
    # Sort by chapter_target ascending for chronological order
    matching.sort(key=lambda n: n.get("chapter_target", 999))
    for node in matching:
        description = node.get("description", "")
        timeline.append(
            {
                "story_time": description[:20] if description else "",
                "duration_minutes": 0,
                "location_id": f"vill_{node.get('chapter_target', chapter)}",
                "participants": [],
                "summary": description,
            }
        )
    return timeline


def _extract_unresolved_foreshadows(nodes: List[Dict[str, Any]], chapter: int) -> List[Dict[str, Any]]:
    """Return foreshadow nodes that have foreshadow_links and are relevant to current chapter.
    
    Only returns nodes with chapter_target >= current chapter (未兑现的伏笔),
    and truncates to front 5 entries to avoid noise.
    """
    result = []
    for node in nodes:
        # Only include foreshadow nodes relevant to current/earlier chapters
        # and truncate to first 5 to avoid noise
        if node.get("chapter_target", 0) >= chapter:
            links = node.get("foreshadow_links", [])
            if links:
                result.append(
                    {
                        "node_id": node.get("id"),
                        "description": node.get("description", ""),
                        "chapter_target": node.get("chapter_target"),
                        "unresolved": True,
                        "evidence_links": links,
                    }
                )
                # Stop after 5 entries to limit noise
                if len(result) >= 5:
                    break
    return result


def retrieve(chapter: int, root: Path = CHAPTERS_DIR) -> Dict[str, Any]:
    """Main entry point for P0-2.

    Returns a dict with three keys:
        "facts"       : list[dict]   — expected after data accumulation
        "timeline"    : list[dict]   — expected after data accumulation
        "foreshadows" : list[dict]   — expected after data accumulation
    """
    # Bug Fix 1: Pre-load plot_nodes and cache candidates
    plot_nodes = _load_plot_nodes()
    
    journal_scenes = _load_journal_chapter(root, chapter)
    plot_graph = _load_plot_graph()
    nodes = plot_graph.get("nodes", [])

    # 1) Facts — accumulate from all scenes (with real-time fallback for old journals)
    all_facts: List[Dict[str, Any]] = []
    for sc in journal_scenes:
        all_facts.extend(_extract_facts_from_scene(sc, chapter, root, plot_nodes))

    # De-duplicate by (subject, object)
    seen = set()
    facts = []
    for f in all_facts:
        key = (f["subject"], f["object"])
        if key not in seen:
            seen.add(key)
            facts.append(f)

    # 2) Timeline — from plot_graph nodes matching chapter or earlier
    timeline = _extract_timeline_from_plot(nodes, chapter)

    # 3) Unresolved foreshadows — relevant to current chapter, limited to 5
    foreshadows = _extract_unresolved_foreshadows(nodes, chapter)

    return {"facts": facts, "timeline": timeline, "foreshadows": foreshadows}


# Convenience helper: produce a short ContinuityBrief string from the retrieval result
def compose_continuity_brief(result: Dict[str, Any], chapter: int) -> str:
    lines = [
        f"ContinuityBrief — Chapter {chapter}",
        f"  Facts captured: {len(result['facts'])}",
        f"  Timeline entries: {len(result['timeline'])}",
        f"  Unresolved foreshadow links: {len(result['foreshadows'])}",
    ]
    # --- Time range from timeline entries ---
    times = [t.get("story_time", "") for t in result["timeline"] if t.get("story_time")]
    if times:
        # Very naive: just list the first and last time strings found
        time_range = f"Time span: {times[0]} → {times[-1]}"
        lines.append(f"  {time_range}")
    else:
        lines.append("  Time span: not determined")
    # --- Potential conflict detection ---
    # Heuristic 1: same canonical_name appears in multiple facts with different confidence
    name_conflicts = {}
    for f in result["facts"]:
        name = f.get("subject", "")
        if not name:
            continue
        if name not in name_conflicts:
            name_conflicts[name] = []
        name_conflicts[name].append(f.get("confidence"))
    conflicts = []
    for name, confs in name_conflicts.items():
        if len(confs) > 1 and len(set(confs)) > 1:  # same entity, different confidence levels
            conflicts.append(f"[POTENTIAL_CONFLICT] entity '{name}' has mixed confidence levels: {confs}")
    if conflicts:
        for c in conflicts:
            lines.append(c)
    else:
        lines.append("No conflicts detected")
    # List first few facts
    for f in result["facts"][:3]:
        lines.append(f"    - {f['subject']} {f['predicate']} {f['object']} (conf={f['confidence']})")
    # List first few foreshadow nodes
    for fg in result["foreshadows"][:3]:
        lines.append(f"    ? {fg['node_id']}: {fg['description']} (ch {fg['chapter_target']})")
    return "\n".join(lines)