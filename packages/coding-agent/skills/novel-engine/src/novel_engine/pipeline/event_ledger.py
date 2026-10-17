# -*- coding: utf-8 -*-
"""CC P0: append-only cross-chapter event ledger.

The director emits `chapter_events` in the task card (no extra LLM call). Entries
are appended **only** when a chapter reaches the atomic COMMIT gate, so HALT-ed /
isolated chapters never pollute the ledger. The next chapter's director/writer
prompts inject the most recent N chapters' one-line summaries with an explicit
"already happened; reference consequences only, never re-stage" instruction.

Older chapters are intentionally not enumerated (3800-chapter scale); only the
rolling recent window is injected. Storage: one JSON object per line at
runtime/event_ledger.jsonl; writes are atomic (temp + os.replace) and idempotent
on event_id.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

LEDGER_REL = Path("runtime") / "event_ledger.jsonl"
RECENT_WINDOW = 5


def ledger_path(root: str | Path) -> Path:
    return Path(root) / LEDGER_REL


def _s(value, limit: int = 0) -> str:
    s = str(value if value is not None else "").strip().replace("\n", " ")
    if limit and len(s) > limit:
        s = s[:limit]
    return s


def _coerce_event(raw: dict, chapter_num: int, idx: int) -> dict | None:
    summary = _s(raw.get("one_line_summary") or raw.get("summary") or raw.get("goal"))
    if not summary:
        return None
    participants = raw.get("participants") or raw.get("characters") or []
    if not isinstance(participants, list):
        participants = [participants]
    return {
        "event_id": _s(raw.get("event_id")) or f"ch{chapter_num}_ev{idx:02d}",
        "chapter_id": int(chapter_num),
        "event_type": _s(raw.get("event_type") or raw.get("type")) or "event",
        "one_line_summary": _s(summary, 80),
        "participants": [_s(p, 20) for p in participants if _s(p)],
        "location": _s(raw.get("location"), 40),
        "consequence_state": _s(raw.get("consequence_state"), 80),
        "narrative_time": _s(raw.get("narrative_time"), 20),
    }


def build_entries(task_card: dict, chapter_num: int) -> list[dict]:
    """Normalize director-provided chapter_events; deterministically derive from
    scene goals when the model omitted the field (mock / lenient path)."""
    chapter_num = int(chapter_num or 0)
    out: list[dict] = []
    raw_events = task_card.get("chapter_events") if isinstance(task_card, dict) else None
    if isinstance(raw_events, list) and raw_events:
        for i, ev in enumerate(raw_events):
            if isinstance(ev, dict):
                e = _coerce_event(ev, chapter_num, i + 1)
                if e:
                    out.append(e)
    if not out:
        bps = (task_card or {}).get("scene_blueprints") or []
        for i, bp in enumerate(bps[:3]):
            goal = _s((bp or {}).get("goal"))
            if not goal:
                continue
            chars = (bp or {}).get("characters") or []
            out.append({
                "event_id": f"ch{chapter_num}_ev{i + 1:02d}",
                "chapter_id": chapter_num,
                "event_type": "scene_goal",
                "one_line_summary": _s(goal, 80),
                "participants": [_s(c, 20) for c in chars if _s(c)],
                "location": _s((bp or {}).get("location"), 40),
                "consequence_state": "",
                "narrative_time": "",
            })
    # Re-index event_ids so they are unique within the chapter even if model reused ids.
    seen: set[str] = set()
    for i, e in enumerate(out):
        base = f"ch{chapter_num}_ev{i + 1:02d}"
        eid = base
        k = 2
        while eid in seen:
            eid = f"{base}_{k}"
            k += 1
        seen.add(eid)
        e["event_id"] = eid
    return out


def load_entries(root: str | Path) -> list[dict]:
    path = ledger_path(root)
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and rec.get("one_line_summary"):
            entries.append(rec)
    return entries


def append_chapter_events(root: str | Path, chapter_num: int, task_card: dict) -> int:
    """Append this chapter's events atomically and idempotently. Call only at COMMIT.

    Returns the number of new entries written. A whole chapter is appended in one
    temp+os.replace step; entries whose event_id already exists are skipped.
    """
    new_entries = build_entries(task_card, chapter_num)
    if not new_entries:
        return 0
    path = ledger_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_entries(root)
    have_ids = {e.get("event_id") for e in existing}
    fresh = [e for e in new_entries if e.get("event_id") not in have_ids]
    if not fresh:
        return 0
    merged = existing + fresh
    tmp = path.with_suffix(".jsonl.tmp")
    payload = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in merged)
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    return len(fresh)


def recent_event_block(root: str | Path, current_chapter: int, limit: int = RECENT_WINDOW) -> str:
    """Ready-to-inject block of the last `limit` committed chapters' events.

    Only chapters strictly before current_chapter are included. Returns "" when
    there is no ledger history (chapter 1 / fresh project).
    """
    current_chapter = int(current_chapter or 0)
    entries = [e for e in load_entries(root) if int(e.get("chapter_id", 0) or 0) < current_chapter]
    if not entries:
        return ""
    chapter_ids = sorted({int(e.get("chapter_id", 0) or 0) for e in entries})[-limit:]
    keep = set(chapter_ids)
    lines = []
    for e in entries:
        if int(e.get("chapter_id", 0) or 0) not in keep:
            continue
        who = "、".join(e.get("participants", [])[:3])
        head = f"第{e.get('chapter_id')}章·{e.get('event_type','event')}"
        if who:
            head += f"（{who}）"
        lines.append(f"- {head}：{e.get('one_line_summary','')}")
    body = "\n".join(lines)
    return (
        "## 前情事件台账（下列事件均已在前文正式发生过）\n"
        "本章只允许提及其结果、余波或角色后续反应，严禁重新描绘其发生经过、换视角重演或再演一遍；\n"
        "如需承接，只能用概括性语句带过，把笔墨用于推进新事件。\n"
        f"{body}"
    )


# ── CC round-7 P0-3: precise end-state anchor (stronger than the one-line ledger) ──

END_STATE_REL = Path("runtime") / "chapter_end_states.jsonl"


def end_state_path(root: str | Path) -> Path:
    return Path(root) / END_STATE_REL


def _coerce_end_state(raw: dict, chapter_num: int, task_card: dict | None = None) -> dict | None:
    """Normalize a director-provided end_state; deterministically derive a coarse one
    from the last scene goal when the model omitted it (mock / lenient path)."""
    def _lst(v):
        if isinstance(v, list):
            return [_s(x, 60) for x in v if _s(x)]
        s = _s(v, 200)
        return [s] if s else []

    es = raw if isinstance(raw, dict) else {}
    position = _s(es.get("narrative_position"), 200)
    completed = _lst(es.get("completed_actions"))
    pending = _lst(es.get("pending_actions"))
    location = _s(es.get("location"), 40)
    time_marker = _s(es.get("time_marker"), 20)

    # Fallback: derive from the final scene blueprint so an anchor always exists.
    if not position or not completed:
        bps = (task_card or {}).get("scene_blueprints") or []
        if bps:
            last = bps[-1] or {}
            goal = _s(last.get("goal"), 200)
            if not position and goal:
                position = goal
            if not completed and goal:
                completed = [goal]
    if not position and not completed:
        return None
    return {
        "chapter_id": int(chapter_num),
        "narrative_position": position,
        "location": location,
        "completed_actions": completed,
        "pending_actions": pending,
        "time_marker": time_marker,
    }


def load_end_states(root: str | Path) -> list[dict]:
    path = end_state_path(root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(rec, dict) and (rec.get("narrative_position") or rec.get("completed_actions")):
            out.append(rec)
    return out


def append_end_state(root: str | Path, chapter_num: int, task_card: dict) -> bool:
    """Persist this chapter's end_state atomically at COMMIT. Idempotent per chapter."""
    es = _coerce_end_state((task_card or {}).get("end_state") or {}, chapter_num, task_card)
    if not es:
        return False
    path = end_state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = [e for e in load_end_states(root) if int(e.get("chapter_id", 0) or 0) != int(chapter_num)]
    existing.append(es)
    existing.sort(key=lambda e: int(e.get("chapter_id", 0) or 0))
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in existing), encoding="utf-8")
    os.replace(tmp, path)
    return True


