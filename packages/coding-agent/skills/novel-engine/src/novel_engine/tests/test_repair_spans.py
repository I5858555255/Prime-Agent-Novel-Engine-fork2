from novel_engine.quality.repair_spans import build_repair_spans, resolve_span_edits


def test_spans_keep_quoted_sentences_together_and_use_immutable_ids():
    dialogue = '“第一句话。第二句话！”'
    text = '陈老根抬手敲门。' + dialogue + '门后的人没有回应。\n他提灯走向院子。'
    spans = build_repair_spans({4: text}, target_chars=10)
    assert [s['span_id'] for s in spans] == list(range(len(spans)))
    assert any(dialogue in s['text'] for s in spans)
    assert ''.join(s['text'] for s in spans) == text.replace('\n', '')
    assert all(s['scene_id'] == 4 and s['chars'] == len(s['text']) for s in spans)


def test_model_cannot_extend_span_or_select_another_scene():
    spans = build_repair_spans({1: '陈老根站在村口提着灯。', 2: '婴孩忽然动了动手指。'})
    edits = resolve_span_edits({'edits': [{'span_id': 0, 'scene_id': 2, 'anchor_end': '全文末尾',
                                         'replacement': '陈老根提灯站在村口。'}]}, spans)
    assert edits == [{'scene_id': 1, 'op': 'replace_span', 'anchor': spans[0]['text'],
                      'anchor_end': '', 'replacement': '陈老根提灯站在村口。'}]


def test_unknown_duplicate_and_boolean_span_ids_are_rejected():
    spans = build_repair_spans({1: '陈老根站在村口提着灯。'})
    assert resolve_span_edits({'edits': [{'span_id': i, 'replacement': '完整替换后的正文。'}
                                       for i in [0, 0, True, 999]]}, spans) == []


def test_short_fragment_lines_are_grouped_instead_of_lost():
    text = '冷。\n风。\n孩子。\n眼前的一切慢慢变了。'
    spans = build_repair_spans({1: text})
    assert ''.join(span['text'].replace('\n', '') for span in spans) == text.replace('\n', '')
    assert all(span['chars'] >= 8 for span in spans)
