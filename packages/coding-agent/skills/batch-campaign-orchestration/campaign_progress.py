"""Head-watermark + progress-file helpers for a resumable batch campaign.

The watermark is the single source of truth for progress; a running job's
stdout is not. These helpers read the persistent progress file and the
committed head so a restart is idempotent.

Usage:
    from campaign_progress import Campaign
    cam = Campaign(Path("expand_progress.json"), target=3800)
    for n in cam.pending(from_ch=4):
        ok, tail = process(n)
        if ok: cam.record(n)
        else: cam.blocked(n, tail); break
"""
from __future__ import annotations

import json
from pathlib import Path


class Campaign:
    def __init__(self, progress_path: Path, target: int, default_from: int = 1):
        self.path = Path(progress_path)
        self.target = target
        self.default_from = default_from

    def done(self) -> set[int]:
        if self.path.exists():
            return set(json.loads(self.path.read_text(encoding="utf-8")).get("done", []))
        return set()

    def frontier(self, frm: int | None = None) -> int:
        """First not-done chapter at/after frm (or after the global done-set)."""
        d = self.done()
        start = frm if frm is not None else self.default_from
        n = start
        while n in d:
            n += 1
        return n

    def pending(self, frm: int | None = None, end: int | None = None):
        d = self.done()
        e = end if end is not None else self.target
        start = frm if frm is not None else self.default_from
        n = self.frontier(start)
        while n <= e:
            yield n
            n = self.frontier(n + 1) if n in d else n + 1

    def record(self, n: int):
        d = self.done()
        d.add(n)
        self.path.write_text(json.dumps({"done": sorted(d)}, ensure_ascii=False, indent=1),
                             encoding="utf-8")

    def complete(self, frm: int = 1) -> bool:
        d = self.done()
        return all(n in d for n in range(frm, self.target + 1))

    def head_watermark(self) -> int:
        return max(self.done()) if self.done() else 0
