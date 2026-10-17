# -*- coding: utf-8 -*-
"""Deterministic pre-validation for world_state changes before application.

Each change is checked against configured hard/soft rules.
Hard violations → rejected (status=rejected in ledger, _flag_for_human called).
Soft violations → logged as warning, change proceeds normally.
Entity-not-found violations → rejected (same path as hard).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# Canonical realm ordering (lowest → highest); used for regression/jump detection.
DEFAULT_REALM_ORDER = [
    "凡人体质",
    "炼气一层", "炼气二层", "炼气三层", "炼气四层", "炼气五层",
    "炼气六层", "炼气七层", "炼气八层", "炼气九层", "炼气圆满",
    "筑基", "筑基期", "筑基初期", "筑基中期", "筑基后期", "筑基圆满",
    "金丹", "金丹期", "金丹初期", "金丹中期", "金丹后期", "金丹圆满",
    "元婴", "元婴期", "元婴初期", "元婴中期", "元婴后期", "元婴圆满",
    "化神", "化神期", "化神初期", "化神中期", "化神后期", "化神圆满",
    "飞升", "飞升期",
    "人道之主",
]

DEAD_MARKERS = ("死亡", "陨落")


def _load_validation_rules(root: str | Path) -> dict:
    path = Path(root) / "config" / "quality_thresholds.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _realm_index(realm: str, order: list[str]) -> int:
    """Return positional index of realm in order; -1 if unknown."""
    realm_stripped = realm.strip()
    for i, r in enumerate(order):
        if r == realm_stripped:
            return i
    # partial match: check if realm_stripped starts with any entry
    for i, r in enumerate(order):
        if realm_stripped.startswith(r):
            return i
    return -1


def _is_dead(realm: str) -> bool:
    return any(marker in str(realm) for marker in DEAD_MARKERS)


def validate_changes(
    changes: list[dict],
    *,
    characters: dict,
    root: str | Path,
    flag_for_human: Optional[Callable[[str], None]] = None,
) -> list[dict]:
    """Validate a batch of pending state changes against hard/soft rules.

    Args:
        changes: list of change dicts as produced by the director/synopsis.
        characters: the characters dict (as loaded from characters.json).
        root: project root path (used to load validation rules config).
        flag_for_human: optional callback invoked on each hard violation;
            called with a human-readable reason string.

    Returns:
        A list of (change, result) tuples where result is one of
        "passed", "rejected", or "soft_warn".  rejected/soft_warn entries
        carry an additional "reason" key.
    """
    rules = _load_validation_rules(root).get("change_validation", {})
    hard_rules = rules.get("hard", [])
    soft_rules = rules.get("soft", [])
    realm_order = rules.get("realm_order", DEFAULT_REALM_ORDER)

    all_char_ids = set(characters.get("characters", {}).keys())

    results: list[tuple[dict, str, Optional[str]]] = []

    for change in changes:
        change_type = change.get("type")
        target = str(change.get("target", ""))
        new_value = change.get("new_value")

        # --- Entity-not-found: always hard, regardless of config ---
        if change_type in ("character_realm", "character_location"):
            if target not in all_char_ids:
                reason = f"entity_not_found: target character '{target}' does not exist in world_state"
                results.append((change, "rejected", reason))
                if flag_for_human is not None:
                    flag_for_human(reason)
                continue
        elif change_type == "relationship_update":
            rel_id = change.get("relationship_id", "")
            char_id = rel_id.split("-", 1)[0] if rel_id and "-" in rel_id else ""
            if char_id and char_id not in all_char_ids:
                reason = f"entity_not_found: target character '{char_id}' in relationship_id does not exist in world_state"
                results.append((change, "rejected", reason))
                if flag_for_human is not None:
                    flag_for_human(reason)
                continue

        # --- Hard: realm regression ---
        if "realm_regression" in hard_rules and change_type == "character_realm":
            char_data = characters["characters"].get(target, {})
            current_realm = char_data.get("realm")
            if current_realm and new_value:
                cur_idx = _realm_index(current_realm, realm_order)
                new_idx = _realm_index(new_value, realm_order)
                if cur_idx >= 0 and new_idx >= 0 and new_idx < cur_idx:
                    reason = (
                        f"realm_regression: {target} realm would regress from "
                        f"'{current_realm}' (index {cur_idx}) to '{new_value}' (index {new_idx})"
                    )
                    results.append((change, "rejected", reason))
                    if flag_for_human is not None:
                        flag_for_human(reason)
                    continue

        # --- Hard: dead character action ---
        if "dead_character_action" in hard_rules and change_type == "character_realm":
            char_data = characters["characters"].get(target, {})
            current_realm = char_data.get("realm")
            if current_realm and _is_dead(current_realm):
                reason = f"dead_character_action: change attempted for deceased character '{target}' (realm='{current_realm}')"
                results.append((change, "rejected", reason))
                if flag_for_human is not None:
                    flag_for_human(reason)
                continue

        # --- Soft: realm jump too large ---
        if "realm_jump_too_large" in soft_rules and change_type == "character_realm":
            char_data = characters["characters"].get(target, {})
            current_realm = char_data.get("realm")
            if current_realm and new_value:
                cur_idx = _realm_index(current_realm, realm_order)
                new_idx = _realm_index(new_value, realm_order)
                if cur_idx >= 0 and new_idx >= 0 and (new_idx - cur_idx) > 1:
                    reason = (
                        f"realm_jump_too_large: {target} would jump {new_idx - cur_idx} level(s) "
                        f"from '{current_realm}' to '{new_value}'"
                    )
                    logger.warning(f"[change_validation] soft violation: {reason}")
                    results.append((change, "soft_warn", reason))
                    continue

        # --- Soft: generic (logged only) ---
        for rule_name in soft_rules:
            if rule_name not in ("realm_jump_too_large",):
                # extensibility: future soft rules can be added here
                pass

        results.append((change, "passed", None))

    return results
