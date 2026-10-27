"""Agent-callable API; output roots are explicit to keep prose projects separate."""
from __future__ import annotations

import asyncio
from pathlib import Path

from .runner import CondensedPipeline


def _invoke(command, output_root, project_root, chapter):
    pipeline = CondensedPipeline(project_root or Path(__file__).resolve().parents[1], output_root)
    try:
        if command == "generate":
            return pipeline.generate(chapter)
        if command == "verify":
            return pipeline.verify()
        return pipeline.status()
    finally:
        pipeline.close()


async def generate_condensed_chapter(chapter, *, output_root, project_root=None):
    return await asyncio.to_thread(_invoke, "generate", output_root, project_root, chapter)


async def condensed_status(*, output_root, project_root=None, verify=False):
    return await asyncio.to_thread(_invoke, "verify" if verify else "status", output_root, project_root, None)
