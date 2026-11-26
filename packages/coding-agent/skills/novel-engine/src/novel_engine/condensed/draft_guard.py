"""Generation-time forbidden-term guard for the DRAFT layer.

Root cause (ch5 10-chapter test): the DRAFT model writes stage-forbidden terms
POSITIVELY into titles and beat perception fields ("浊气", "意识到", "频率"),
and REPAIR rounds keep rewriting the same violations or break other schema
constraints. The audit layer then blocks forever.

This module turns the SAME authority the validator enforces
(`accept_check.stage_forbidden_terms` / `perception_forbidden_terms`, driven by
concept_unlocks.json) into an explicit generation-time instruction, so the
model avoids the collision on the first draft instead of paying repair rounds.

Design constraints:
- One shared guard section is injected into EVERY draft/repair round of the
  chapter (generation, duplicate-event regen, structural regen, targeted
  repair) so all candidate rounds see identical instructions; only the
  validator's contract_hash changes, which the draft persistence logic treats
  as "keep history, extend round limit" -- exactly the desired behaviour.
- The block is pure text; it never changes the JSON schema, so committed
  candidates from older contract hashes still validate.
"""
from __future__ import annotations

from .accept_check import perception_forbidden_terms, stage_forbidden_terms
from .contracts import dumps_compact

# Neutral substitutes for the draft perception fields (action/outcome/title/
# expansion_guidance). Mirrors the expansion-layer synonym map but scoped to the
# fields the draft validator scans. A term not listed is left untouched.
_DRAFT_SUBSTITUTES: dict[str, str] = {
    "频率": "节律", "共振": "呼应", "数据": "迹象", "浊气": "浑浊的气息",
    "潮气": "潮湿的气息", "排异": "排斥", "吐纳": "呼吸吐息", "调息": "调匀呼吸",
    "同频": "气息相合", "气感": "气息的微妙", "引气": "引动气息", "丹田": "小腹",
    "周天": "周身流转", "灵力运转": "气息流转", "守护者": "护佑之人", "外门": "外头",
    "意识到": "察觉到", "这意味着": "这表示", "这说明": "这表示", "明白自己": "察觉自己",
}


