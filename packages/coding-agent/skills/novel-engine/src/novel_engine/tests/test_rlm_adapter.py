"""Tests for NovelRLMAdapter — P2 integration."""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure novel_engine is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.adapters.rlm_adapter import NovelRLMAdapter
from novel_engine.agent_api import _KERNEL


@pytest.fixture(autouse=True)
def _reset_kernel():
    """Reset RLM kernel between tests."""
    original = dict(_KERNEL)
    _KERNEL["rlm"] = None
    _KERNEL["agent_message"] = None
    yield
    _KERNEL["rlm"] = original.get("rlm")
    _KERNEL["agent_message"] = original.get("agent_message")


@pytest.fixture
def mock_orch():
    """Build a PipelineOrchestrator with MockLLMClient for tests."""
    from novel_engine.core.llm_client import MockLLMClient
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    return PipelineOrchestrator(llm_client=MockLLMClient())


@pytest.fixture
def adapter(mock_orch):
    return NovelRLMAdapter(mock_orch)


class TestNovelRLMAdapterDegrade:
    """When rlm is None, adapter must degrade to sync path with identical output shape."""

    @pytest.mark.allow_network
    @pytest.mark.asyncio
    async def test_run_chapter_without_rlm_degrades_to_sync(self, adapter, mock_orch):
        _KERNEL["rlm"] = None
        result = await adapter.run_chapter(1)
        assert "chapter" in result
        assert "success" in result
        assert "score" in result
        assert "errors" in result

    @pytest.mark.allow_network
    @pytest.mark.asyncio
    async def test_run_chapter_without_rlm_returns_same_shape_as_sync(self, adapter, mock_orch):
        _KERNEL["rlm"] = None
        adapter_result = await adapter.run_chapter(2)
        sync_result = mock_orch.generate_single_chapter(2)
        # Both must have the same keys
        assert set(adapter_result.keys()) == set(sync_result.keys())


