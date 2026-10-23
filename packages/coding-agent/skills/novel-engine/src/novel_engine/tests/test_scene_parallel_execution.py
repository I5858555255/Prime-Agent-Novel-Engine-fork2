import pytest
from unittest.mock import MagicMock
from novel_engine.agents.writer_agent import WriterAgent
from novel_engine.core.llm_client import LLMClient

def test_generate_scenes_parallel_sorting():
    client = LLMClient(use_mock=True)
    writer = WriterAgent(llm_client=client)
    task_card = {
        "chapter_num": 1,
        "scene_blueprints": [
            {"scene_num": 3, "goal": "Goal 3", "location": "Loc 3"},
            {"scene_num": 1, "goal": "Goal 1", "location": "Loc 1"},
            {"scene_num": 2, "goal": "Goal 2", "location": "Loc 2"},
        ]
    }
    synopsis = {"synopsis": "Test synopsis"}

    scenes = writer.generate_scenes(task_card, synopsis)
    assert len(scenes) == 3
    scene_nums = [s.scene_id for s in scenes]
    assert scene_nums == [1, 2, 3]