def scrub_draft_forbidden(candidate: dict, chapter: int) -> list[str]:
    """Deterministically replace stage-forbidden terms in the candidate's
    perception fields (title, scene beats action/outcome, expansion_guidance).

    Only rewrites the *specific* forbidden tokens that appear; leaves cause
    fields (author-layer mechanism) untouched, matching the validator. Returns
    the list of tokens replaced (empty = nothing to scrub).
    """
    from .contracts import _strip_negative_directives
    replaced: list[str] = []
    forbidden = set(perception_forbidden_terms(chapter))

    def scrub_text(text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        # Scan the negative-directive-stripped form so we only touch positive use.
        scanned = _strip_negative_directives(text)
        for token in sorted(forbidden, key=len, reverse=True):
            sub = _DRAFT_SUBSTITUTES.get(token)
            if sub and token in scanned and sub != token:
                text = text.replace(token, sub)
                if token not in replaced:
                    replaced.append(token)
        return text

    if candidate.get("title"):
        candidate["title"] = scrub_text(candidate["title"])
    for scene in candidate.get("scenes", []):
        if not isinstance(scene, dict):
            continue
        if scene.get("expansion_guidance"):
            scene["expansion_guidance"] = scrub_text(scene["expansion_guidance"])
        for beat in scene.get("beats", []):
            if not isinstance(beat, dict):
                continue
            for field in ("action", "outcome"):
                if beat.get(field):
                    beat[field] = scrub_text(beat[field])
    return replaced


def _dedup(terms) -> list[str]:
    """De-duplicate and drop empty terms; short substrings first for readability."""
    out, seen = [], set()
    for term in sorted(set(t for t in terms if isinstance(t, str) and t.strip())):
        key = term.strip()
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def draft_guard(chapter: int, subject: str | None = None) -> str:
    """Build the generation-time forbidden-terms block for `chapter`.

    Returns '' when nothing is forbidden (nothing to add to the prompt).
    `subject` feeds character-restricted concepts (term released only if its
    applying character appears in the beat text); None = wildcard concepts.
    """
    terms = _dedup(stage_forbidden_terms(chapter, subject=subject))
    if not terms:
        return ""
    return (
        "\n\n【认知边界硬约束（本章生成必须遵守）】"
        f"本章（第{chapter}章）尚未解锁以下概念词，标题与 beat 的 action/outcome"
        "（感知层）字段中不得正向出现；需要表达同一含义时，只能用中性观察语言"
        "（如：异样的气味、模糊的感觉、村民的传闻、物证），不得点明机制或名称："
        f"{dumps_compact(terms)}。"
        "beat 的 cause（因果依据）允许保留作者层机制词，须加'据设定'前缀标明非角色已知。"
        "负向指令（如'不命名X'）本身合法，但正文不得因此正向写出X。"
    )


def guard_subjects(candidate: dict | None) -> list[str]:
    """Candidate texts used to probe character-restricted concept unlocks.

    A term whose concept applies only to a named character (e.g. 陆烬's own
    筑基 is locked until ch988 even after 筑基 exists in the world at ch653)
    is released only when that character actually appears in the candidate.
    Feeding the candidate's own text keeps the guard as permissive as the
    validator, which probes per-field.
    """
    if not candidate or not isinstance(candidate, dict):
        return []
    probe = dumps_compact({k: candidate[k] for k in ("title", "scenes", "state_changes", "ending")
                           if k in candidate and candidate[k] is not None})
    return [probe]


def skeleton_lock(previous: dict | None, chapter: int) -> str:
    """Schema-skeleton lock for the full-regen round.

    The regen branch (incoming_issues >= 3) regenerates the whole chapter, and
    the model sometimes parrots the reviewer's schema back or rewrites the
    structural fields. This block freezes the proven skeleton -- chapter
    number, story_day, scene ids, titles, and beat ids -- so the model only
    rewrites the content fields. Without a usable previous candidate it
    returns the minimal chapter-number lock.
    """
    if not previous or not isinstance(previous, dict):
        return (f"\n\n【骨架锁定】重新生成必须保留章节号 {chapter}。"
                "只重写内容字段（事件/因果/结果/状态/对白/扩写指引），不得改变章节号。")
    prev_day = previous.get("story_day")
    prev_scenes = [s.get("id") for s in previous.get("scenes", []) if isinstance(s, dict) and s.get("id")]
    prev_title = previous.get("title")
    prev_beats = []
    for s in previous.get("scenes", []):
        if isinstance(s, dict):
            prev_beats.extend(b.get("id") for b in s.get("beats", []) if isinstance(b, dict) and b.get("id"))
    parts = [f"\n\n【骨架锁定】重新生成必须原样保留以下结构骨架（不得新增/删除/改号），只重写各字段内容："]
    parts.append(f"- chapter = {chapter}")
    if prev_day is not None:
        parts.append(f"- story_day = {prev_day}（不得倒退或改期）")
    if prev_title:
        parts.append(f"- title = \"{prev_title}\"")
    if prev_scenes:
        parts.append("- 场景编号：" + "、".join(str(x) for x in prev_scenes))
    if prev_beats:
        parts.append("- 事件链编号：" + "、".join(str(x) for x in prev_beats))
    parts.append("上述编号内的 action/cause/outcome/对话意图/扩写指引等可按需重写，但编号与结构必须不变。")
    return "\n".join(parts)


def draft_guard_terms_for(candidate: dict | None, chapter: int) -> list[str]:
    """Forbidden terms at `chapter` after releasing what the candidate itself unlocks.

    Mirrors `perception_forbidden_terms(..., subject=probe)` per candidate field,
    so the guard never instructs the model to avoid a term the validator would
    already accept for this candidate.
    """
    base = set()
    for subject in (None,) + tuple(guard_subjects(candidate)):
        base.update(perception_forbidden_terms(chapter, subject=subject))
    return _dedup(base)
