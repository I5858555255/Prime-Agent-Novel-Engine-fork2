# -*- coding: utf-8 -*-
"""gen_task_cards 模板任务书生成的 hermetic 测试。

构造失败日志 → 断言：
- 评审方差/修复循环耗尽/硬项泄漏三类生成任务书；
- 未达标（below_threshold）不生成任务书；
- 硬项卡包含命中词、判例编号（R01/R03/R04）、验收门禁清单。
零 LLM；不写真实任务卡目录。
"""
from __future__ import annotations

from novel_engine.scripts.gen_task_cards import _TEMPLATES, build_card
from novel_engine.scripts.batch_audit import classify, parse_log

_SAMPLE = """\
2026-10-11T01:01:08+0800 [INFO] === END chapter 161 [FAILED] in 200.0s (score=39.3) note=score=39.3 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
2026-10-11T01:01:08+0800 [ERROR] chapter 161 errors: []
2026-10-11T01:01:08+0800 [INFO] === END chapter 190 [FAILED] in 300.0s (score=None) note= ===
2026-10-11T01:01:08+0800 [ERROR] chapter 190 errors: ['Chapter 190 hard-failed after retries']
2026-10-11T01:01:08+0800 [INFO] === END chapter 201 [FAILED] in 400.0s (score=87.3) note=best 87.3 < 88 (hard gate []) ===
2026-10-11T01:01:08+0800 [ERROR] chapter 201 errors: []
2026-10-11T01:01:08+0800 [INFO] === END chapter 229 [FAILED] in 400.0s (score=79.19999999999999) note=best 79.19999999999999 < 88 (hard gate ['[canon硬词] 调息：他盯着陆烬。']) ===
2026-10-11T01:01:08+0800 [ERROR] chapter 229 errors: []
"""


def _cards(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    out = []
    for rec in recs:
        cat, _, term = classify(rec)
        if cat not in _TEMPLATES:
            continue
        out.append((cat, build_card(cat, rec, term, log.name)))
    return out


def test_generates_cards_for_three_fail_categories(tmp_path):
    cards = _cards(tmp_path)
    cats = [c for c, _ in cards]
    assert "review_variance" in cats
    assert "fix_loop_exhausted" in cats
    assert "hard_gate_leak" in cats


def test_below_threshold_not_generated(tmp_path):
    cards = _cards(tmp_path)
    for cat, card in cards:
        assert "ch201" not in card, "below_threshold 章不应生成任务书"


def test_hard_gate_card_contains_term_and_rules(tmp_path):
    cards = _cards(tmp_path)
    hard = [card for cat, card in cards if cat == "hard_gate_leak"][0]
    assert "调息" in hard          # 命中词注入
    assert "R01" in hard and "R03" in hard and "R04" in hard  # 判例编号
    assert "验收门禁" in hard
    assert "pyflakes" in hard and "pytest" in hard


def test_review_variance_card_mentions_repro(tmp_path):
    cards = _cards(tmp_path)
    rv = [card for cat, card in cards if cat == "review_variance"][0]
    assert "repro" in rv and "CC26" in rv


def test_fix_loop_card_mentions_root_causes(tmp_path):
    cards = _cards(tmp_path)
    fl = [card for cat, card in cards if cat == "fix_loop_exhausted"][0]
    assert "root-causes" in fl and "staticmethod" in fl
