# -*- coding: utf-8 -*-
"""设定贯通·伏笔-终局回收远期校验（validate_foreshadow_closure）纯函数测试。

hermetic：不依赖任何真实生成产物，直接构造 registry dict。
"""
from __future__ import annotations

from novel_engine.quality.outline_coverage_gate import validate_foreshadow_closure


def _registry(foreshadows: list[dict]) -> dict:
    return {"foreshadows": foreshadows}


def test_overdue_unresolved_blocks_with_readable_reason():
    """伏笔预定回收章节已过、status 仍 planned → 硬拦截 + 可读归因。"""
    reg = _registry([
        {"id": "F001", "resolve_chapter": 100, "importance": 0.9, "status": "planned"},
    ])
    passed, issues = validate_foreshadow_closure(reg, current_chapter=105)
    assert not passed
    assert len(issues) == 1
    assert "F001" in issues[0]
    assert "resolve@100" in issues[0]
    assert "ch105" in issues[0]


def test_not_yet_due_passes():
    """回收节点未到（resolve_chapter >= current_chapter）→ 通过。"""
    reg = _registry([
        {"id": "F001", "resolve_chapter": 120, "importance": 0.9, "status": "planned"},
    ])
    passed, issues = validate_foreshadow_closure(reg, current_chapter=105)
    assert passed
    assert issues == []


def test_due_this_chapter_counts_as_resolving_passes():
    """resolve_chapter == current_chapter 视为正在回收，不拦。"""
    reg = _registry([
        {"id": "F001", "resolve_chapter": 105, "importance": 0.9, "status": "planned"},
    ])
    passed, issues = validate_foreshadow_closure(reg, current_chapter=105)
    assert passed
    assert issues == []


def test_resolved_status_passes():
    """status='resolved' → 视为已回收，不拦。"""
    reg = _registry([
        {"id": "F001", "resolve_chapter": 100, "importance": 0.9, "status": "resolved"},
    ])
    passed, issues = validate_foreshadow_closure(reg, current_chapter=105)
    assert passed
    assert issues == []


def test_resolved_ids_param_passes():
    """调用方传入 resolved_ids（如 StateDB status='resolved' 集合）→ 不拦。"""
    reg = _registry([
        {"id": "F001", "resolve_chapter": 100, "importance": 0.9, "status": "planned"},
    ])
    passed, issues = validate_foreshadow_closure(
        reg, current_chapter=105, resolved_ids={"F001"})
    assert passed
    assert issues == []


def test_low_importance_not_checked():
    """importance < 0.7 不参与远期回收校验（与编写指南口径一致）。"""
    reg = _registry([
        {"id": "F001", "resolve_chapter": 100, "importance": 0.5, "status": "planned"},
    ])
    passed, issues = validate_foreshadow_closure(reg, current_chapter=105)
    assert passed
    assert issues == []


def test_empty_or_missing_registry_passes():
    """registry 为 None / 空 / 无 foreshadows → 全部通过。"""
    assert validate_foreshadow_closure(None, 105) == (True, [])
    assert validate_foreshadow_closure({}, 105) == (True, [])
    assert validate_foreshadow_closure({"foreshadows": []}, 105) == (True, [])


def test_mixed_overdue_and_ok_reports_only_overdue():
    """多条混合：只报逾期未回收项，不误报未到期项。"""
    reg = _registry([
        {"id": "F001", "resolve_chapter": 100, "importance": 0.9, "status": "planned"},
        {"id": "F002", "resolve_chapter": 300, "importance": 0.92, "status": "planned"},
        {"id": "F003", "resolve_chapter": 90, "importance": 0.95, "status": "resolved"},
        {"id": "F004", "resolve_chapter": 110, "importance": 0.3, "status": "planned"},
    ])
    passed, issues = validate_foreshadow_closure(reg, current_chapter=105)
    assert not passed
    assert len(issues) == 1
    assert "F001" in issues[0]
    assert "F002" not in issues[0]
    assert "F003" not in issues[0]
    assert "F004" not in issues[0]
