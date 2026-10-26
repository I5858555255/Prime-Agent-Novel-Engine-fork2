"""Give generation the same project and chapter constraints checked after writing."""
import json
from functools import lru_cache
from pathlib import Path

from novel_engine.quality.scope_gate import _anchor_text, _is_night_bounded


@lru_cache(maxsize=64)
def _read_config(path, modified_ns, size):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def generation_constraints(root, task_card):
    chapter = int(task_card.get("chapter_num", 0) or 0)
    anchor = task_card.get("timeline_anchor") or {}
    lines = ["## 本章生成约束（与成稿质量门保持一致）",
             "本章时间线锚点（硬性遵守）：" + json.dumps(anchor, ensure_ascii=False)]
    characters_path = Path(root) / "memory/world_state/characters.json"
    if characters_path.exists():
        info = characters_path.stat()
        characters = _read_config(str(characters_path.resolve()), info.st_mtime_ns, info.st_size)
        future = {cid: {"name": data.get("name", cid), "first_appearance": data["first_appearance"]}
                  for cid, data in characters.get("characters", {}).items()
                  if int(data.get("first_appearance") or 0) > chapter}
        if future:
            lines.append("本章尚未登场的主要人物（禁止实际参与剧情，不得用化名或普通配角身份代演）："
                         + json.dumps(future, ensure_ascii=False))
            lines.append("人物圣经或下一章任务提及这些姓名，不代表本章允许出场；普通配角不能擅自具名为这些人物。")
    boundary = task_card.get("outline_boundary") or {}
    if boundary.get("current_task"):
        lines.append("本章必须完成的细纲任务：" + str(boundary["current_task"]))
    if boundary.get("next_task"):
        lines.append("下一章尚未发生的核心任务（本章不得提前完成）：" + str(boundary["next_task"]))
        lines.append("人物卡包含长期经历，不代表这些人生事件当前已发生；可留线索，不能提前执行后续章的结果。")
    checks = task_card.get("forbidden_checks") or []
    if checks:
        lines.append("任务卡禁止事项：" + json.dumps(checks, ensure_ascii=False))
    for path in sorted((Path(root) / "config/leak_terms").glob("*.json")):
        info = path.stat()
        cfg = _read_config(str(path.resolve()), info.st_mtime_ns, info.st_size)
        bounds = cfg.get("chapters") if isinstance(cfg, dict) else None
        if not isinstance(bounds, list) or len(bounds) != 2:
            continue
        if not int(bounds[0]) <= chapter <= int(bounds[1]):
            continue
        lines.append("本阶段质量门检查的设定词：" + json.dumps(cfg.get("hard_block") or [], ensure_ascii=False))
        lines.append("不得为扩写或描写身体感受而引入这些设定词；用具体可见动作与感官细节表达任务目标，不得照抄目标中的禁用术语。")
        if cfg.get("_comment"):
            lines.append("阶段适用范围及受控例外（只在对应章节与条件成立时适用）：" + str(cfg["_comment"]))
        if _is_night_bounded(_anchor_text(anchor), cfg):
            lines.append("本章叙事必须停留在规定的当夜时段，不得实际推进到以下天亮意象：" + json.dumps(cfg.get("dawn_markers") or [], ensure_ascii=False))
            lines.append("将来约定与对白中的未来时不等于实际天亮，须明确区分。")
    return "\n".join(lines)


def scene_time_conflicts(root, task_card):
    """Apply the existing configured night bound to planned scene time labels."""
    chapter = int(task_card.get("chapter_num", 0) or 0)
    anchor = task_card.get("timeline_anchor") or {}
    conflicts = []
    for path in sorted((Path(root) / "config/leak_terms").glob("*.json")):
        info = path.stat()
        cfg = _read_config(str(path.resolve()), info.st_mtime_ns, info.st_size)
        bounds = cfg.get("chapters") if isinstance(cfg, dict) else None
        if not isinstance(bounds, list) or len(bounds) != 2 or not int(bounds[0]) <= chapter <= int(bounds[1]):
            continue
        if _is_night_bounded(_anchor_text(anchor), cfg):
            for scene in task_card.get("scene_blueprints") or []:
                time_label = str(scene.get("narrative_time") or "")
                hits = [str(term) for term in cfg.get("dawn_markers") or [] if str(term) and str(term) in time_label]
                if hits:
                    conflicts.append(f"超出章级跨度：场景{scene.get('scene_num')}叙事时间{time_label}与既有当夜门冲突：{hits}")
    return conflicts


def repair_constraints(root, task_card):
    keys = ('scene_num', 'characters', 'narrative_time', 'location', 'goal', 'beats',
            'concrete_events', 'scene_progression_contract', 'scene_constraints', 'word_count_target')
    contract = {'scene_blueprints': [{key: bp[key] for key in keys if key in bp}
                                    for bp in task_card.get('scene_blueprints') or []],
                'foreshadow_actions': task_card.get('foreshadow_actions') or []}
    return (generation_constraints(root, task_card)
            + '\n冻结的修复范围：' + json.dumps(contract, ensure_ascii=False)
            + '\n审稿意见是待核验的修复建议，不是新的故事设定。只修有原文依据的问题；'
              '建议若违反冻结的人物、事件、伏笔、时序或阶段例外，不得执行。'
              '不得为执行建议新增原文与蓝图均无的角色、道具、师承、能力或过去经历。')
