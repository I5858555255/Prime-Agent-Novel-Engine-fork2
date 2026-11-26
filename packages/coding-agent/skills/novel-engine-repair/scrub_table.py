"""Hot-reload scrub table for forbidden-term repair.

The scrub table is a persistent JSON file shared by every fix attempt in a
campaign. It has two sections:

- `repl` : ordered `[source, replacement]` pairs, applied top-down to string
  leaves of the candidate (and separately to expanded prose text).
- `neutral` : stem-keyed fallback map used when a NEW forbidden term is
  learned; the first stem that appears inside the term wins, else `现象`.

The table is reloaded fresh on every commit attempt and any newly-learned
term is persisted back to the file, so the campaign converges: the LLM
invents a word once, we learn it, and it never blocks again.

Usage:
    from scrub_table import ScrubTable
    st = ScrubTable(Path("scrub_terms.json"))
    text, edits = st.scrub(text)
    learned = st.learn(new_term)   # persists; returns the replacement
"""
from __future__ import annotations

import json
from pathlib import Path

_DEFAULT_NEUTRAL = {
    "气": "体感", "息": "呼吸", "频": "节奏",
    "振": "震动", "阈": "底线", "数": "痕迹", "机": "规律",
}
_FALLBACK_REPL: list[tuple[str, str]] = [
    ("排异", "抗拒"), ("机制", "规律"), ("频率", "节奏"),
]


class ScrubTable:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._doc, self._repl = self._load()

    def _load(self):
        if self.path.exists():
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            repl = [tuple(p) for p in doc.get("repl", [])]
            return doc, repl
        return {}, list(_FALLBACK_REPL)

    def save(self):
        self._doc["repl"] = [list(p) for p in self._repl]
        self.path.write_text(json.dumps(self._doc, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    @property
    def repl(self) -> list[tuple[str, str]]:
        return list(self._repl)

    def neutral_for(self, term: str) -> str:
        neutral = self._doc.get("neutral", {}) or _DEFAULT_NEUTRAL
        for stem, rep in neutral.items():
            if stem.startswith("_") or stem == "_comment":
                continue
            if stem in term:
                return rep
        return "现象"

    def learn(self, term: str) -> str:
        """Add a new forbidden term with a neutral replacement; persist.
        Returns the replacement that was used."""
        known = {o for o, _ in self._repl}
        if term in known:
            return dict(known and {o: r for o, r in self._repl})[term]
        rep = self.neutral_for(term)
        self._repl.append((term, rep))
        self.save()
        return rep

    def learn_from_errors(self, errors) -> list[str]:
        """Parse forbidden-term hits out of validator error strings and
        learn+persist each. Returns the list of newly-learned terms.

        The validator names terms as e.g. "场景S2扩写指引含禁词['调息']"; we
        extract the bracketed list and learn each CJK token (skipping pure
        punctuation/whitespace fragments)."""
        import re as _re
        known = {o for o, _ in self._repl}
        new_terms: list[str] = []
        for err in errors:
            m = _re.search(r"含禁词(\[[^\]]+\])", str(err))
            if not m:
                continue
            arr = m.group(1)
            for term in _re.findall(r"['\u2018\u2019\"]?([^',\]\u2018\u2019\"]+)['\u2018\u2019\"]?", arr):
                term = term.strip()
                # skip empty and pure-punctuation fragments
                if not term or not _re.search(r"[\u4e00-\u9fffA-Za-z0-9]", term):
                    continue
                if term in known:
                    continue
                new_terms.append(term)
                self.learn(term)
                known.add(term)
        return new_terms

    def scrub_text(self, text: str) -> tuple[str, int]:
        """Apply the table to a plain string. Returns (new_text, edit_count)."""
        count = 0
        for old, new in self._repl:
            if old in text:
                text = text.replace(old, new)
                count += 1
        return text, count

    def scrub_obj(self, obj):
        """Apply the table to every string leaf of a nested dict/list.
        Returns (new_obj, edit_count)."""
        count = 0

        def walk(o):
            nonlocal count
            if isinstance(o, str):
                for old, new in self._repl:
                    if old in o:
                        o = o.replace(old, new)
                        count += 1
                return o
            if isinstance(o, list):
                return [walk(x) for x in o]
            if isinstance(o, dict):
                return {k: walk(v) for k, v in o.items()}
            return o

        return walk(obj), count
