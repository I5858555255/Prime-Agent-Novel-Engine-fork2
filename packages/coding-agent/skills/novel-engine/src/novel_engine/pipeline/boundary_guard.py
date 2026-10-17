# -*- coding: utf-8 -*-
"""Cross-chapter boundary guard (CC round-7 P0-3).

Two-stage, post-assembly / pre-review independent gate that stops a chapter from
re-staging an event the previous chapter already dramatized (the chapter-1→2 "fog
pickup" replay).

Stage 1 (zero LLM): cheap character n-gram containment + char-bigram cosine of the
current chapter against the most recent committed chapter. Only *candidates* above a
deliberately loose threshold are forwarded, so legit callbacks rarely cost a call.

Stage 2 (one LLM, only when Stage 1 fires): judge replay vs legitimate callback with
four criteria. A confirmed replay is repaired via the existing scene-targeted
regeneration path (E), feeding the previous chapter's ``end_state.completed_actions``
as a precise negative example.

Standard library only (no numpy). All IO/LLM is the caller's responsibility so the
scoring + parsing logic stays unit-testable offline.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter

# Loose screening thresholds (CC round-7); widen to 0.15 / 0.7 if false positives.
STAGE1_NGRAM = 10
STAGE1_CONTAINMENT = 0.10
STAGE1_COSINE = 0.60


def _norm(text: str) -> str:
    # Keep CJK + alnum, drop whitespace and punctuation.
    return "".join(ch for ch in (text or "") if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")


def _char_ngrams(text: str, n: int) -> list[str]:
    t = _norm(text)
    return [t[i:i + n] for i in range(len(t) - n + 1)] if len(t) >= n else []


def containment_ratio(cur: str, prev: str, n: int = STAGE1_NGRAM) -> float:
    """Fraction of the current chapter's n-grams already present in the previous one.

    Uses the *current* side as denominator so a short replay inside a normal-length
    chapter still registers (CC's chosen orientation).
    """
    cur_grams = _char_ngrams(cur, n)
    if not cur_grams:
        return 0.0
    prev_set = set(_char_ngrams(prev, n))
    if not prev_set:
        return 0.0
    hits = sum(1 for g in cur_grams if g in prev_set)
    return hits / len(cur_grams)


def bigram_cosine(cur: str, prev: str) -> float:
    """TF-IDF-style cosine over character bigrams (N=2 docs).

    Bigrams shared by both chapters get idf weight 1; chapter-specific bigrams get a
    higher weight (log(3/(df+1))+1 with df=1 → ~1.405), so generic shared vocabulary
    (grammar / recurring names) is down-weighted while verbatim replay stays high.
    """
    a = Counter(_char_ngrams(cur, 2))
    b = Counter(_char_ngrams(prev, 2))
    if not a or not b:
        return 0.0
    idf_common = 1.0
    idf_unique = math.log(3.0 / 2.0 + 1.0)  # df=1 → ~1.405
    dot = sum(a[g] * b[g] * idf_common for g in (set(a) & set(b)))
    na = sum((v * (idf_common if g in b else idf_unique)) ** 2 for g, v in a.items()) ** 0.5
    nb = sum((v * (idf_common if g in a else idf_unique)) ** 2 for g, v in b.items()) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def stage1_score(cur_text: str, prev_text: str) -> dict:
    """Score one (current, previous) pair. ``candidate`` True above loose threshold."""
    cont = containment_ratio(cur_text, prev_text)
    cos = bigram_cosine(cur_text, prev_text)
    return {
        "containment": round(cont, 4),
        "cosine": round(cos, 4),
        "candidate": cont > STAGE1_CONTAINMENT or cos > STAGE1_COSINE,
    }


def scene_overlap_scores(scene_texts: dict[int, str], prev_text: str) -> dict[int, float]:
    """Per-scene Stage1 containment, used to locate the offending scene to regenerate."""
    return {sid: containment_ratio(txt, prev_text) for sid, txt in scene_texts.items()}


def build_stage2_prompt(end_state: dict | None, prev_tail: str, cur_opening: str) -> str:
    """Four-criterion replay judgement prompt. Returns the USER message only."""
    es = end_state if isinstance(end_state, dict) else {}
    completed = "、".join(es.get("completed_actions", []) or []) or "（无）"
    position = str(es.get("narrative_position", "") or "（无）")
    pending = "、".join(es.get("pending_actions", []) or []) or "（无）"
    return f"""请判断新章节开头是否“重演”了上一章已正式演完的事件。只输出 JSON。

上一章结束位置：{position}
上一章已完成动作（不得重新描绘其发生过程）：{completed}
下一章本应从这些动作之后接续：{pending}

【上一章结尾节选】
{prev_tail[-1200:]}

【新章节开头】
{cur_opening[:1600]}

判定标准（四条同时倾向“重演”才判 true）：
1. 复述篇幅：把同一事件的发生过程又详细演了一遍（占新章开头显著篇幅，而非一两句带过）。
2. 信息增量：几乎没有推进新情节，只是换措辞/换视角重述已发生的事。
3. 概括 vs 场景化：是在重新“场景化搬演”动作经过，而非用概括句交代结果/余波。
4. 结果导向：没有从“已完成动作之后”的新时空节点开场（如已进村却又重新进雾捡婴）。
若只是简短回忆、点到为止的回扣或直接承接结果，判 false（合理回扣）。

输出格式（仅 JSON，不要解释）：
{{"replay": true或false, "confidence": 0到1的数, "reason": "一句话依据"}}"""


def parse_stage2_verdict(raw: str) -> dict:
    """Parse the Stage2 JSON; tolerate code fences / surrounding prose via json_repair."""
    text = raw or ""
    fenced = re.search(r"\{.*\}", text, flags=re.S)
    candidate = fenced.group(0) if fenced else text
    data = None
    try:
        data = json.loads(candidate)
    except Exception:
        try:
            import json_repair  # type: ignore
            data = json_repair.loads(candidate)
        except Exception:
            data = None
    if not isinstance(data, dict):
        return {"replay": False, "confidence": 0.0, "reason": "unparseable", "ok": False}
    replay = bool(data.get("replay", False))
    try:
        conf = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return {"replay": replay, "confidence": conf,
            "reason": str(data.get("reason", "")), "ok": True}
