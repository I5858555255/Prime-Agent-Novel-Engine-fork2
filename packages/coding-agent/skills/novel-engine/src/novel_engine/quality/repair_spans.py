"""Immutable prose spans keep repair models from selecting an entire scene as an anchor."""
import re
from novel_engine.quality.punctuation_health import _body_with_quote_sentinel


def build_repair_spans(texts, target_chars=120):
    spans = []
    for sid, text in sorted(texts.items()):
        body, offsets = _body_with_quote_sentinel(text)
        boundaries = [offsets[m.start()] + 1 for m in re.finditer(r'[。！？…\n]', body)]
        start = 0
        for end in boundaries + [len(text)]:
            if end - start < target_chars and end != len(text) and text[end - 1:end] != "\n":
                continue
            chunk = text[start:end].strip()
            if len(chunk) < 8 and end != len(text):
                continue
            start = end
            if len(chunk) >= 8 and text.count(chunk) == 1:
                spans.append({'span_id': len(spans), 'scene_id': sid, 'text': chunk,
                              'chars': len(chunk)})
    return spans


def resolve_span_edits(response, spans):
    edits = response.get('edits') if isinstance(response, dict) else None
    if not isinstance(edits, list):
        return []
    by_id = {span['span_id']: span for span in spans}
    indices = [edit.get('span_id') for edit in edits if isinstance(edit, dict)]
    result = []
    for edit in edits:
        if not isinstance(edit, dict):
            continue
        index = edit.get('span_id')
        replacement = edit.get('replacement')
        if type(index) is not int or index not in by_id or indices.count(index) != 1:
            continue
        if not isinstance(replacement, str) or not replacement.strip():
            continue
        span = by_id[index]
        result.append({'scene_id': span['scene_id'], 'op': 'replace_span',
                       'anchor': span['text'], 'anchor_end': '', 'replacement': replacement.strip()})
    return result[:3]
