# -*- coding: utf-8 -*-
from novel_engine.quality.repetition_detector import purify_novel_for_publish


def test_ascii_dialogue_quotes_normalized_to_double_curly():
    draft = '# 第1章\n\n他沉声说："这孩子，我带走了。"\n\n有人低声道：\'昆仑那边出事了。\'\n'
    out = purify_novel_for_publish(draft, chapter_num=1)
    assert '"' not in out
    assert "'" not in out
    assert "“这孩子，我带走了。”" in out
    assert "“昆仑那边出事了。”" in out


def test_quote_normalization_is_balanced():
    draft = '# 第1章\n\n' + ''.join('他说："好。"\n\n' for _ in range(5))
    out = purify_novel_for_publish(draft, chapter_num=1)
    assert out.count("“") == out.count("”") == 5
