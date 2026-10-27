"""Select current constraints without resending every future volume's full text."""
from __future__ import annotations

import re


def scope_canon(canon, chapter, through):
    scoped = dict(canon)
    parts = re.split(r"(?=^## )", canon["intent"], flags=re.MULTILINE)
    selected, roadmap = [], []
    for part in parts:
        match = re.search(r"chapter_range:\s*\[(\d+),\s*(\d+)\]", part)
        if not match:
            selected.append(part)
            continue
        start, end = map(int, match.groups())
        theme = re.search(r'^\s*theme:\s*(.+)$', part, re.MULTILINE)
        roadmap.append(f"{part.splitlines()[0]} [{start},{end}] {theme[1] if theme else ''}")
        if start <= through and end >= chapter:
            selected.append(part)
    scoped["intent"] = "\n".join(selected)
    scoped["whole_book_roadmap"] = "\n".join(roadmap)
    nodes = sorted(canon["milestones"], key=lambda node: node["chapter_target"])
    before = [node for node in nodes if node["chapter_target"] < chapter][-2:]
    current = [node for node in nodes if chapter <= node["chapter_target"] <= through]
    future = [node for node in nodes if node["chapter_target"] > through][:2]
    scoped["milestones"] = before + current + future
    scoped["scope"] = {"chapter_start": chapter, "chapter_end": through,
                        "full_canon_hash": canon["hash"], "all_foreshadow_windows_included": True}
    return scoped
