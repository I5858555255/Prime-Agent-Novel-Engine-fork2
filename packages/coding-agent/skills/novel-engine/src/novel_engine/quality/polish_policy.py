"""Select scene edits from deterministic evidence without generating new prose."""
from novel_engine.agents.scene_schema import (
    validate_scene_text, validate_beats_covered, detect_scene_overlap,
)
from novel_engine.core.quality_policy import load_quality_policy, derive_scene_targets
from novel_engine.quality.chinese_gate import is_chinese_clean
from novel_engine.quality.timeline_gate import detect_timeline_jump
from novel_engine.quality.scope_gate import detect_scope_violations


def select_polish_scenes(scenes, task_card, root, targets=None, forbidden=None):
    policy = load_quality_policy(root)
    blueprints = task_card.get("scene_blueprints") or []
    by_id = {int(bp.get("scene_num", 0)): bp for bp in blueprints}
    if targets is None:
        lengths = derive_scene_targets(int(load_quality_policy(root)["chapter_target_chars"]), len(blueprints)) if blueprints else []
        targets = {int(bp.get("scene_num", 0)): n for bp, n in zip(blueprints, lengths)}
    selected = {}
    overlaps = detect_scene_overlap(scenes)
    chapter = int(task_card.get("chapter_num", 0) or 0)
    for scene in scenes:
        sid, text = scene.scene_id, scene.scene_text or ""
        target = int(targets.get(sid, 0) or 0)
        _, reasons = validate_scene_text(text, target)
        floor = int(target * policy["min_ratio"])
        if target and len(text) < floor:
            reasons.append(f"under_target:{len(text)}/{floor}")
        if target and len(text) > int(target * policy["max_ratio"]):
            reasons.append("length_excess")
        if scene.finish_reason == "length":
            reasons.append("finish_reason_length")
        if not is_chinese_clean(text):
            reasons.append("latin_leak")
        if any(marker in text for marker in ("【", "】", "※")):
            reasons.append("scaffolding")
        if scene.structured:
            ok, issue = validate_beats_covered(scene.beats_covered, len(by_id.get(sid, {}).get("beats") or []))
            if not ok:
                reasons.append(issue)
        timeline = detect_timeline_jump(text, task_card.get("timeline_anchor"), chapter)
        if timeline:
            reasons.append(str(timeline))
        for issue in detect_scope_violations(text, chapter, timeline_anchor=task_card.get("timeline_anchor"), root=root).get("hard", []):
            reasons.append(f"scope:{issue.get('term', '')}")
        if forbidden:
            reasons.extend(f"forbidden:{hit.get('name', '')}" for hit in forbidden(text, ""))
        reasons.extend(overlaps.get(sid, []))
        if reasons:
            selected[sid] = list(dict.fromkeys(reasons))
    return selected
