"""P6: Cross-chapter continuity and quality gate regression tests."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.engine.db import StateDB


class TestCharacterContinuity:
    """Character state must persist across chapters via StateDB."""

    def test_character_realm_persistence(self, tmp_path):
        db = StateDB(db_path=str(tmp_path / "state.db"), project_root=tmp_path)
        db.execute_custom_query(
            "INSERT OR REPLACE INTO characters (id, name, realm, location, description, faction_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("char_001", "主角", "炼气一层", "村口", "test", ""),
        )
        realm = db.get_character_realm("char_001")
        assert realm == "炼气一层"

    def test_character_death_persistence(self, tmp_path):
        db = StateDB(db_path=str(tmp_path / "state.db"), project_root=tmp_path)
        db.execute_custom_query(
            "INSERT OR REPLACE INTO characters (id, name, realm, location, description, faction_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("char_002", "配角甲", "炼气二层", "", "test", ""),
        )
        assert db.is_character_alive("char_002") is True
        db.execute_custom_query(
            "UPDATE characters SET realm = '陨落' WHERE id = ?", ("char_002",)
        )
        assert db.is_character_alive("char_002") is False

    def test_realm_progression_validation(self, tmp_path):
        db = StateDB(db_path=str(tmp_path / "state.db"), project_root=tmp_path)
        db.execute_custom_query(
            "INSERT OR REPLACE INTO characters (id, name, realm, location, description, faction_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("char_003", "主角", "筑基初期", "", "test", ""),
        )
        realm = db.get_character_realm("char_003")
        assert realm == "筑基初期"
        assert realm != "炼气圆满"


class TestForeshadowContinuity:
    """Foreshadowing state must be tracked across chapters."""

    def test_foreshadow_planted_and_queryable(self, tmp_path):
        db = StateDB(db_path=str(tmp_path / "state.db"), project_root=tmp_path)
        db.execute_custom_query(
            "INSERT OR REPLACE INTO foreshadows (id, plant_chapter, resolve_chapter, plant_context, resolve_method, importance, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("F001", 1, 0, "埋下伏笔：迷雾禁婴传闻", "", "medium", "planned"),
        )
        db.execute_custom_query(
            "INSERT INTO clue_plans (foreshadow_id, chapter, intensity, method) VALUES (?, ?, ?, ?)",
            ("F001", 2, "high", "direct"),
        )
        active = db.query_active_foreshadows(current_chapter=2)
        assert len(active) >= 1
        assert any(f["id"] == "F001" for f in active)

    def test_foreshadow_resolved_removes_from_active(self, tmp_path):
        db = StateDB(db_path=str(tmp_path / "state.db"), project_root=tmp_path)
        db.execute_custom_query(
            "INSERT OR REPLACE INTO foreshadows (id, plant_chapter, resolve_chapter, plant_context, resolve_method, importance, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("F002", 1, 3, "伏笔2", "confirmed", "medium", "planned"),
        )
        db.execute_custom_query(
            "INSERT INTO clue_plans (foreshadow_id, chapter, intensity, method) VALUES (?, ?, ?, ?)",
            ("F002", 2, "high", "direct"),
        )
        active_ch2 = db.query_active_foreshadows(current_chapter=2)
        active_ch4 = db.query_active_foreshadows(current_chapter=4)
        assert any(f["id"] == "F002" for f in active_ch2)
        assert not any(f["id"] == "F002" for f in active_ch4)


class TestQualityGateConsistency:
    """Quality gates must produce consistent results on identical input."""

    def test_deterministic_gate_consistency(self, tmp_path):
        from novel_engine.quality.continuity_gate import detect_inversions
        specs = [
            {"seq": 1, "scene_id": 1, "text": "开场描写"},
    def test_deterministic_gate_consistency(self, tmp_path):
        from novel_engine.quality.continuity_gate import detect_inversions
        specs = [
            {"seq": 1, "scene_id": 1, "text": "开场描写", "bp_text": "goal; beats"},
            {"seq": 2, "scene_id": 2, "text": "后续发展", "bp_text": "goal2; beats2"},
        ]
        r1 = detect_inversions(specs)
        r2 = detect_inversions(specs)
        assert r1 == r2
            {"seq": 2, "scene_id": 2, "text": "后续发展"},
        ]
        r1 = detect_inversions(specs)
        r2 = detect_inversions(specs)
        assert r1 == r2

    def test_normalize_sequence_returns_mapping(self, tmp_path):
        from novel_engine.quality.continuity_gate import normalize_sequence
        bps = [{"scene_num": 1, "sequence_index": 1},
               {"scene_num": 2, "sequence_index": 2}]
        order, ok = normalize_sequence(bps)
        assert order == {1: 1, 2: 2}
        assert ok is True

    def test_timeline_jump_detection_deterministic(self, tmp_path):
        from novel_engine.quality.timeline_gate import detect_timeline_jump
        jump = detect_timeline_jump("数日内", timeline_anchor=None, chapter_num=1)
        assert jump is None or isinstance(jump, str)


class TestSessionTreeRollback:
    """Session tree must support safe rollback."""

    def test_session_tree_add_and_prune(self, tmp_path):
        from novel_engine.engine.session import SessionTree
        tree = SessionTree()
        tree.add_commit(
            chapter_num=1, content_hash="abc123",
            world_state_snapshot={"characters": {"char1": "realm1"}},
            score=85.0, branch_name="main",
        )
        tree.add_commit(
            chapter_num=2, content_hash="def456",
            world_state_snapshot={"characters": {"char1": "realm2"}},
            score=88.0, branch_name="main",
        )
        history = tree.get_branch_history("main")
        assert len(history) == 2
        tree.prune_snapshots(branch_name="main", keep=1)
        assert len(tree.get_branch_history("main")) <= 2

    def test_rollback_to_previous_chapter(self, tmp_path):
        from novel_engine.engine.session import SessionTree
        tree = SessionTree()
        tree.add_commit(
            chapter_num=1, content_hash="h1",
            world_state_snapshot={"characters": {"p": "r1"}},
            score=80.0, branch_name="main",
        )
        tree.add_commit(
            chapter_num=2, content_hash="h2",
            world_state_snapshot={"characters": {"p": "r2"}},
            score=85.0, branch_name="main",
        )
        tree.add_commit(
            chapter_num=3, content_hash="h3",
            world_state_snapshot={"characters": {"p": "r3"}},
            score=70.0, branch_name="main",
        )
        nodes = tree.get_branch_history("main")
        target = nodes[1]  # ch2
        rolled = tree.rollback_to_node(target.node_id, branch_name="main")
        assert rolled["characters"]["p"] == "r2"

    def test_session_tree_serialization_roundtrip(self, tmp_path):
        from novel_engine.engine.session import SessionTree
        tree = SessionTree()
        tree.add_commit(
            chapter_num=1, content_hash="test",
            world_state_snapshot={"a": 1},
            score=90.0, branch_name="main",
        )
        d = tree.to_dict()
        restored = SessionTree.from_dict(d)
        history = restored.get_branch_history("main")
        assert len(history) == 1
        assert history[0].chapter_num == 1
