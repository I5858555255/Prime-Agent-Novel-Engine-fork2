"""Deterministic contracts for a manuscript intended for external expansion."""
from __future__ import annotations

import hashlib
import json
import re
from difflib import SequenceMatcher
from dataclasses import asdict, dataclass

from .resources.count_han import han_count

GENERIC_ROLE = re.compile(r"(?:村民|村人|村长|村老|捕快|差役|守卫|商人|货郎|妇人|老人|猎户|掌柜|乡亲|人群|邻居)[甲乙丙丁一二三四]?")


def generic_role(name):
    return bool(GENERIC_ROLE.fullmatch(name))


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2)


def digest(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def parse_object(text):
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    result = json.loads(text)
    if not isinstance(result, dict):
        raise ValueError("JSON root must be an object")
    return result


@dataclass(frozen=True)
class ProductionConfig:
    chapters: int = 3800
    total_expanded_han: int = 16_000_000
    expanded_min: int = 4000
    expanded_max: int = 5000
    condensed_reference_min: int = 900
    condensed_reference_max: int = 1400
    plan_batch: int = 10
    repair_rounds: int = 2
    max_calls_per_operation: int = 16
    max_seconds_per_operation: int = 900

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < (0 if key == "repair_rounds" else 1):
                raise ValueError(f"{key} must be a positive integer")
        if not self.chapters * self.expanded_min <= self.total_expanded_han <= self.chapters * self.expanded_max:
            raise ValueError("total expansion budget is outside chapter bounds")
        if self.condensed_reference_min > self.condensed_reference_max:
            raise ValueError("invalid condensed reference range")

    def chapter_budget(self, chapter):
        if not 1 <= chapter <= self.chapters:
            raise ValueError("chapter outside project range")
        return (self.total_expanded_han * chapter // self.chapters
                - self.total_expanded_han * (chapter - 1) // self.chapters)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def strings(value):
    return isinstance(value, list) and bool(value) and all(nonempty(item) for item in value)


def validate_plan(plan, start, end, config, milestones=()):
    errors = []
    entries = plan.get("chapters")
    if not isinstance(entries, list) or not all(isinstance(e, dict) and type(e.get("chapter")) is int for e in entries) or [e.get("chapter") for e in entries] != list(range(start, end + 1)):
        return ["规划必须按顺序完整覆盖指定章节，不能重复、跳章或增加章节"]
    for entry in entries:
        chapter = entry["chapter"]
        required = {node["id"] for node in milestones if node["chapter_target"] == chapter}
        references = entry.get("milestone_ids", [])
        if not isinstance(references, list) or not all(nonempty(ref) for ref in references) or set(references) != required:
            errors.append(f"第{chapter}章必须准确覆盖既定里程碑{sorted(required)}")
        if not nonempty(entry.get("objective")) or not nonempty(entry.get("ending")):
            errors.append(f"第{chapter}章缺少具体任务或结束状态")
        events = entry.get("events")
        if not isinstance(events, list) or not 3 <= len(events) <= 7:
            errors.append(f"第{chapter}章需要3至7个具体事件")
            continue
        ids = []
        for event in events:
            if not isinstance(event, dict) or not all(nonempty(event.get(k)) for k in ("id", "action", "cause", "outcome")):
                errors.append(f"第{chapter}章事件缺少编号、动作、原因或结果")
            else:
                ids.append(event["id"])
        if len(set(ids)) != len(events):
            errors.append(f"第{chapter}章事件编号不唯一")
        if not strings(entry.get("cast")) or not strings(entry.get("boundaries")):
            errors.append(f"第{chapter}章缺少人物或扩写边界")
        if entry.get("expanded_han") != config.chapter_budget(chapter):
            errors.append(f"第{chapter}章扩写预算不匹配")
        if chapter == 1 and re.search(r"产室|产妇|产婆接生", dumps(entry.get("events", []))):
            errors.append("第1章是迷雾边缘出现的弃婴，不可改成村内产妇分娩")
    if errors:
        return errors
    for index, entry in enumerate(entries):
        matched = 0
        for other in entries[index + 1:]:
            if any(SequenceMatcher(None, event.get("action", ""), later.get("action", "")).ratio() > 0.85
                   and SequenceMatcher(None, event.get("outcome", ""), later.get("outcome", "")).ratio() > 0.85
                   for event in entry.get("events", []) if isinstance(event, dict)
                   for later in other.get("events", []) if isinstance(later, dict)):
                matched += 1
        if matched >= 2:
            errors.append(f"从第{entry['chapter']}章起存在至少三章重复事件及结果，必须改为有进展的不同事件")
            break
    return errors


def assign_plan_budgets(plan, config):
    entries = plan.get("chapters", [])
    if not isinstance(entries, list):
        return plan
    for entry in entries:
        if isinstance(entry, dict) and type(entry.get("chapter")) is int and 1 <= entry["chapter"] <= config.chapters:
            entry["expanded_han"] = config.chapter_budget(entry["chapter"])
            events = entry.get("events")
            if isinstance(events, list):
                for index, event in enumerate(events, 1):
                    if isinstance(event, dict):
                        event["id"] = f"CH{entry['chapter']}-E{index}"
                        event.pop("event_ids", None)
    return plan


def assign_scene_budgets(chapter, target):
    scenes = chapter.get("scenes")
    if not isinstance(scenes, list) or not scenes or not all(isinstance(s, dict) for s in scenes) or len(scenes) * 300 > target:
        return chapter
    if all(type(s.get("expanded_han")) is int and s["expanded_han"] >= 300 for s in scenes) and sum(s["expanded_han"] for s in scenes) == target:
        return chapter
    weights = [s.get("expansion_weight", s.get("expanded_han")) for s in scenes]
    if not all(type(weight) is int and weight > 0 for weight in weights):
        return chapter
    total = sum(weights)
    remaining = target - 300 * len(scenes)
    cumulative, allocated = 0, 0
    for scene, weight in zip(scenes, weights, strict=True):
        cumulative += weight
        boundary = remaining * cumulative // total
        scene["expanded_han"] = 300 + boundary - allocated
        scene["expansion_weight"] = weight
        allocated = boundary
    return chapter


def assign_state_preconditions(chapter, state):
    changes = chapter.get("state_changes", [])
    if not isinstance(changes, list):
        return chapter
    for change in changes:
        if isinstance(change, dict) and nonempty(change.get("entity")) and nonempty(change.get("key")):
            change["before"] = state.get((change["entity"], change["key"]))
    return chapter


def validate_chapter(chapter, plan, state, previous_day, known_cast=()):
    errors = []
    if type(chapter.get("chapter")) is not int or chapter.get("chapter") != plan["chapter"] or not nonempty(chapter.get("title")):
        errors.append("章节编号或标题错误")
    day = chapter.get("story_day")
    if isinstance(day, bool) or not isinstance(day, int) or day < previous_day:
        errors.append("故事时间必须为不倒退的整数天；确有成长需标记实际经过天数")
    scenes = chapter.get("scenes")
    if not isinstance(scenes, list) or not 2 <= len(scenes) <= 6:
        return errors + ["需要2至6个完整场景"]
    event_ids = {event["id"] for event in plan["events"]}
    coverage, beats, scene_ids, total = set(), {}, set(), 0
    for scene in scenes:
        if not isinstance(scene, dict):
            errors.append("场景必须是对象")
            continue
        sid = scene.get("id")
        if not nonempty(sid) or sid in scene_ids:
            errors.append("场景编号必须非空且唯一")
        scene_ids.add(str(sid))
        for field in ("location", "time", "pov", "start", "end", "emotion", "expansion_guidance", "text"):
            if not nonempty(scene.get(field)):
                errors.append(f"场景{sid}缺少{field}")
        if not strings(scene.get("cast")) or not strings(scene.get("dialogue_intents")):
            errors.append(f"场景{sid}缺少人物或对话意图（无对白也需注明原因）")
        if strings(scene.get("cast")) and any(name not in plan["cast"] and name not in known_cast and name not in dumps(plan["events"])
                                              and not generic_role(name)
                                              for name in scene["cast"]):
            errors.append(f"场景{sid}出现规划外人物")
        words = scene.get("expanded_han")
        if isinstance(words, bool) or not isinstance(words, int) or words < 300:
            errors.append(f"场景{sid}扩写预算必须至少300汉字")
        else:
            total += words
        items = scene.get("beats")
        if not isinstance(items, list) or not items:
            errors.append(f"场景{sid}缺少具体事件链")
            continue
        for beat in items:
            if not isinstance(beat, dict) or not all(nonempty(beat.get(k)) for k in ("id", "action", "cause", "outcome")):
                errors.append(f"场景{sid}的事件链字段不完整")
                continue
            if beat["id"] in beats:
                errors.append("事件链编号重复")
            beats[beat["id"]] = beat
            refs = beat.get("event_ids")
            if not strings(refs) or not set(refs).issubset(event_ids):
                errors.append(f"场景{sid}事件引用无效")
            else:
                coverage.update(refs)
    if coverage != event_ids:
        errors.append("章节任务中的必要事件未完整覆盖")
    for event in plan["events"]:
        if re.match(r"^(?:几名|一群|部分|一些)?村民", event["action"]):
            actual = "\n".join(beat["action"] + beat["outcome"] for beat in beats.values()
                               if event["id"] in beat.get("event_ids", []))
            if not any(alias in actual for alias in ("村民", "村人", "乡亲", "乡人", "人群")):
                errors.append(f"事件{event['id']}要求村民参与，实际事件却缺少对应参与者，不能只引用编号")
    if total != plan["expanded_han"]:
        errors.append("各场景扩写预算之和必须等于章节预算")
    updates = chapter.get("state_changes")
    if not isinstance(updates, list) or not updates:
        errors.append("缺少本章事实、知识、位置或物品变化记录")
    else:
        touched = set()
        for change in updates:
            if not isinstance(change, dict) or not all(nonempty(change.get(k)) for k in ("entity", "key", "after", "evidence_beat")) or "before" not in change:
                errors.append("状态变化字段不完整")
                continue
            key = (change["entity"], change["key"])
            if key in touched:
                errors.append("同一状态键一章只能提交一次最终变化")
            touched.add(key)
            if change["before"] != state.get(key):
                errors.append(f"状态前值不匹配：{key}，禁止静默改写历史")
            references = [ref.strip() for ref in re.split(r"[,，;；]", change["evidence_beat"])]
            if not references or any(ref not in beats for ref in references):
                errors.append("状态变化缺少有效事件证据")
    if not nonempty(chapter.get("ending")):
        errors.append("缺少章节结束与后章交接")
    return errors


REVIEW_CHECKS = ("canon", "causality", "knowledge", "continuity", "coverage", "foreshadow", "expandability", "boundaries")


def validate_review(review, input_hash, candidate=None, plan=None):
    if not nonempty(input_hash) or review.get("input_hash") != input_hash:
        raise ValueError("review is not bound to the submitted input")
    checks = review.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(REVIEW_CHECKS) or not all(type(v) is bool for v in checks.values()):
        raise ValueError("review must explicitly check all eight contracts")
    issues = review.get("issues")
    if not isinstance(issues, list):
        raise ValueError("review issues must be a list")
    for issue in issues:
        if not isinstance(issue, dict) or issue.get("check") not in REVIEW_CHECKS or not all(nonempty(issue.get(k)) for k in ("location", "evidence", "repair")):
            raise ValueError("each issue requires a contract, location, evidence and minimal repair")
    if any(not v for v in checks.values()) and not issues:
        raise ValueError("failed checks require actionable evidence")
    if all(checks.values()) and issues:
        raise ValueError("passing review cannot have unresolved issues")
    if candidate is not None and plan is not None:
        beats = {beat["id"]: beat for scene in candidate["scenes"] for beat in scene["beats"]}
        events = review.get("event_evidence")
        required = {event["id"] for event in plan["events"]}
        if not isinstance(events, list) or not all(isinstance(item, dict) for item in events) or {item.get("event_id") for item in events} != required or len(events) != len(required):
            raise ValueError("review must audit every required event separately")
        for item in events:
            if type(item.get("covered")) is not bool or not nonempty(item.get("reason")):
                raise ValueError("event evidence requires an explicit finding and comparison reason")
            if item["covered"]:
                refs = item.get("beat_ids")
                if not strings(refs) or any(ref not in beats for ref in refs):
                    raise ValueError("coverage cites nonexistent events")
                evidence = item.get("evidence")
                actual = "\n".join(beats[ref]["action"] for ref in refs)
                if not nonempty(evidence) or evidence not in actual:
                    raise ValueError("coverage evidence must quote an actual action")
                if any(item["event_id"] not in beats[ref]["event_ids"] for ref in refs):
                    raise ValueError("coverage evidence references the wrong required event")
            elif checks["coverage"]:
                raise ValueError("uncovered events cannot pass coverage")
        milestones = review.get("milestone_evidence")
        expected = set(plan.get("milestone_ids", []))
        if not isinstance(milestones, list) or not all(isinstance(item, dict) for item in milestones) or {item.get("milestone_id") for item in milestones} != expected or len(milestones) != len(expected):
            raise ValueError("review must audit every scheduled milestone")
        actual_text = "\n".join(scene["text"] for scene in candidate["scenes"])
        for item in milestones:
            if type(item.get("covered")) is not bool or not nonempty(item.get("reason")):
                raise ValueError("milestone evidence requires a finding and comparison reason")
            if item["covered"] and (not strings(item.get("beat_ids")) or any(ref not in beats for ref in item["beat_ids"])):
                raise ValueError("milestone evidence cites nonexistent events")
            if item["covered"] and (not nonempty(item.get("evidence")) or any(quote not in actual_text for quote in item["evidence"].split("\n"))):
                raise ValueError("milestone evidence must quote the actual brief")
            if not item["covered"] and checks["coverage"]:
                raise ValueError("uncovered milestone cannot pass coverage")
    return all(checks.values())


def bind_review_evidence(review, candidate, plan, milestones):
    beats = {beat["id"]: beat for scene in candidate["scenes"] for beat in scene["beats"]}
    expected_events = {event["id"]: event["action"] for event in plan["events"]}
    expected_nodes = {node["id"]: node["description"] for node in milestones}
    for field, identifier, expected in (("event_evidence", "event_id", expected_events),
                                         ("milestone_evidence", "milestone_id", expected_nodes)):
        items = review.get(field)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            refs = item.get("beat_ids")
            if not strings(refs) or any(ref not in beats for ref in refs):
                continue
            item["model_evidence"] = item.get("evidence")
            item["evidence"] = "\n".join(beats[ref]["action"] for ref in refs)
            item["expected_action"] = expected.get(item.get(identifier))
    return review
