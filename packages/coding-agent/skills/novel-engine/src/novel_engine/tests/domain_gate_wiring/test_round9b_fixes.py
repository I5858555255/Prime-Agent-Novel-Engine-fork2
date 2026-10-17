# -*- coding: utf-8 -*-
"""CC round-9 真机反例补丁测试：## 正文 脚手架拦截 + 婴儿篇体征修炼词硬杀。"""
import json
from pathlib import Path

import pytest

from novel_engine.agents.scene_schema import validate_scene_text
from novel_engine.quality import scope_gate
from novel_engine.quality.scope_gate import detect_scope_violations

BASE = (
    "荒坡上风贴着地皮打旋，裹挟土腥与腐叶气味，枯草在雾里轻轻瑟缩。"
    "婴儿躺在一丛衰草旁，粗布襁褓四角散开，露出瘦小发红的手脚，嘴唇微微泛紫。"
    "灰绿色迷雾从坡下漫上来，像活物般缓缓吞吐，偶尔翻涌出一两道幽蓝微光。"
    "他忽然啼哭，声音细弱却像一根针，骤然扎破了夜的薄膜，惊起远处几点寒鸦。"
    "坡下泥径上传来脚步，三名樵夫扛着柴担次第走出浓雾，为首者攥紧刀柄，神色惊疑。"
    "谁也不敢先靠近那片幽光，只远远望着，喉头滚动，背上沁出一层细而冷的汗。"
    "雾气忽聚忽散，把火把的影子切得粉碎，落在一张张绷紧而苍白的面孔上。"
) * 1  # keep n-grams distinct; this is >300 chars and ends with a sentence stop

INFANT = {
    "arc": "infant", "chapters": [1, 5], "negation_window": 20,
    "negation_markers": ["不是", "并非", "而非"], "idiom_whitelist": [],
    "hard_block": ["丹田", "经脉", "灵胎", "守护者"],
    "soft_warn": ["灵气", "灵力"],
    "night_anchor_markers": ["当夜", "数时辰"],
    "dawn_markers": ["天亮", "鱼肚白"], "future_markers": ["等", "再说"],
}
ANCHOR = {"max_time_progression": "数时辰内"}


def test_base_prose_passes_scaffolding():
    ok, issues = validate_scene_text(BASE, 2000)
    assert not any(i.startswith("markdown_scaffolding") for i in issues)


def test_markdown_heading_flagged():
    text = "一段概述后文再细表。\n## 正文\n" + BASE
    ok, issues = validate_scene_text(text, 2000)
    assert any(i.startswith("markdown_scaffolding") for i in issues)


def test_plain_hash_heading_flagged():
    text = "# 第一章\n" + BASE
    ok, issues = validate_scene_text(text, 2000)
    assert any(i.startswith("markdown_scaffolding") for i in issues)


@pytest.fixture()
def root(tmp_path: Path):
    d = tmp_path / "config" / "leak_terms"
    d.mkdir(parents=True)
    (d / "infant.json").write_text(json.dumps(INFANT, ensure_ascii=False), encoding="utf-8")
    scope_gate.reset_config_cache()
    yield tmp_path
    scope_gate.reset_config_cache()


def test_body_cultivation_terms_hard(root):
    txt = "一股暖流自丹田处缓缓升起，顺着稚嫩的经脉游走，隐隐有灵胎初成之感。"
    res = detect_scope_violations(txt, 1, ANCHOR, root)
    assert {"丹田", "经脉", "灵胎"} <= {v["term"] for v in res["hard"]}


def test_negated_cultivation_terms_soft(root):
    txt = "那并非什么丹田经脉，只是一股寻常暖意流过四肢百骸罢了。"
    res = detect_scope_violations(txt, 1, ANCHOR, root)
    assert {v["term"] for v in res["hard"]} == set()
    assert {"丹田", "经脉"} <= {v["term"] for v in res["soft"]}
