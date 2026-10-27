"""Render the brief from its events so the readable version cannot omit card facts."""
from __future__ import annotations

import copy


def generation_view(chapter):
    value = copy.deepcopy(chapter)
    value.pop("timeline_proof", None)
    value.pop("story_day", None)
    for scene in value.get("scenes", []):
        if isinstance(scene, dict):
            if "expansion_weight" not in scene and isinstance(scene.get("expanded_han"), int):
                scene["expansion_weight"] = scene["expanded_han"]
            scene.pop("expanded_han", None)
            for key in ("text", "time", "start_day", "end_day"):
                scene.pop(key, None)
    return value


def render_brief(chapter):
    scenes = chapter.get("scenes", [])
    if not isinstance(scenes, list):
        return chapter
    for scene in scenes:
        if not isinstance(scene, dict) or not isinstance(scene.get("beats"), list):
            continue
        sentences = []
        if all(isinstance(scene.get(key), str) for key in ("location", "time", "pov")):
            sentences.append(f"场景：{scene['location']}；{scene['time']}；视角：{scene['pov']}")
        if isinstance(scene.get("start"), str):
            sentences.append("起点：" + scene["start"])
        for beat in scene["beats"]:
            if not isinstance(beat, dict):
                continue
            for key, label in (("action", "事件"), ("cause", "因果依据（作者资料，须遵守角色认知边界）"), ("outcome", "结果")):
                if isinstance(beat.get(key), str):
                    sentences.append(label + "：" + beat[key])
        if isinstance(scene.get("dialogue_intents"), list):
            sentences.extend("对话意图：" + item for item in scene["dialogue_intents"] if isinstance(item, str))
        if isinstance(scene.get("emotion"), str):
            sentences.append("情绪转折：" + scene["emotion"])
        if isinstance(scene.get("end"), str):
            sentences.append("终点：" + scene["end"])
        if isinstance(scene.get("expanded_han"), int):
            sentences.append(f"扩写预算：{scene['expanded_han']}汉字")
        if isinstance(scene.get("expansion_guidance"), str):
            sentences.append("扩写重点：" + scene["expansion_guidance"])
        scene["text"] = "\n".join(sentences)
    return chapter
