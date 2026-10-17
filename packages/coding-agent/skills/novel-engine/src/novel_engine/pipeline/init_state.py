#!/usr/bin/env python3
"""
init_state.py — 初始化 characters.json 和 relationships 数据

从 constraints.json 和 character_bible.md 生成完整的 world_state 数据，
确保 StateDB 和 WorldSimulator 有确定的事实基础。
"""
import json
from pathlib import Path


CHARACTER_DEFS = {
    "C001": {
        "name": "陆烬",
        "realm": "凡人体质",
        "location": "验证营地",
        "description": "地球孤儿李淳神魂跨界重生，雾隐村弃婴，陈老根养子。性格谨慎、隐忍、算计缜密。线粒体源自地球，可吸收暴走活化氧；后修《太上养魂经》。成长弧光：孤儿→凡人武者→修仙者→人道之主，主动退场传承。",
        "ability": "线粒体源自地球，可吸收暴走活化氧；后修《太上养魂经》",
        "arc": "孤儿→凡人武者→修仙者→人道之主，主动退场传承",
        "taboos": ["不冒进", "使用人皇信物必权衡代价"],
        "faction_id": "",
        "first_appearance": 1,
    },
    "C002": {
        "name": "陈老根",
        "realm": "凡人体质（深藏不露）",
        "location": "雾隐村",
        "description": "雾隐村废弃武者，拾陆烬归家，'有秘密的观察者'。成长弧光：普通养父→展露深藏武艺→为陆烬铺路离村。",
        "ability": "",
        "arc": "普通养父→展露深藏武艺→为陆烬铺路离村",
        "taboos": [],
        "faction_id": "",
        "first_appearance": 2,
    },
    "C003": {
        "name": "沈知微",
        "realm": "炼气期",
        "location": "未知",
        "description": "太虚仙门内门弟子，陆烬道友，沉静锐利。成长弧光：历练同盟→'加速主义'改革理念分歧但不分裂。",
        "ability": "",
        "arc": "历练同盟→'加速主义'改革理念分歧但不分裂",
        "taboos": [],
        "faction_id": "太虚仙门",
        "first_appearance": 688,
    },
    "C004": {
        "name": "上官烈",
        "realm": "炼气期",
        "location": "未知",
        "description": "太虚仙门藏锋峰弟子，出身重要脉系，言辞倨傲。成长弧光：正面冲突对手→战友。",
        "ability": "",
        "arc": "正面冲突对手→战友",
        "taboos": [],
        "faction_id": "太虚仙门",
        "first_appearance": 684,
    },
    "C005": {
        "name": "柳踏歌",
        "realm": "未知",
        "location": "未知",
        "description": "引路散修，执事堂外围协理，身份模糊需谨慎。",
        "ability": "",
        "arc": "",
        "taboos": [],
        "faction_id": "",
        "first_appearance": 714,
    },
    "C006": {
        "name": "苏问魂",
        "realm": "筑基期（寿元将尽）",
        "location": "未知",
        "description": "寿元将尽的魂修天才，因救妻失败落魄，《太上养魂经》来源，传功条件苛刻。",
        "ability": "魂修",
        "arc": "",
        "taboos": [],
        "faction_id": "",
        "first_appearance": 890,
    },
    "C007": {
        "name": "乾武帝",
        "realm": "化神期",
        "location": "大乾皇京",
        "description": "大乾皇朝皇帝，隐忍算计，借陆烬完成皇室赎罪。",
        "ability": "",
        "arc": "",
        "taboos": [],
        "faction_id": "大乾皇朝",
        "first_appearance": 387,
    },
    "C008": {
        "name": "荒尾",
        "realm": "未知",
        "location": "未知",
        "description": "妖族代表，与陆烬信任建立需经'试探→交换→验证'三阶段，不可一步到位透露妖族立场。",
        "ability": "",
        "arc": "",
        "taboos": [],
        "faction_id": "妖族",
        "first_appearance": 1063,
    },
}

RELATIONSHIPS = {
    ("C001", "C002"): {"type": "养父子", "trigger": "第60章：展露深藏武艺", "affinity": 90},
    ("C001", "C003"): {"type": "道友", "trigger": "第1580章：改革理念之争", "affinity": 70},
    ("C001", "C004"): {"type": "对手", "trigger": "第687章：沈知微解围", "affinity": 30},
    ("C001", "C005"): {"type": "引路人", "trigger": "第714章起：身份渐模糊", "affinity": 50},
    ("C001", "C006"): {"type": "传功缘分", "trigger": "第989章：获传承", "affinity": 60},
    ("C001", "C007"): {"type": "暗中盟友", "trigger": "第567-569章：真相揭示", "affinity": 75},
}


def init_characters(root: Path) -> dict:
    """主初始化函数。"""
    output_path = root / "memory" / "world_state" / "characters.json"
    rel_output_path = root / "memory" / "world_state" / "relationships.json"

    characters = {"characters": {}}
    for char_id, data in CHARACTER_DEFS.items():
        characters["characters"][char_id] = {
            "name": data["name"],
            "realm": data["realm"],
            "location": data["location"],
            "description": data["description"],
            "ability": data.get("ability", ""),
            "arc": data.get("arc", ""),
            "taboos": data.get("taboos", []),
            "faction_id": data.get("faction_id", ""),
            "first_appearance": data.get("first_appearance", 0),
            "relationships": {},
        }

    # 填充关系
    for (c1, c2), rel in RELATIONSHIPS.items():
        if c1 in characters["characters"]:
            characters["characters"][c1]["relationships"][c2] = rel

    # 写入 characters.json
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(characters, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写入 relationships.json（独立文件便于快速读取）
    rel_data = {"relationships": []}
    for (c1, c2), rel in RELATIONSHIPS.items():
        rel_data["relationships"].append({
            "char_from": c1,
            "char_to": c2,
            "relation_type": rel["type"],
            "affinity": rel["affinity"],
            "trigger": rel["trigger"],
        })
    rel_output_path.write_text(json.dumps(rel_data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Initialized {len(characters['characters'])} characters to {output_path}")
    print(f"Initialized {len(rel_data['relationships'])} relationships to {rel_output_path}")
    try:
        from novel_engine.engine.db import StateDB
        db_dir = root / "runtime"
        db_dir.mkdir(parents=True, exist_ok=True)
        db = StateDB(db_path=str(db_dir / "state.db"), project_root=root)
        db.import_from_json()
    except Exception as _e:
        print(f"WARNING: failed to sync StateDB on init: {_e}")
    return characters


if __name__ == "__main__":
    import sys
    root = Path(__file__).parent.parent
    if len(sys.argv) > 1:
        root = Path(sys.argv[1])
    result = init_characters(root)
    print(f"Characters: {list(result.get('characters', {}).keys())}")
