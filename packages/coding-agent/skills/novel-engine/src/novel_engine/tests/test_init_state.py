# -*- coding: utf-8 -*-
"""Tests for init_state seed behavior when characters.json is empty or missing.

The conftest globally blocks real socket connections; ``allow_network``
unblocks them at ``pytest_runtest_setup`` time. Event-loop creation
(``asyncio.run``) therefore happens inside the test body, not in
pytest-asyncio's ``event_loop`` fixture (which is set up before the
conftest hook unblocks sockets).
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from novel_engine.agent_api import init_state, _is_empty_characters_json


def _setup_project_root(tmp_path: Path) -> Path:
    """Create minimal project directory structure for testing."""
    for sub in (
        "config/simulation", "config/foreshadow", "config/quality_policy",
        "config/outline", "config/forbidden", "config/director",
        "memory/world_state", "chapters/novel", "chapters/synopsis",
        "chapters/draft", "runtime", "bible",
    ):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)

    (tmp_path / "config" / "simulation" / "constraints.json").write_text(
        "{}", encoding="utf-8"
    )
    (tmp_path / "config" / "foreshadow" / "registry.json").write_text(
        json.dumps({"foreshadows": []}), encoding="utf-8"
    )
    (tmp_path / "config" / "quality_policy.json").write_text(
        json.dumps({
            "publication_line": 80,
            "soft_publication_line": 75,
            "min_ratio": 0.85,
            "max_ratio": 1.2,
            "tolerance_chars": 500,
        }),
        encoding="utf-8",
    )
    (tmp_path / "config" / "forbidden.json").write_text(
        json.dumps({"per_scenes": [], "per_chapters": [], "global": []}),
        encoding="utf-8",
    )
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({
            "pipeline": {"allow_whole_chapter_rewrite": False},
            "autonomy": {
                "failover_trigger_consecutive_errors": 3,
                "max_backoff_seconds": 1800,
            },
        }),
        encoding="utf-8",
    )
    return tmp_path


def _run_init_state(project_root: str) -> dict:
    """Run init_state with the orchestrator mocked out; returns its result dict."""
    orch = MagicMock()
    orch.state_machine.current_chapter = 0
    with patch("novel_engine.agent_api._build_orchestrator", return_value=orch):
        # asyncio.run creates the event loop in the test body, where the
        # allow_network marker has already restored socket connectivity.
        return asyncio.run(init_state(project_root=project_root))


class TestIsEmptyCharactersJson:
    """Tests for the _is_empty_characters_json helper function."""

    def test_empty_dict(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.json"
        p.write_text("{}", encoding="utf-8")
        assert _is_empty_characters_json(p) is True

    def test_empty_characters_wrapper(self, tmp_path: Path) -> None:
        p = tmp_path / "empty_chars.json"
        p.write_text(json.dumps({"characters": {}}), encoding="utf-8")
        assert _is_empty_characters_json(p) is True

    def test_valid_characters_not_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "valid.json"
        data = {"characters": {"C001": {"name": "陆烬", "realm": "凡人体质"}}}
        p.write_text(json.dumps(data), encoding="utf-8")
        assert _is_empty_characters_json(p) is False

    def test_missing_file(self, tmp_path: Path) -> None:
        p = tmp_path / "missing.json"
        assert _is_empty_characters_json(p) is False

    def test_malformed_json(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{invalid json", encoding="utf-8")
        assert _is_empty_characters_json(p) is True

    def test_characters_key_is_list(self, tmp_path: Path) -> None:
        p = tmp_path / "list.json"
        p.write_text(json.dumps({"characters": []}), encoding="utf-8")
        assert _is_empty_characters_json(p) is True


class TestInitStateEmptySeeding:
    """Tests for init_state seeding behavior (Bug 1 regression)."""

    @pytest.mark.allow_network
    def test_seeds_empty_characters_json(self, tmp_path: Path) -> None:
        """characters.json == {} -> init_characters runs; C001-C008 written."""
        _setup_project_root(tmp_path)
        ws = tmp_path / "memory" / "world_state"
        (ws / "characters.json").write_text("{}", encoding="utf-8")

        result = _run_init_state(str(tmp_path))

        data = json.loads((ws / "characters.json").read_text(encoding="utf-8"))
        chars = data.get("characters", {})
        assert len(chars) == 8, f"Expected 8 characters, got {len(chars)}"
        assert "C001" in chars and chars["C001"]["name"] == "陆烬"
        assert "C008" in chars and chars["C008"]["name"] == "荒尾"
        rel_data = json.loads(
            (ws / "relationships.json").read_text(encoding="utf-8")
        )
        assert len(rel_data.get("relationships", [])) == 6
        assert result["status"] == "ready"

    @pytest.mark.allow_network
    def test_preserves_existing_nonempty_characters(self, tmp_path: Path) -> None:
        """Non-empty valid characters.json -> NOT overwritten."""
        _setup_project_root(tmp_path)
        ws = tmp_path / "memory" / "world_state"
        existing = {
            "characters": {
                "C001": {
                    "name": "CustomName",
                    "realm": "CustomRealm",
                    "location": "CustomLoc",
                    "description": "CustomDesc",
                    "ability": "",
                    "arc": "",
                    "taboos": [],
                    "faction_id": "",
                    "first_appearance": 1,
                    "relationships": {},
                }
            }
        }
        (ws / "characters.json").write_text(json.dumps(existing), encoding="utf-8")

        result = _run_init_state(str(tmp_path))

        data = json.loads((ws / "characters.json").read_text(encoding="utf-8"))
        assert data["characters"]["C001"]["name"] == "CustomName"
        assert data["characters"]["C001"]["realm"] == "CustomRealm"
        assert len(data["characters"]) == 1  # not re-seeded with defaults
        assert result["status"] == "ready"

    @pytest.mark.allow_network
    def test_seeds_empty_characters_wrapper(self, tmp_path: Path) -> None:
        """{'characters': {}} -> init triggered."""
        _setup_project_root(tmp_path)
        ws = tmp_path / "memory" / "world_state"
        (ws / "characters.json").write_text(
            json.dumps({"characters": {}}), encoding="utf-8"
        )

        _run_init_state(str(tmp_path))

        data = json.loads((ws / "characters.json").read_text(encoding="utf-8"))
        assert len(data.get("characters", {})) == 8

    @pytest.mark.allow_network
    def test_seeds_when_file_missing(self, tmp_path: Path) -> None:
        """No characters.json at all -> init still called (legacy behavior)."""
        _setup_project_root(tmp_path)
        ws = tmp_path / "memory" / "world_state"
        assert not (ws / "characters.json").exists()

        _run_init_state(str(tmp_path))

        data = json.loads((ws / "characters.json").read_text(encoding="utf-8"))
        assert len(data.get("characters", {})) == 8

    @pytest.mark.allow_network
    def test_seeds_state_db_on_init(self, tmp_path: Path) -> None:
        """After init_characters, state.db has characters and relationships rows."""
        _setup_project_root(tmp_path)
        ws = tmp_path / "memory" / "world_state"
        (ws / "characters.json").write_text("{}", encoding="utf-8")

        _run_init_state(str(tmp_path))

        db_path = tmp_path / "runtime" / "state.db"
        assert db_path.exists()
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM characters")
        char_count = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM relationships")
        rel_count = cur.fetchone()[0]
        conn.close()
        assert char_count == 8, f"Expected 8 characters in StateDB, got {char_count}"
        assert rel_count == 6, f"Expected 6 relationships in StateDB, got {rel_count}"
        # Verify a known Chinese name is present
        conn2 = sqlite3.connect(str(db_path))
        conn2.row_factory = sqlite3.Row
        row = conn2.execute("SELECT name FROM characters WHERE id=?", ("C001",)).fetchone()
        conn2.close()
        assert row is not None and row["name"] == "陆烬"
