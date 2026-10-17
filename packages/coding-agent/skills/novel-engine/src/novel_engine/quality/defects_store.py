import json
from pathlib import Path


class DefectsStore:
    def __init__(self, path: str):
        self.path = Path(path)
        self._data = {"defects": []}
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                self._data = {"defects": []}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, chapter: int, kind: str, detail: str):
        self._data["defects"].append({
            "chapter": chapter, "kind": kind, "detail": detail,
            "consumed": False, "known": False,
        })
        self._save()

    def all(self) -> list:
        return self._data["defects"]

    def pending(self) -> list:
        return [d for d in self._data["defects"] if not d["consumed"] and not d["known"]]

    def consume(self, chapter: int) -> bool:
        for d in self._data["defects"]:
            if d["chapter"] == chapter and not d["consumed"]:
                d["consumed"] = True
                self._save()
                return True
        return False

    def mark_known(self, chapter: int):
        for d in self._data["defects"]:
            if d["chapter"] == chapter:
                d["known"] = True
                self._save()
