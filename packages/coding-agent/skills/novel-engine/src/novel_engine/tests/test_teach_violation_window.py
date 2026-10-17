# -*- coding: utf-8 -*-
"""CC ch21-50 F1/F2 回归：_has_teach_violation 传授违规判定。

F1（3ae9752a0）：婴儿受事由全句 any 收紧为动词后 20 字窗+受事结构——
修复"陆烬…教导孩子…"类无传授语义误报（ch24/25/37/38 同源，final 文本
实测 0 吐纳却标 [canon硬词] 吐纳）。
F2：提及/指称豁免（"教的那套/教过的"=回忆指称，无施动动词时放行）；
传授疑问/心理降级（"传授呼吸法，究竟意欲何为"=疑问，非实施）；
引语内吐纳豁免（他人话语/书面记录中的吐纳词，如"呼吸平稳，吐纳节奏…
异于常童"=记录者观察，非陆烬修炼）。
"""
import pytest

from novel_engine.quality.scope_gate import _has_teach_violation

FALSE_POSITIVE_CASES = [
    # ── F1：全句婴儿标记误报（无传授语义）──
    "那对眼睛里，没有寻常父亲教导孩子时的温和或鼓励，反而像两口深井，里面沉淀着太多陆烬看不懂的东西。",
    "陆烬下意识地想问为什么，想问这与白日的教导、与那些关于外界凶险的讲述有何不同。",
    "先生教导孩子读书识字，日复一日。",
    "陆烬在村塾听先生讲解《三字经》，一字一句记在心里。",
    "他想起母亲教导他做人的道理，字字句句都刻在心里。",
    # ── F2a：提及/指称语境（非实施传授）──
    "陈老根教的那套呼吸法",
    "他只是想弄清楚养父教的那套呼吸法。",
    "还能感觉到老人呼吸间，那比寻常人悠长沉重许多的节奏——正是那套他教过的、用来“强身健体”的呼吸法门",
    "他想起陈老根教过的吐纳，那是在山里的夜晚。",
    # ── F2b：传授疑问/心理（非实施）──
    "他收养自己，传授呼吸法，究竟意欲何为。",
    # ── CC 2026-10-05 ch56：普通物件传递被误判为传授（"传给"+泛称孩子）──
    "老妇哆哆嗦嗦接过碗，只抿了一小口，便传给旁边半大孩子。",
    "老妇将碗传给旁边的孩子，孩子捧着喝了。",
    "药汤一碗一碗传下去，众人分着喝。",
]

TRUE_POSITIVE_CASES = [
    "陈老根教孩子吐纳，引着气流在四肢百骸间流转。",
    "传功给陆烬，让他夜半修习。",
    "教陆烬吐纳之法。",
    "他盘算着把呼吸之法传给这个孩子。",
    # F2a 边界：提及结构 + 施动动作 = 实施（陆烬亲练，A 路线禁止）
    "他按照陈老根教的节奏，开始调整呼吸。",
    "他尝试着像陈老根教的那样，让呼吸变得绵长平缓。",
    # 既成传授实施（教给+婴儿+给）
    "自那套强身健体的动作教给陆烬后，他便一直在观察。",
]


@pytest.mark.parametrize("sentence", FALSE_POSITIVE_CASES)
def test_teach_violation_no_false_positive(sentence):
    assert _has_teach_violation(sentence) is False, f"误报: {sentence[:50]}"


@pytest.mark.parametrize("sentence", TRUE_POSITIVE_CASES)
def test_teach_violation_still_catches_real(sentence):
    assert _has_teach_violation(sentence) is True, f"漏拦: {sentence[:50]}"
