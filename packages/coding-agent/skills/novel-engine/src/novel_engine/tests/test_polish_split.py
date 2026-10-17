"""Task P1: scene-boundary-split polish with measured budget (TDD RED)."""
from novel_engine.agents.writer_agent import WriterAgent


def test_split_preserves_scene_count_and_budget():
    calls = []

    class Stub:
        def chat_completion(self, messages, temperature=None, max_tokens=None, timeout=None, **kw):
            calls.append(max_tokens)
            return {"content": messages[-1]["content"].split("【正文】\n")[-1], "finish_reason": "stop"}

    w = WriterAgent(llm_client=Stub())
    text = "【场景1：村口】\n" + "正" * 2000 + "\n\n※\n\n【场景2：村尾】\n" + "正" * 2000
    out = w._polish_by_scenes_concurrent(text, {"chapter_num": 1, "title": "t",
        "scene_blueprints": [{"scene_num": 1}, {"scene_num": 2}]}, Stub())
    assert out.count("正") > 1500 and out.count("正") > 1500
    assert all(t >= int(2000 / 0.9) for t in calls)
    # Split proof: one LLM call per scene, ※-joined output with both scenes intact.
    assert len(calls) == 2
    parts = out.split("※")
    assert len(parts) == 2
    assert all(p.count("正") > 1500 for p in parts)


def test_length_truncation_keeps_original():
    class CappedStub:
        def chat_completion(self, *a, **k):
            return {"content": "截" * 1500, "finish_reason": "length"}
    w = WriterAgent(llm_client=CappedStub())
    scene = "正" * 2000
    out = w._polish_single(scene, {"chapter_num": 1, "title": "t"}, CappedStub())
    assert out == scene  # truncated output must not replace the original


def test_short_path_length_truncation_keeps_original():
    """Short (<=4000-char) polish_chapter path shares the finish_reason guard:
    a length-truncated response clearing no bar must yield the original text."""
    seen = {}

    class CappedStub:
        def chat_completion(self, messages, temperature=None, max_tokens=None, timeout=None, **kw):
            seen["max_tokens"] = max_tokens
            return {"content": "截" * 1500, "finish_reason": "length"}

    w = WriterAgent(llm_client=CappedStub())
    short = "正" * 1000  # <= 4000 → short path with temperature retry loop
    out = w.polish_chapter(short, {"chapter_num": 1, "title": "t"}, llm_client=CappedStub())
    assert out == short
    # Unified measured budget (no old /1.5 estimate) on the short path too.
    assert seen["max_tokens"] >= int(1000 / 0.9)
