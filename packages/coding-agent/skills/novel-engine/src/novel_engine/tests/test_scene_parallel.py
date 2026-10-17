import time

from agents.writer_agent import WriterAgent


def test_scenes_run_concurrently():
    calls = []

    class Rec:
        def chat_completion(self, messages, **k):
            calls.append(time.monotonic())
            time.sleep(0.2)
            return {"content": "scene"}

    w = WriterAgent(llm_client=Rec())
    tc = {"scenes": [{"id": i, "scene_num": i + 1} for i in range(4)], "title": "t"}
    w.generate_full_chapter(tc, {"text": "syn"})
    # 4 scenes sleeping 0.2s each; if sequential -> >=0.8s; concurrent (<=5 workers) -> ~0.2s
    assert (calls[-1] - calls[0]) < 0.6
