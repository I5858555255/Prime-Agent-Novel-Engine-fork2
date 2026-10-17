# -*- coding: utf-8 -*-
"""CC28 #2：CC25 文学 pass 编辑预算/去 no-op/相邻合并/语义护栏（零 LLM）单测。"""
from novel_engine.agents.literary_pass import (
    apply_literary_edits,
    is_noop_edit,
    violates_protected_terms,
)

S1 = ("陆烬站在山洞口，望着远方连绵的黑色山脊，心里却异常平静。"
      "风从谷底卷上来，带着潮湿的腐叶气味，吹得他破旧的衣襟猎猎作响。"
      "他握紧了拳头，知道从今夜起，一切都将不同。")

# 句2 的有效替换（等长、实质不同、不含受保护词）
A2_HEAD = "风从谷底卷上来"
A2_END = "猎猎作响。"
R2 = "山风穿过幽深林子，裹着夜露的凉意，拂过他单薄衣衫，久久不息。"

# 紧接句2 的句3（与句2 终点间隔≈0，属相邻碎改）
A3_HEAD = "他握紧了拳头"
A3_END = "都将不同。"
R3 = "他攥紧指节，明白自此以后，命数要由自己来写。"


def _span(sid, head, end, repl):
    return {"scene_id": sid, "op": "replace_span",
            "anchor": head, "anchor_end": end, "replacement": repl}


def test_noop_helper_basic():
    assert is_noop_edit("他向山下走去。", "他向山下走去。") is True
    assert is_noop_edit("他向山下走去。", "他向山下走去") is True   # 仅差标点
    assert is_noop_edit("他向山下走去。", "他快步奔下山去。") is False


def test_violates_protected_helper():
    assert violates_protected_terms("陆烬站在门口", "陆尽站在门口", {"陆烬"}) is True
    assert violates_protected_terms("那人站在门口", "陆烬站在门口", {"陆烬"}) is True
    assert violates_protected_terms("陆烬站在门口", "陆烬立在门前", {"陆烬"}) is False
    assert violates_protected_terms("任意一句话", "另一句表达", None) is False


def test_noop_edit_32_to_32_dropped():
    # replacement 与被替换片段逐字相同 → no-op，整条丢弃
    edits = [{"scene_id": 1, "op": "replace_span",
              "anchor": "陆烬站在山洞口", "anchor_end": "异常平静。",
              "replacement": "陆烬站在山洞口，望着远方连绵的黑色山脊，心里却异常平静。"}]
    out, applied, notes = apply_literary_edits({1: S1}, edits)
    assert applied == 0
    assert out[1] == S1
    assert any("no-op" in n for n in notes)


def test_semantic_guard_rejects_name_change():
    edits = [{"scene_id": 1, "op": "replace_span",
              "anchor": "陆烬站在山洞口", "anchor_end": "异常平静。",
              # 替换片段删掉了 canonical 人名“陆烬”
              "replacement": "少年立在山洞口，望着远方连绵的黑色山脊，心里异常平静。"}]
    out, applied, notes = apply_literary_edits({1: S1}, edits,
                                               protected_terms={"陆烬"})
    assert applied == 0
    assert out[1] == S1
    assert any("protected term" in n for n in notes)


def test_adjacent_micro_edits_second_dropped():
    edits = [_span(1, A2_HEAD, A2_END, R2), _span(1, A3_HEAD, A3_END, R3)]
    out, applied, notes = apply_literary_edits({1: S1}, edits)
    assert applied == 1                      # 句2 接受，紧邻的句3 被判碎改丢弃
    assert R2 in out[1]
    assert any("adjacent" in n for n in notes)


def test_effective_edit_budget_caps_at_three():
    texts = {k: S1 for k in range(1, 5)}    # 4 个场各 1 条分散且有效的编辑
    edits = [_span(sid, A2_HEAD, A2_END, R2) for sid in range(1, 5)]
    out, applied, notes = apply_literary_edits(texts, edits)
    assert applied == 3                      # 单轮最多 3 条有效编辑
    assert any("budget" in n for n in notes)


def test_absolute_length_cap_forces_rollback():
    edits = [_span(1, A2_HEAD, A2_END, R2)]
    out, applied, notes = apply_literary_edits({1: S1}, edits, max_abs_total_change=0)
    assert applied == 0
    assert out[1] == S1                      # 触发绝对字数上限 → 整体回滚


def test_valid_edit_applies_cleanly():
    edits = [_span(2, A2_HEAD, A2_END, R2)]
    out, applied, notes = apply_literary_edits({2: S1}, edits)
    assert applied == 1
    assert out[2] != S1 and R2 in out[2]
    # 除替换片段外其它字不变
    assert out[2].endswith(A3_HEAD) or "他握紧了拳头" in out[2]
