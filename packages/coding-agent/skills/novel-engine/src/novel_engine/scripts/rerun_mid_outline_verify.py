# -*- coding: utf-8 -*-
"""一次性重跑 ch48/ch25（中纲约束生效后），结果写 runtime/mid_outline_rerun_result.json。"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


async def _main() -> int:
    from novel_engine.agent_api import generate_chapter
    out = []
    for ch in (48, 25):
        t0 = time.monotonic()
        print(f"=== START ch{ch} {time.strftime('%H:%M:%S')} ===", flush=True)
        try:
            r = await generate_chapter(ch, project_root=str(ROOT), use_mock=False)
        except Exception as e:  # noqa: BLE001
            r = {"success": False, "error": str(e)}
        elapsed_min = round((time.monotonic() - t0) / 60, 1)
        rec = {
            "chapter": ch,
            "success": r.get("success"),
            "score": r.get("score"),
            "note": (r.get("note") or r.get("human_review_note") or "")[:200],
            "errors": (r.get("errors") or [])[:3],
            "elapsed_min": elapsed_min,
        }
        print(json.dumps(rec, ensure_ascii=False), flush=True)
        out.append(rec)
    (ROOT / "runtime" / "mid_outline_rerun_result.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE all", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
