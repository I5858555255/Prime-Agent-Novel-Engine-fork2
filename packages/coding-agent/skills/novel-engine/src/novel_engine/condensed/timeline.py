"""A single day ledger derived from scene transitions, not a model's chapter counter."""
from __future__ import annotations

import re

PHASES = {"凌晨": 0, "黎明": 1, "清晨": 2, "早晨": 2, "上午": 3, "白昼": 3, "白天": 3,
          "午间": 4, "正午": 4, "午后": 5, "下午": 5, "黄昏": 6, "傍晚": 6, "夜晚": 7,
          "夜间": 7, "夜": 7, "深夜": 8, "天亮": 2, "当晚": 7, "晚间": 7, "早上": 2,
          "子时": 8, "丑时": 0, "寅时": 1, "卯时": 2, "辰时": 3, "巳时": 3,
          "午时": 4, "未时": 5, "申时": 5, "酉时": 6, "戌时": 7, "亥时": 8}
NUMBER = r"[零〇一二两三四五六七八九十百千\d]+"


def chinese_number(value):
    if value.isdigit():
        return int(value)
    digits = dict(zip("零一二三四五六七八九", range(10))) | {"两": 2, "〇": 0}
    total, current = 0, 0
    for char in value:
        if char in digits:
            current = digits[char]
        elif char in "十百千":
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[char]
            current = 0
        else:
            raise ValueError("unsupported Chinese numeral")
    return total + current


def phase(text):
    matches = list(re.finditer("|".join(sorted(PHASES, key=len, reverse=True)), text or ""))
    return matches[-1].group() if matches else "白天"


def start_phase(text):
    match = re.search("|".join(sorted(PHASES, key=len, reverse=True)), text or "")
    return match.group() if match else "白天"


def elapsed_days(text):
    values = []
    for number, unit in re.findall(rf"(?:经过|历时|持续|耗时|花了)(?:约|了)?({NUMBER})(天|日|个月|月|年)", text or ""):
        values.append(chinese_number(number) * {"天": 1, "日": 1, "个月": 30, "月": 30, "年": 365}[unit])
    if "至天亮" in (text or "") and ("夜" in text):
        values.append(1)
    return max(values, default=0)


def age_floor(text, protagonist):
    escaped = re.escape(protagonist)
    floors = []
    lead = r"(?:^|[。；;\n])\s*"
    if re.search(lead + escaped + r"(?:已经|已|刚刚|刚)?满月", text):
        floors.append(30)
    patterns = [rf"{lead}{escaped}(?:已经|已|刚刚|刚)?(?:年满|满|现年|年仅)({NUMBER})(岁|个月|月)",
                rf"{lead}({NUMBER})(岁|个月|月)(?:大)?的{escaped}"]
    for pattern in patterns:
        for number, unit in re.findall(pattern, text):
            floors.append(chinese_number(number) * (365 if unit == "岁" else 30))
    return max(floors, default=0)


def normalize_timeline(chapter, previous_day, previous_phase="白天", protagonist="陆烬", plan=None):
    scenes = chapter.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return chapter
    now, prior_phase = previous_day, previous_phase
    initial = previous_day
    proof = []
    applied_plan_age = False
    for index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        original = scene.get("time", "")
        if not isinstance(original, str):
            original = ""
        part = scene.get("daypart")
        if not isinstance(part, str) or part not in PHASES:
            part = start_phase(original)
        end_part = scene.get("end_daypart")
        if not isinstance(end_part, str) or end_part not in PHASES:
            end_part = phase(original) if any(word in original for word in PHASES) else part
        scope = scene.get("time_scope", "present")
        if scope == "flashback":
            scene.update(start_day=now, end_day=now, gap_days=0, duration_days=0, daypart=part, end_daypart=end_part,
                         time=f"回忆（主线第{now}天，{part}）")
            continue
        gap = scene.get("gap_days")
        duration = scene.get("duration_days")
        if type(gap) is not int or gap < 0:
            gap = 1 if "次日" in original or "翌日" in original else 0
        jump = re.search(rf"({NUMBER})(天|日|个月|月|年)(?:之后|后)", original)
        if jump:
            gap = max(gap, chinese_number(jump[1]) * {"天": 1, "日": 1, "个月": 30, "月": 30, "年": 365}[jump[2]])
        if type(duration) is not int or duration < 0:
            duration = elapsed_days(original)
        if gap == 0 and PHASES[part] < PHASES.get(prior_phase, 3):
            gap = 1
        ordinal = re.search(rf"第({NUMBER})(?:天|日)", original) if "第" in original and "主线第" not in original else None
        ordinal_floor = initial + chinese_number(ordinal[1]) - 1 if ordinal else 0
        start_text = scene.get("start") if isinstance(scene.get("start"), str) else ""
        source_beats = scene.get("beats") if isinstance(scene.get("beats"), list) else []
        actual = start_text + "\n" + "\n".join(
            beat.get("action", "") for beat in source_beats if isinstance(beat, dict) and isinstance(beat.get("action", ""), str))
        floor = age_floor(actual, protagonist)
        if not applied_plan_age and plan:
            floor = max(floor, age_floor("\n".join(event["action"] for event in plan["events"]), protagonist))
        applied_plan_age = True
        start = max(now + gap, ordinal_floor, floor)
        duration = max(duration, elapsed_days(original))
        if PHASES[end_part] < PHASES[part] and duration == 0:
            duration = 1
        scene.update(start_day=start, end_day=start + duration, gap_days=start - now,
                     duration_days=duration, daypart=part, end_daypart=end_part,
                     time=f"主线第{start}天{part}" + (f"至第{start + duration}天{end_part}" if duration or end_part != part else ""))
        proof.append({"scene": scene.get("id"), "age_floor_days": floor,
                      "start_day": start, "end_day": start + duration})
        now, prior_phase = start + duration, end_part
    chapter["story_day"] = now
    chapter["timeline_proof"] = {"previous_end_day": previous_day, "scenes": proof,
                                 "ending_daypart": prior_phase,
                                 "age_conversion": "岁按365日、月按30日估算下限，不新增世界历法设定"}
    return chapter


def validate_timeline(chapter, previous_day):
    cursor = previous_day
    scenes = chapter.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        return ["场景日程必须是非空数组"]
    for scene in scenes:
        if not isinstance(scene, dict):
            return ["场景日程必须是对象，不能使用字符串占位"]
        start, end = scene.get("start_day"), scene.get("end_day")
        if type(start) is not int or type(end) is not int or start < cursor or end < start:
            return ["场景绝对时间缺失、倒退或持续时长非法"]
        if type(scene.get("duration_days")) is not int or type(scene.get("gap_days")) is not int or scene.get("duration_days") != end - start or scene.get("gap_days") != start - cursor:
            return ["场景间隔或持续天数与绝对时间不一致"]
        cursor = end
    return [] if chapter.get("story_day") == cursor else ["章节结束日必须等于最后场景结束日"]
