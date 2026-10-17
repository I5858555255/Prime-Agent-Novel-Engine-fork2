# -*- coding: utf-8 -*-
"""CC round-16 P0-2：相邻场景边界“事件重演”门（零 LLM）。

抓的是有明确位置结构的现象：下一场开头复述上一场结尾刚演完的事件（如两场连演同一番村民
争吵）。取上一场末 N 句与下一场首 N 句的“动作/事件实词”集合做 Jaccard；剔除环境氛围词、
人名/身份、功能字后，重合率超阈且下一场首窗口不含“推进词”即判重演。
"""
from __future__ import annotations

import json
from pathlib import Path

from .cross_scene_repeat_gate import split_sentences, _sid_text

_DEFAULT = {
    "boundary_sentences": 4,
    "min_next_head_sentences": 3,
    "overlap_ratio": 0.45,
    "progression_markers": [],
    "boilerplate_terms": [],
}
# 仅保留动作动词与事件相关名词（CC：不要环境/人名/功能字）
_KEEP_POS = {"v", "vd", "vn", "vg", "n", "nz", "i"}
_DROP_POS1 = {"u", "p", "c", "d", "r", "m", "q", "a", "ad", "an", "f", "s", "t", "nr", "ns",
              "nt", "e", "y", "o", "w", "x", "k", "l", "z", "eng"}


def load_boundary_config(root: str | Path) -> dict:
    cfg = dict(_DEFAULT)
    fp = Path(root) / "config" / "boundary_reprise.json"
    if fp.exists():
        try:
            cfg.update(json.loads(fp.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def extract_action_event_terms(text: str, cfg: dict, exclude: set[str] | None = None) -> set[str]:
    boiler = set(cfg.get("boilerplate_terms", []) or []) | set(exclude or set())
    terms: set[str] = set()
    try:
        from .density_gate import _posseg
        tagged = _posseg().cut(text or "")
    except Exception:
        return terms
    for w, flag in tagged:
        w = w.strip()
        if len(w) < 2:
            continue
        # jieba 词性：v* 动词，n/nz/i 具体/事件名词；排除名地名(nr/ns)与虚词
        if flag in _DROP_POS1:
            continue
        if flag and flag[0] in ("u", "p", "c", "r", "d", "m", "q", "a"):
            continue
        if flag not in _KEEP_POS:
            continue
        if w in boiler:
            continue
        terms.add(w)
    return terms


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_boundary_reprise(prev_text: str, next_text: str, cfg: dict,
                            exclude_terms: set[str] | None = None) -> dict:
    n_tail = int(cfg.get("boundary_sentences", 4))
    n_head = int(cfg.get("boundary_sentences", 4))
    min_head = int(cfg.get("min_next_head_sentences", 3))
    thr = float(cfg.get("overlap_ratio", 0.45))
    markers = cfg.get("progression_markers", []) or []

    prev_s = split_sentences(prev_text or "")
    next_s = split_sentences(next_text or "")
    if len(next_s) < min_head:
        return {"is_reprise": False, "overlap_ratio": 0.0, "reason": "next_head_too_short"}

    tail = " ".join(prev_s[-n_tail:])
    head = " ".join(next_s[:n_head])
    a = extract_action_event_terms(tail, cfg, exclude_terms)
    b = extract_action_event_terms(head, cfg, exclude_terms)
    ratio = _jaccard(a, b)
    has_progression = any(m and m in head for m in markers)
    is_reprise = (ratio + 1e-9 >= thr) and not has_progression
    return {
        "is_reprise": bool(is_reprise),
        "overlap_ratio": round(ratio, 3),
        "has_progression": bool(has_progression),
        "shared": sorted(a & b),
        "tail_terms": sorted(a),
        "head_terms": sorted(b),
    }


def detect_chapter_boundary_reprises(scenes: list, cfg: dict, task_card: dict | None = None) -> list[dict]:
    """对相邻场景顺序（按 scene_id 升序）逐对检测。仅报告，删除由编排层按 sid 处理。"""
    ordered = sorted(scenes, key=lambda s: _sid_text(s)[0])
    out = []
    for k in range(len(ordered) - 1):
        sid1, t1 = _sid_text(ordered[k])
        sid2, t2 = _sid_text(ordered[k + 1])
        r = detect_boundary_reprise(t1, t2, cfg)
        if r.get("is_reprise"):
            out.append({"prev_scene": sid1, "next_scene": sid2, **r})
    return out


def excise_head_reprise(next_text: str, cfg: dict) -> str:
    """删除下一场开头的重演窗口（首 N 句），保留其后的推进内容，按段重拼。"""
    n_head = int(cfg.get("boundary_sentences", 4))
    sents = split_sentences(next_text or "")
    if len(sents) <= n_head:
        return next_text  # 删完就空了，交给定点重生而非确定性删
    return "".join(sents[n_head:]).lstrip("\n").strip()
