from agents.writer_agent import WriterAgent

def test_polish_uses_full_text_not_truncated():
    seen = {}
    class Rec:
        def chat_completion(self, messages, **k):
            seen["prompt"] = messages[0]["content"]
            seen["max_tokens"] = k.get("max_tokens")
            # 模拟 ModelRouter/LLMClient 返回 dict（含 content 字段）
            content = messages[0]["content"].split("【正文】\n", 1)[-1]
            return {"role": "assistant", "content": content}
    w = WriterAgent(llm_client=Rec())
    long_text = "正文" * 6000          # > 8000 chars
    out = w.polish_chapter(long_text, {"title": "t"}, llm_client=Rec())
    # 并发 polish 按场景分割后重新拼接，输出含 ※ 分隔符
    assert "正文" * 100 in out         # 原始内容仍在
    assert "※" in out                  # 场景分隔符存在
    assert len(out) >= len(long_text)  # 不退化

def test_polish_extracts_content_from_dict():
    long = "原始正文" * 100           # 400 chars，不超过 4000 阈值
    class DictRec:
        def chat_completion(self, messages, **k):
            return {"role": "assistant", "content": long, "finish_reason": "stop"}
    w = WriterAgent(llm_client=DictRec())
    out = w.polish_chapter(long, {"title": "t"}, llm_client=DictRec())
    assert out == long                # 从 dict 中正确提取 content

def test_build_synopsis_from_task_card():
    from agents.writer_agent import SynopsisAgent
    tc = {
        "chapter_num": 3,
        "core_goal": "推进主线",
        "scene_blueprints": [
            {"scene_num": 1, "goal": "登场"},
            {"scene_num": 2, "goal": "冲突"},
        ],
    }
    s = SynopsisAgent().build_synopsis_from_task_card(tc)
    assert s["chapter_num"] == 3
    assert "推进主线" in s["synopsis"]
    assert "场景1" in s["synopsis"] and "场景2" in s["synopsis"]
    assert s["state_changes"] == []

def test_polish_falls_back_on_degenerate_output():
    class ShortRec:
        def chat_completion(self, messages, **k):
            return "好的"              # 退化短输出（如 5 字符）
    w = WriterAgent(llm_client=ShortRec())
    long_text = "正文" * 6000
    out = w.polish_chapter(long_text, {"title": "t"}, llm_client=ShortRec())
    # 并发 polish 失败时保留各场景原文，以 ※ 分隔
    assert "正文" in out
    assert "※" in out
    # 不退化为空或极短文本
    assert len(out) > len(long_text) * 0.5

def test_polish_falls_back_on_timeout():
    class ErrRec:
        def chat_completion(self, messages, **k):
            raise RuntimeError("read operation timed out")
    w = WriterAgent(llm_client=ErrRec())
    long_text = "正文" * 6000
    out = w.polish_chapter(long_text, {"title": "t"}, llm_client=ErrRec())
    # 超时失败时保留各场景原文，以 ※ 分隔
    assert "正文" in out
    assert "※" in out
    assert len(out) > len(long_text) * 0.5
