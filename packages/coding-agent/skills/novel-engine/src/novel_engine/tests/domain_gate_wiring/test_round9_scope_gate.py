# -*- coding: utf-8 -*-
"""CC round-9 scope gate：设定泄漏/时间锚越界确定性门离线测试。"""
import json
from pathlib import Path

import pytest

from novel_engine.quality import scope_gate
from novel_engine.quality.scope_gate import (
    detect_scope_violations,
    scope_fix_directive,
    _extract_card_hard_terms,
)

INFANT = {
    "arc": "infant",
    "chapters": [1, 5],
    "negation_window": 20,
    "negation_markers": ["不是", "并非", "不信", "如果", "据说", "之说", "莫", "等", "等到", "直到"],
    "idiom_whitelist": ["魂飞魄散", "失魂落魄"],
    "hard_block": ["魂魄印记", "跨界", "守护者", "灵根", "筑基", "仙门", "太虚仙门"],
    "soft_warn": ["修炼", "境界", "魂魄"],
    "night_anchor_markers": ["子时", "当夜", "数时辰"],
    "dawn_markers": ["天亮", "黎明", "鱼肚白", "鸡鸣", "天边泛起"],
    "future_markers": ["等", "等到", "直到", "再说", "尚未"],
}
LATER = {
    "arc": "cultivation",
    "chapters": [20, 40],
    "negation_window": 20,
    "negation_markers": ["不是"],
    "idiom_whitelist": [],
    "hard_block": [],
    "soft_warn": [],
    "night_anchor_markers": [],
    "dawn_markers": [],
    "future_markers": [],
}
NIGHT_ANCHOR = {"chapter_start_marker": "出生当夜", "max_time_progression": "数时辰内"}


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    d = tmp_path / "config" / "leak_terms"
    d.mkdir(parents=True)
    (d / "infant.json").write_text(json.dumps(INFANT, ensure_ascii=False), encoding="utf-8")
    (d / "cultivation.json").write_text(json.dumps(LATER, ensure_ascii=False), encoding="utf-8")
    scope_gate.reset_config_cache()
    yield tmp_path
    scope_gate.reset_config_cache()


def _terms(res, kind):
    return {v["term"] for v in res[kind]}


def test_hard_leak_compound(root):
    txt = "雾中响起古老声音，自称这片迷雾的守护者，其魂魄印记里有跨界而来的东西。"
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert {"守护者", "魂魄印记", "跨界"} <= _terms(res, "hard")


def test_negation_downgrades_to_soft(root):
    txt = "陈老根从不信什么魂魄印记守护者之说，只当山民讹传。"
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert _terms(res, "hard") == set()
    assert {"魂魄印记", "守护者"} <= _terms(res, "soft")


def test_idiom_whitelist_exempt(root):
    txt = "一声炸雷吓得他魂飞魄散，手中柴刀险些落地。"
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert _terms(res, "hard") == set()
    assert _terms(res, "soft") == set()


def test_dawn_overrun_narrative_is_hard(root):
    txt = "两人疾走，天边泛起鱼肚白，远处鸡鸣声起，眼看就要天亮。"
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert {"天边泛起", "鱼肚白", "鸡鸣", "天亮"} <= _terms(res, "hard")
    assert all(v["kind"] == "dawn_overrun" for v in res["hard"])


def test_dawn_in_quotes_exempt(root):
    txt = "陈老根低声道：“莫慌，等天亮再说。”他裹紧婴儿继续躲在暗处。"
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert _terms(res, "hard") == set()


def test_dawn_future_tense_narrative_exempt(root):
    txt = "他打定主意，要等到天亮再回村，此刻仍伏在雾里。"
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert _terms(res, "hard") == set()


def test_generic_terms_soft_only(root):
    txt = "他不懂什么修炼，也不知境界高低，只凭蛮力过活。"
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert _terms(res, "hard") == set()
    assert {"修炼", "境界"} <= _terms(res, "soft")


def test_inactive_arc_has_no_gate(root):
    txt = "少年盘膝引气，灵根一动便踏上筑基之路，天边泛起鱼肚白。"
    res = detect_scope_violations(txt, 20, {"max_time_progression": "数年"}, root)
    assert _terms(res, "hard") == set()
    assert _terms(res, "soft") == set()


def test_dawn_only_under_night_anchor(root):
    # 非当夜锚（普通章节）不应因天明词触发
    txt = "天边泛起鱼肚白，新的一日开始了。"
    res = detect_scope_violations(txt, 1, {"max_time_progression": "数日内"}, root)
    assert _terms(res, "hard") == set()


def test_extra_hard_terms_from_card(root):
    txt = "岩缝里隐隐透出一股诡异的青光。"
    base = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root)
    assert _terms(base, "hard") == set()
    res = detect_scope_violations(txt, 1, NIGHT_ANCHOR, root, extra_hard_terms=["青光"])
    assert "青光" in _terms(res, "hard")


def test_extract_card_hard_terms_short_only():
    card = {"forbidden_checks": ["仙门", "本章不得提前演到婴儿被村民围住之后的情节", "修炼体系", ""]}
    terms = _extract_card_hard_terms(card)
    assert "仙门" in terms
    assert "修炼体系" in terms
    assert all(len(t) <= 6 for t in terms)
    assert not any(len(t) > 6 for t in terms)


def test_fix_directive_mentions_leak_and_dawn(root):
    res = detect_scope_violations(
        "守护者在雾中低语，天边泛起鱼肚白，他静待天亮。", 1, NIGHT_ANCHOR, root)
    d = scope_fix_directive(res["hard"])
    assert "守护者" in d
    assert "鱼肚白" in d or "天亮" in d
    assert "时间" in d


def test_empty_text_is_clean(root):
    res = detect_scope_violations("   ", 1, NIGHT_ANCHOR, root)
    assert res["hard"] == [] and res["soft"] == []
