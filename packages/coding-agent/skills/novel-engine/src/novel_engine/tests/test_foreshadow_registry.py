# -*- coding: utf-8 -*-
"""伏笔权威注册表（config/foreshadow/registry.json）的 hermetic 测试。

- 注册表完整：9 条伏笔均含 fid（稳定语义ID）+ legacy id（F编号）+ anchors；
- 双向解析：audit 的 _load_foreshadow_anchors() 视图同时含 F00X 与 fid，
  且指向同一实体的同一组锚点（防编号错位，外部评审 6.1 缺口）；
- 锚点校验：boundary_note 用 fid 或 legacy F 编号均能校验命中/未命中；
- 旧文件 foreshadow_anchors.json 已废弃（不再含 anchors 数据）。
零 LLM；只读 config 数据，不写任何文件。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from novel_engine.scripts.audit_mid_outline import (
    _check_f_anchor,
    _load_foreshadow_anchors,
)

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

ROOT = _SRC  # src/novel_engine/
REG = ROOT / "config" / "foreshadow" / "registry.json"


def _load_registry() -> list[dict]:
    data = json.loads(REG.read_text(encoding="utf-8"))
    return [fs for fs in data.get("foreshadows", []) if isinstance(fs, dict)]


def test_registry_has_9_entries_with_fid_and_anchors():
    reg = _load_registry()
    assert len(reg) == 9
    for fs in reg:
        assert fs.get("fid"), f"{fs['id']} missing fid"
        assert fs.get("id", "").startswith("F")
        assert isinstance(fs.get("anchors"), list) and len(fs["anchors"]) >= 3


def test_fid_legacy_bidirectional_resolution():
    """F005 与 f005_fog_core 解析到同一实体同一组锚点。"""
    anchors_view = _load_foreshadow_anchors()
    assert "F005" in anchors_view
    assert "f005_fog_core" in anchors_view
    assert anchors_view["F005"] == anchors_view["f005_fog_core"]
    assert "f001_royal_heirlooms" in anchors_view
    assert "F009" in anchors_view


def test_anchor_check_with_fid_reference():
    anchors_view = _load_foreshadow_anchors()
    # 命中：boundary_note 用 fid 引用，文本含锚点词"门芯"
    note = "需人工确认：接近 f005_fog_core 门芯回收边界"
    joined = "陆烬在禁区边缘的迷雾中感应到异变，提及门芯"
    assert _check_f_anchor(note, joined, anchors_view) is None
    # 未命中：同 fid 引用，但文本无任何锚点词 → 挂起归因
    joined2 = "陆烬与村民商议冬季粮食储备"
    reason = _check_f_anchor(note, joined2, anchors_view)
    assert reason is not None and "f005_fog_core" in reason


def test_anchor_check_with_legacy_f_reference():
    anchors_view = _load_foreshadow_anchors()
    note = "需人工确认：接近 F005 门芯回收边界"
    joined = "迷雾禁区的异变与太初原核的门芯有关"
    assert _check_f_anchor(note, joined, anchors_view) is None
    joined2 = "陈老根教陆烬辨认草药"
    assert _check_f_anchor(note, joined2, anchors_view) is not None


def test_old_anchors_file_is_deprecated():
    """foreshadow_anchors.json 不得再含可用的 anchors 数据（防第三份同步源）。"""
    p = ROOT / "config" / "planning" / "foreshadow_anchors.json"
    if not p.exists():
        return  # 已删除也视为通过
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data.get("_deprecated") is True
    assert "anchors" not in data
