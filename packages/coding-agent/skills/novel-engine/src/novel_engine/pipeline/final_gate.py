"""Final gate: stage6 foreshadow coverage check before commit.

Runs after review and fix-loop, before publish decision. Validates that
all registered foreshadow beats are resolved in the final text.
"""
from __future__ import annotations

import logging
from pathlib import Path

from novel_engine.quality.outline_coverage_gate import (
    extract_must_cover_beats as _emb_fn,
    check_scene_must_cover_beats as _cscc_fn,
)
from novel_engine.pipeline.chapter_journal import load_authoritative_scenes

logger = logging.getLogger(__name__)


def run_stage6_foreshadow_check(
    root, chapter_num: int, task_card: dict, final_text: str, cur_score: float
) -> dict:
    """Run stage6 foreshadow coverage check.

    Returns dict with:
        blocked: bool - whether chapter should be hard-blocked
        missing_fs_ids: list - registered but unresolved foreshadow IDs
        blocked_scenes: dict - scene_id -> missing beat descriptions
    """
    result = {
        'blocked': False,
        'missing_fs_ids': [],
        'blocked_scenes': {},
        'result': {},
    }

    # R17-2 read-time correction
    try:
        _reconciled = _emb_fn(task_card)
        if _reconciled:
            task_card = {**task_card, 'must_cover_beats': _reconciled}
        else:
            task_card = {**task_card}
            task_card.pop('must_cover_beats', None)
        _all = _emb_fn(task_card)
        _mcb = [b for b in _all if b.get('category') == 'foreshadow']
        result['mcb'] = _mcb
    except Exception:
        _mcb = []
        result['mcb'] = []

    # R17-3: check registered foreshadow actions
    _registered = {
        (fa.get('foreshadow_id') or '').strip()
        for fa in (task_card.get('foreshadow_actions') or [])
        if isinstance(fa, dict) and (fa.get('foreshadow_id') or '').strip()
    }
    _resolved = {b.get('foreshadow_id', '') for b in _mcb if b.get('foreshadow_id')}
    _missing = sorted(_registered - _resolved)
    result['missing_fs_ids'] = _missing

    if _missing:
        logger.error(
            f'ch{chapter_num} MANDATORY HARD BLOCK at stage6: '
            f'foreshadow_actions present {_registered} but resolved 0 foreshadow beats; '
            f'missing_ids={_missing}; score={cur_score} cannot override')
        try:
            _qdir6 = Path(root) / 'chapters' / 'draft' / 'failed' / f'chapter_{chapter_num}'
            _qdir6.mkdir(parents=True, exist_ok=True)
            (_qdir6 / 'final_gate_reject.txt').write_text(final_text, encoding='utf-8')
        except Exception as _qe6:
            logger.error(f'Failed to quarantine stage6 reject ch{chapter_num}: {_qe6}')
        result['blocked'] = True
        result['hard_block_reason'] = (
            f'mandatory foreshadow absent from task card: {_missing}')
        return result

    # Per-scene check
    _blocked_scenes = {}
    if _mcb:
        try:
            _journal = {d['scene_id']: d.get('scene_text', '')
                        for d in load_authoritative_scenes(root, chapter_num)}
            if not _journal:
                _journal = {1: final_text}
            for _sn in sorted(_journal):
                _ok, _miss = _cscc_fn(_journal[_sn], _mcb, _sn)
                if not _ok and _miss:
                    _blocked_scenes[_sn] = _miss
        except Exception as _e6:
            logger.warning(f'ch{chapter_num} stage6 foreshadow check failed: {_e6}')

    result['blocked_scenes'] = _blocked_scenes

    if _blocked_scenes:
        logger.error(
            f'ch{chapter_num} MANDATORY HARD BLOCK at stage6: '
            f'foreshadow beats still missing in final_text scenes {list(_blocked_scenes.keys())}; '
            f'score={cur_score} cannot override')
        try:
            _qdir6 = Path(root) / 'chapters' / 'draft' / 'failed' / f'chapter_{chapter_num}'
            _qdir6.mkdir(parents=True, exist_ok=True)
            (_qdir6 / 'final_gate_reject.txt').write_text(final_text, encoding='utf-8')
        except Exception as _qe6:
            logger.error(f'Failed to quarantine stage6 reject ch{chapter_num}: {_qe6}')
        result['blocked'] = True
        result['hard_block_reason'] = (
            f'mandatory foreshadow beats unresolved at final_text: '
            f'scenes {list(_blocked_scenes.keys())}')

    return result