def latest_end_state(root: str | Path, current_chapter: int) -> dict | None:
    """End_state of the greatest committed chapter strictly before current_chapter."""
    current_chapter = int(current_chapter or 0)
    prior = [e for e in load_end_states(root) if int(e.get("chapter_id", 0) or 0) < current_chapter]
    if not prior:
        return None
    return max(prior, key=lambda e: int(e.get("chapter_id", 0) or 0))


def end_state_anchor_block(root: str | Path, current_chapter: int) -> str:
    """Ready-to-inject strict opening anchor for the DIRECTOR prompt (CC wording)."""
    es = latest_end_state(root, current_chapter)
    if not es:
        return ""
    completed = "、".join(es.get("completed_actions", []) or [])
    pending = "、".join(es.get("pending_actions", []) or []) or "紧接其后的新情节"
    position = es.get("narrative_position", "")
    return (
        "## 上一章精确停点（本章必须从此之后接续，严禁倒回重演）\n"
        f"上一章结束于：{position}。\n"
        f"已完成动作：{completed}（禁止重新描绘这些动作的发生过程、换视角再演或倒带）。\n"
        f"本章第一个场景必须从“{pending}”或更靠后的情节开始，"
        "不得早于上一章结束的时间/空间节点；开场不要再回到这些动作发生之前。\n"
    )
