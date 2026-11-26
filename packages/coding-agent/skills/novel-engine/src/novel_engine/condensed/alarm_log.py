"""M2 OQ-gate: OQ4 minimal alarm channel — a local JSONL log (design §1 of the
minimal closed loop).

The OQ4 gate's real input is "where drift-suspect alarms go" (a metrics system,
an external endpoint, a paging tool). None of that is available offline, so
this module provides the **minimal offline stand-in**: a local JSONL file that
drift-suspect events are appended to. `drift.record_drift_suspect` already
persists a suspect row to the `telemetry` table (the hash-chained record); this
channel is the **emit hook** that routes that suspect somewhere a human can see
it. When the real OQ4 channel lands, swap `AlarmLog` for a client that posts to
the real destination — `drift` and the loop do not change.

No-op discipline: `AlarmLog.emit` writes ONLY when called with a real suspect
(the caller gates on `drift_window["suspect"]`). It never fabricates an alarm;
it appends one JSONL line per emit (deterministic content, an ISO-8601
captured_at timestamp).

Fully deterministic, no network, no LLM.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path


class AlarmLog:
    """Append drift-suspect events to a local JSONL file (the OQ4 emit hook).

    Each `emit` appends one line:
        {"ts": ISO8601, "kind": "drift_suspect", "telemetry_row_id": int|None,
         "drift_window": {...}}
    The file is created on first emit if absent; an empty/absent log means no
    suspect has fired (the explicit "nothing to alarm" state).
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, drift_window: dict, telemetry_row_id: int | None = None,
             captured_at: str | None = None) -> None:
        """Append one suspect event. `drift_window` is the `drift.rolling_agreement`
        readout; `telemetry_row_id` is the `lastrowid` returned by
        `drift.record_drift_suspect` (the hash-chained record this alarm points
        at). The caller is responsible for only emitting on a real suspect —
        this method does not re-gate on `drift_window["suspect"]` so a caller
        that wants to log a non-suspect readout (for audit) can still do so.

        `captured_at` (an ISO-8601 string) is the record's timestamp. When
        omitted it falls back to the current UTC time; supplying it makes the
        emitted line fully deterministic (tests inject a fixed value)."""
        ts = captured_at if captured_at is not None else _dt.datetime.now(_dt.timezone.utc).isoformat()
        record = {
            "ts": ts,
            "kind": "drift_suspect",
            "telemetry_row_id": telemetry_row_id,
            "drift_window": dict(drift_window or {}),
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def read_all(self) -> list[dict]:
        """Read back every emitted event (for tests / inspection). Missing file
        → [] (no suspect fired). Corrupt lines are skipped, not raised."""
        if not self.path.exists():
            return []
        out: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