class TestNovelRLMAdapterWithKernel:
    """When rlm is present, adapter dispatches scene groups via subagents."""

    @pytest.mark.allow_network
    @pytest.mark.asyncio
    async def test_run_chapter_with_rlm_calls_subagent_for_scenes(self, mock_orch):
        """RLM path should call rlm('sub-task') for each scene group."""
        mock_rlm = AsyncMock()
        mock_handle = MagicMock()
        mock_handle.name = "scene-group-0"
        mock_rlm.return_value = mock_handle

        mock_agent_msg = MagicMock()
        mock_agent_msg.send = AsyncMock()
        mock_agent_msg.recv = AsyncMock(return_value=[
            ({"scene_num": 1}, MagicMock(scene_id=1, scene_text="Scene 1 text", beats=["beat1"])),
        ])

        _KERNEL["rlm"] = mock_rlm
        _KERNEL["agent_message"] = mock_agent_msg

        adapter = NovelRLMAdapter(mock_orch)

        # Mock the director/synopsis stages to bypass outline coverage validation
        mock_orch._stage_world_sim = MagicMock(return_value={"characters": {}, "factions": {}, "power_system": {}})
        mock_orch._stage_directing = MagicMock(return_value={
            "chapter_num": 1,
            "scene_blueprints": [{"scene_num": 1, "beats": ["beat1"]}],
        })
        mock_orch._stage_synopsis = MagicMock(return_value={"synopsis": "test synopsis"})

        result = await adapter.run_chapter(1)

        # rlm should have been called for scene dispatch
        mock_rlm.assert_called_once_with("sub-task")
        assert "chapter" in result

    @pytest.mark.allow_network
    @pytest.mark.asyncio
    async def test_run_chapter_falls_back_on_rlm_failure(self, mock_orch):
        """If RLM dispatch raises, adapter falls back to ThreadPool path."""
        mock_rlm = AsyncMock(side_effect=RuntimeError("kernel unavailable"))

        _KERNEL["rlm"] = mock_rlm
        _KERNEL["agent_message"] = MagicMock()

        adapter = NovelRLMAdapter(mock_orch)
        mock_orch._stage_world_sim = MagicMock(return_value={"characters": {}, "factions": {}, "power_system": {}})
        mock_orch._stage_directing = MagicMock(return_value={
            "chapter_num": 1,
            "scene_blueprints": [{"scene_num": 1, "beats": ["beat1"]}],
        })
        mock_orch._stage_synopsis = MagicMock(return_value={"synopsis": "test"})

        result = await adapter.run_chapter(1)

        # Must still return a valid result dict (fallback path)
        assert "chapter" in result
        # Trace should show fallback
        trace = adapter.trace()
        assert any(t.get("status") == "fallback" for t in trace)

    @pytest.mark.allow_network
    @pytest.mark.asyncio
    async def test_trace_records_rlm_success(self, mock_orch):
        mock_rlm = AsyncMock()
        mock_handle = MagicMock()
        mock_handle.name = "sg-0"
        mock_rlm.return_value = mock_handle

        mock_agent_msg = MagicMock()
        mock_agent_msg.send = AsyncMock()
        mock_reply = [(MagicMock(scene_num=1), MagicMock(scene_id=1, scene_text="x" * 100, beats=[]))]
        mock_agent_msg.recv = AsyncMock(return_value=mock_reply)

        _KERNEL["rlm"] = mock_rlm
        _KERNEL["agent_message"] = mock_agent_msg

        adapter = NovelRLMAdapter(mock_orch)
        mock_orch._stage_world_sim = MagicMock(return_value={"characters": {}, "factions": {}, "power_system": {}})
        mock_orch._stage_directing = MagicMock(return_value={
            "chapter_num": 1,
            "scene_blueprints": [{"scene_num": 1, "beats": ["beat1"]}],
        })
        mock_orch._stage_synopsis = MagicMock(return_value={"synopsis": "test"})

        await adapter.run_chapter(1)

        trace = adapter.trace()
        rlm_entries = [t for t in trace if t.get("phase") == "rlm_dispatch"]
        assert len(rlm_entries) >= 2  # attempt + success
        assert rlm_entries[0]["status"] == "attempt"
        assert rlm_entries[1]["status"] == "success"
        trace = adapter.trace()
        rlm_entries = [t for t in trace if t.get("phase") == "rlm_dispatch"]
        assert len(rlm_entries) >= 1
        assert rlm_entries[0]["status"] == "success"
        trace = adapter.trace()
        trace = adapter.trace()
        rlm_entries = [t for t in trace if t.get("phase") == "rlm_dispatch"]
        assert len(rlm_entries) >= 1
        assert rlm_entries[0]["status"] == "success"
        rlm_entries = [t for t in trace if t.get("phase") == "scene_group_rlm"]
        assert len(rlm_entries) >= 1
        assert rlm_entries[0]["status"] == "ok"


class TestDomainTools:
    """Verify domain tool wrappers return valid shapes (mock-only)."""

    def test_get_world_context_returns_dict(self):
        from novel_engine.tools.domain_tools import get_world_context
        result = get_world_context(1)
        assert isinstance(result, dict)
        assert "characters" in result

    def test_get_character_state_returns_dict(self):
        from novel_engine.tools.domain_tools import get_character_state
        result = get_character_state("主角")
        assert isinstance(result, dict)

    def test_get_timeline_context_returns_dict(self):
        from novel_engine.tools.domain_tools import get_timeline_context
        result = get_timeline_context(1)
        assert isinstance(result, dict)
        assert "chapter_num" in result

    def test_get_chapter_context_returns_empty_when_no_chapter(self):
        from novel_engine.tools.domain_tools import get_chapter_context
        result = get_chapter_context(9999)
        assert result["task_card"] == {}
        assert result["chapter_num"] == 9999

    def test_save_checkpoint_returns_hash(self):
        from novel_engine.tools.domain_tools import save_checkpoint
        result = save_checkpoint(1, "test chapter content")
        assert "checkpoint_hash" in result
        assert result["chapter"] == 1

    def test_validate_continuity_returns_passed_bool(self):
        from novel_engine.tools.domain_tools import validate_continuity
        result = validate_continuity(1, "test text", {"scene_blueprints": []})
        assert isinstance(result["passed"], bool)
        assert isinstance(result["issues"], list)
