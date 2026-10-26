"""Bounded, punctuation-only edits to measured long sentences before literary review."""
from .punctuation_health import detect_long_sentences, is_punctuation_only_fix, check_text_punctuation


PUNCTUATION_INSTRUCTION = (
    '修正以下实测超长句。逗号不算句末：必须用中文句号把每项拆成至少两个完整句，'
    '每个完整句少于40个中文字，不能只添加逗号。不得增删改任何文字、不得增删引号、不得换行。'
    '在完整语义处断句，禁止拆开词语（如“呼。吸”）。'
    '例如“风吹过树梢院门轻轻作响”可断为“风吹过树梢。院门轻轻作响”。'
    '返回JSON对象{"edits":[{"index":0,"text":"修正后的完整原句"}]}，不能安全修改的项略过。\n'
)


def revise_long_sentences(text, revise, limit=96, max_chars=6000, on_edit=None):
    items = []
    total = 0
    for hit in detect_long_sentences(text):
        original = hit['text']
        if '\x00' in original or text.count(original) != 1:
            continue
        if len(items) >= limit or total + len(original) > max_chars:
            break
        items.append({'index': len(items), 'text': original})
        total += len(original)
    if not items:
        return text, 0
    response = revise(items)
    edits = response.get('edits') if isinstance(response, dict) else None
    if not isinstance(edits, list):
        return text, 0
    candidates = {}
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        index = edit.get('index')
        if type(index) is not int or not 0 <= index < len(items):
            continue
        candidates.setdefault(index, []).append(edit.get('text'))
    accepted = 0
    for index, proposals in candidates.items():
        if len(proposals) != 1 or not isinstance(proposals[0], str):
            continue
        original, candidate = items[index]['text'], proposals[0].strip()
        if not candidate or candidate == original or '\n' in candidate:
            continue
        if not is_punctuation_only_fix(original, candidate):
            continue
        if any(candidate.count(mark) != original.count(mark) for mark in '“”‘’「」『』\"？！?!'):
            continue
        if detect_long_sentences(candidate) or check_text_punctuation(candidate):
            continue
        if len(candidate) > len(original) * 1.4 + 10:
            continue
        text = text.replace(original, candidate, 1)
        if on_edit is not None:
            on_edit(original, candidate)
        accepted += 1
    return text, accepted
