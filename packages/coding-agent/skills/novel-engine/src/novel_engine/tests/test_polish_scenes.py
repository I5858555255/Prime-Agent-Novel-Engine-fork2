"""pkg2c: 测试 WriterAgent.polish_scenes() 和 orchestrator W3 修复。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from novel_engine.agents.scene_schema import SceneOutput
from novel_engine.agents.writer_agent import WriterAgent


def _make_scene(scene_id, text, hook="", beats=None):
    return SceneOutput(scene_id, text, hook, beats or [])


# 辅助：构造足够长的中文场景文本（>= 200 字，满足 _coerce_polish_text 下限）
def _long_scene_text(prefix, n=200):
    return prefix + "正文内容段落" * n


# ── 1. 数量/顺序/scene_id 不变；不含 ※ ──────────────────────────────

def test_polish_scenes_preserves_order_and_no_marker():
    """场景数量、顺序、scene_id 不变；组装后不含 ※。"""

    class Rec:
        def chat_completion(self, messages, **k):
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    w = WriterAgent(llm_client=Rec())
    scenes = [
        _make_scene(1, _long_scene_text("场景一正文内容")),
        _make_scene(2, _long_scene_text("场景二正文内容")),
        _make_scene(3, _long_scene_text("场景三正文内容")),
    ]
    task_card = {"title": "test_ch", "scene_blueprints": [
        {"scene_num": 1}, {"scene_num": 2}, {"scene_num": 3}
    ]}

    polished, flags = w.polish_scenes(scenes, task_card, client=Rec())

    assert len(polished) == 3
    assert [s.scene_id for s in polished] == [1, 2, 3]
    assert all(f is True for f in flags)

    # 组装后不应含 ※
    assembled = "\n\n".join(s.scene_text for s in polished)
    if polished[-1].hook:
        assembled += "\n\n" + polished[-1].hook
    assert "※" not in assembled


# ── 2. 客户端异常或退化输出 → 保留原文，polished=False ───────────────

def test_polish_scenes_fallback_on_exception():
    """某场景 client 抛异常时，该场景保留原文，polished=False。"""
    class ErrRec:
        call_count = 0
        def chat_completion(self, messages, **k):
            self.call_count += 1
            if self.call_count == 2:
                raise RuntimeError("network timeout")
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    w = WriterAgent(llm_client=ErrRec())
    scenes = [
        _make_scene(1, _long_scene_text("场景一正文内容")),
        _make_scene(2, _long_scene_text("场景二正文内容")),
        _make_scene(3, _long_scene_text("场景三正文内容")),
    ]
    task_card = {"title": "test_ch", "scene_blueprints": [
        {"scene_num": 1}, {"scene_num": 2}, {"scene_num": 3}
    ]}

    polished, flags = w.polish_scenes(scenes, task_card, client=ErrRec())

    assert len(polished) == 3
    assert flags[0] is True
    assert flags[1] is False   # 异常场景
    assert flags[2] is True
    # 异常场景保留原文
    assert polished[1].scene_text == scenes[1].scene_text


def test_polish_scenes_fallback_on_degenerate():
    """返回退化文本（非中文）→ 保留原文。"""
    class BadRec:
        def chat_completion(self, messages, **k):
            content = messages[0]["content"]
            return {"role": "assistant", "content": content.split("【正文】\n", 1)[-1] + "English_LEAK"}

    w = WriterAgent(llm_client=BadRec())
    orig_text = _long_scene_text("原始正文内容")
    scenes = [_make_scene(1, orig_text)]
    task_card = {"title": "t", "scene_blueprints": [{"scene_num": 1}]}

    polished, flags = w.polish_scenes(scenes, task_card, client=BadRec())

    assert len(polished) == 1
    assert flags[0] is False
    assert polished[0].scene_text == orig_text


# ── 3. prompt 包含相邻场景锚点 ───────────────────────────────────────

def test_polish_scenes_includes_anchors():
    """fake 捕获的 prompt 中包含上一场尾和下一场头。"""
    captured_prompts = []

    class CaptureRec:
        def chat_completion(self, messages, **k):
            captured_prompts.append(messages[0]["content"])
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    w = WriterAgent(llm_client=CaptureRec())
    scenes = [
        _make_scene(1, "A" * 300 + " scene1_end"),
        _make_scene(2, "B" * 300 + " scene2_end"),
        _make_scene(3, "C" * 300 + " scene3_end"),
    ]
    task_card = {"title": "anchor_test", "scene_blueprints": [
        {"scene_num": 1}, {"scene_num": 2}, {"scene_num": 3}
    ]}

    w.polish_scenes(scenes, task_card, client=CaptureRec())

    assert len(captured_prompts) == 3
    # 场景 1：有 next_anchor，无 prev_anchor
    assert "上一场景结尾" not in captured_prompts[0]
    assert "下一场景开头" in captured_prompts[0]
    # 场景 2：有 prev_anchor 和 next_anchor
    assert "上一场景结尾" in captured_prompts[1]
    assert "下一场景开头" in captured_prompts[1]
    # 场景 3：有 prev_anchor，无 next_anchor
    assert "上一场景结尾" in captured_prompts[2]
    assert "下一场景开头" not in captured_prompts[2]


# ── 4. hook 原样保留，不出现在 scene_text 中 ───────────────────────

def test_hook_preserved_and_not_in_scene_text():
    """hook 字段原样保留，且不混入 scene_text。"""
    class EchoRec:
        def chat_completion(self, messages, **k):
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    w = WriterAgent(llm_client=EchoRec())
    scenes = [
        _make_scene(1, _long_scene_text("正文段落一"), hook="【本章悬念：他发现了一封信】"),
        _make_scene(2, _long_scene_text("正文段落二"), hook=""),
    ]
    task_card = {"title": "hook_test", "scene_blueprints": [
        {"scene_num": 1}, {"scene_num": 2}
    ]}

    polished, flags = w.polish_scenes(scenes, task_card, client=EchoRec())

    assert polished[0].hook == "【本章悬念：他发现了一封信】"
    assert polished[1].hook == ""
    assert "本章悬念" not in polished[0].scene_text
    assert "本章悬念" not in polished[1].scene_text


# ── 5. 扩写生效回归（覆盖 W3）────────────────────────────────────────

def test_expand_effect_regression():
    """扩写结果进入最终成稿文本（直接操作 SceneOutput，不经过 call_llm）。"""
    w = WriterAgent(llm_client=None)
    # 模拟偏短场景（触发扩写）
    short_text = "短" * 50  # 50 chars，远低于 0.6 target
    scenes = [
        _make_scene(1, short_text, hook=""),
        _make_scene(2, _long_scene_text("正常场景内容"), hook=""),
    ]

    # 直接模拟扩写：fake 返回更长且含唯一标记的文本
    unique_marker = "◇扩写锚句X1◇"
    expanded_text = unique_marker + " 这是扩写后的更丰富内容 " + short_text
    scenes[0].scene_text = expanded_text

    assert unique_marker in scenes[0].scene_text
    assert len(scenes[0].scene_text) > len(short_text)

    # 模拟 polish（client 原样返回 scene_text）
    class EchoRec:
        def chat_completion(self, messages, **k):
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    polished, flags = w.polish_scenes(scenes, {"title": "expand_test"}, client=EchoRec())

    # 组装成稿
    assembled = "\n\n".join(s.scene_text for s in polished)
    if polished[-1].hook:
        assembled += "\n\n" + polished[-1].hook

    # 断言：扩写后的内容进入了成稿
    assert unique_marker in assembled
    # 断言：不含 ※
    assert "※" not in assembled


def test_w3_structural_fix():
    """W3 结构性修复：扩写修改 scene_text 后，组装的 novel_text 包含新内容。"""
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    class FakeClient:
        call_count = 0
        def chat_completion(self, messages, **k):
            self.call_count += 1
            content = messages[0]["content"]
            if "扩写" in content:
                return "◇扩写锚句W3◇ 这是扩写后的内容 " + content
            # polish 调用：原样返回 scene_text 部分
            body = content.split("【正文】\n", 1)[-1]
            return {"role": "assistant", "content": "润色后的" + body}
        def close(self):
            pass

    fake = FakeClient()
    w = WriterAgent(llm_client=fake)
    unique_marker = "◇扩写锚句W3◇"
    # 模拟 generate_full_chapter 产出的 last_scenes
    w.last_scenes = [
        _make_scene(1, "短" * 30, hook=""),   # 偏短
        _make_scene(2, _long_scene_text("正常场景内容"), hook=""),
    ]

    # 模拟 _stage_write 的关键路径：先扩写，再 polish_scenes，再组装
    scenes_list = w.last_scenes
    # 扩写（直接赋值，模拟 call_llm 成功）
    for s in scenes_list:
        if len(s.scene_text) < 50:
            s.scene_text = unique_marker + " 扩写后更丰富的正文内容 " + s.scene_text

    # polish_scenes
    polished, flags = w.polish_scenes(scenes_list, {"title": "w3_test"}, client=fake)
    w.last_scenes = polished

    # 组装
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.root = Path(__file__).parent.parent
    assembled = orch._assemble_chapter_text(polished)

    # 断言：扩写后的内容进入了成稿
    assert unique_marker in assembled
    # 断言：不含 ※
    assert "※" not in assembled


# ── 6. 异常时成稿等于原文（不含报错标记）────────────────────────────

def test_expand_failure_preserves_original():
    """扩写抛异常时，最终成稿等于原文，不含任何方括号/【】/报错标记串。"""
    w = WriterAgent(llm_client=None)
    scenes = [
        _make_scene(1, _long_scene_text("原始正文内容"), hook=""),
        _make_scene(2, _long_scene_text("另一场景内容"), hook=""),
    ]
    w.last_scenes = scenes

    # 扩写失败（直接跳过，保留原文）
    # polish 也失败（client 抛异常）
    class FailRec:
        def chat_completion(self, messages, **k):
            raise RuntimeError("service unavailable")

    polished, flags = w.polish_scenes(scenes, {"title": "fail_test"}, client=FailRec())

    # 组装后的成稿不应含报错标记
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.root = Path(__file__).parent.parent
    assembled = orch._assemble_chapter_text(polished)

    assert "【" not in assembled
    assert "】" not in assembled
    assert "※" not in assembled
    assert "error" not in assembled.lower()
    assert "service unavailable" not in assembled


# ── 7. resume 幂等：journal 已有场景不被二次润色 ─────────────────────

def test_resume_idempotent():
    """polish_scenes 对相同输入可重复调用（幂等语义测试）。"""
    class CountRec:
        call_count = 0
        def chat_completion(self, messages, **k):
            self.call_count += 1
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    w = WriterAgent(llm_client=CountRec())
    scenes = [
        _make_scene(1, _long_scene_text("场景一内容"), hook="悬念一"),
        _make_scene(2, _long_scene_text("场景二内容"), hook="悬念二"),
    ]
    task_card = {"title": "resume_test", "scene_blueprints": [
        {"scene_num": 1}, {"scene_num": 2}
    ]}

    # 第一次 polish
    p1, f1 = w.polish_scenes(scenes, task_card, client=CountRec())
    assert len(p1) == 2
    assert all(f is True for f in f1)

    # 第二次 polish（模拟 resume）
    p2, f2 = w.polish_scenes(p1, task_card, client=CountRec())
    assert len(p2) == 2
    # hook 保持不变
    assert p2[0].hook == "悬念一"
    assert p2[1].hook == "悬念二"
# ── 8. resume：1 journaled + 1 new → journaled 调用 0 次 ─────────────────

def test_resume_one_journaled_one_new_no_calls_on_journal():
    """1 个 journaled + 1 个 new，对 journaled 场景 chat_completion 调用次数为 0，
    对 new 场景正常调用；最终成稿中 journaled 文本逐字不变，两场景顺序不变。"""
    class CountRec:
        def __init__(self):
            self.call_count = 0
        def chat_completion(self, messages, **k):
            self.call_count += 1
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    w = WriterAgent(llm_client=CountRec())
    journaled_text = _long_scene_text("已发布场景正文内容")
    new_text = _long_scene_text("新生成场景正文内容")
    scenes = [
        _make_scene(1, journaled_text, hook="悬念一"),
        _make_scene(2, new_text, hook="悬念二"),
    ]
    task_card = {"title": "resume_partial", "scene_blueprints": [
        {"scene_num": 1}, {"scene_num": 2}
    ]}

    # only_ids 只包含 new scene (2)
    p, f = w.polish_scenes(scenes, task_card, client=CountRec(), only_ids={2})

    assert len(p) == 2
    assert [s.scene_id for s in p] == [1, 2]
    # journaled 场景（id=1）调用次数为 0，保留原文
    assert p[0].scene_text == journaled_text
    assert f[0] is False
    # new 场景（id=2）被润色
    assert f[1] is True
    assert p[1].scene_text != new_text  # 被润色了


# ── 9. 全 resume：所有场景已 journal → chat_completion 总调用次数为 0 ───────

def test_resume_all_journaled_zero_calls():
    """全 resume（全部 journaled）：总 chat_completion 次数为 0，
    成稿等于原始场景文本拼接。"""
    class CountRec:
        def __init__(self):
            self.call_count = 0
        def chat_completion(self, messages, **k):
            self.call_count += 1
            content = messages[0]["content"]
            return {"role": "assistant", "content": content}

    w = WriterAgent(llm_client=CountRec())
    scenes = [
        _make_scene(1, _long_scene_text("场景一正文")),
        _make_scene(2, _long_scene_text("场景二正文")),
    ]
    task_card = {"title": "resume_all", "scene_blueprints": [
        {"scene_num": 1}, {"scene_num": 2}
    ]}

    # only_ids 为空集合（全部 journaled）
    p, f = w.polish_scenes(scenes, task_card, client=CountRec(), only_ids=set())

    assert len(p) == 2
    assert all(fl is False for fl in f)
    # 所有场景文本不变
    assert p[0].scene_text == scenes[0].scene_text
    assert p[1].scene_text == scenes[1].scene_text
    # 顺序不变
    assert [s.scene_id for s in p] == [1, 2]


# ── 10. polish 标记写入 state 文件 ─────────────────────────────────────────

def test_polish_flags_written_to_state():
    """跑完规范路径后能从 state 文件读回 polish 标记，scene_id 与 bool 对应正确。"""
    import json
    import shutil
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from novel_engine.agents.writer_agent import WriterAgent
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    class CountRec:
        def __init__(self):
            self.call_count = 0
        def chat_completion(self, messages, **k):
            self.call_count += 1
            content = messages[0]["content"]
            return {"role": "assistant", "content": "润色后的" + content.split("【正文】\n", 1)[-1]}

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        # Copy real config so PipelineOrchestrator can load it
        _src_config = Path(__file__).parent.parent / "config"
        _dst_config = root / "config"
        shutil.copytree(_src_config, _dst_config)
        (root / "chapters" / "state").mkdir(parents=True, exist_ok=True)
        (root / "chapters" / "draft").mkdir(parents=True, exist_ok=True)

        # Mock ModelRouter to avoid API key validation
        with patch('novel_engine.pipeline.pipeline_orchestrator.ModelRouter'):
            orch = PipelineOrchestrator(project_root=root)
        orch.writer = WriterAgent(llm_client=CountRec())

        scenes = [
            _make_scene(1, _long_scene_text("场景一正文")),
            _make_scene(2, _long_scene_text("场景二正文")),
            _make_scene(3, _long_scene_text("场景三正文")),
        ]
        flags = [True, False, True]

        orch._write_polish_flags(1, scenes, flags)

        state_path = root / "chapters" / "state" / "chapter_1_polish.json"
        assert state_path.exists(), "polish state file should be written"

        data = json.loads(state_path.read_text(encoding="utf-8"))
        assert len(data) == 3
        assert data[0] == {"scene": 1, "polished": True}
        assert data[1] == {"scene": 2, "polished": False}
        assert data[2] == {"scene": 3, "polished": True}
        orch.close()

# ── 11. polish 标记原子写入（不留下 tmp 文件）─────────────────────────────

def test_polish_flags_atomic_write_no_tmp():
    """写完 polish flags 后，tmp 文件不应残留。"""
    import shutil
    import tempfile
    from pathlib import Path
    from unittest.mock import patch

    from novel_engine.agents.writer_agent import WriterAgent
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        _src_config = Path(__file__).parent.parent / "config"
        _dst_config = root / "config"
        shutil.copytree(_src_config, _dst_config)
        (root / "chapters" / "state").mkdir(parents=True, exist_ok=True)

        # Mock ModelRouter to avoid API key validation
        with patch('novel_engine.pipeline.pipeline_orchestrator.ModelRouter'):
            orch = PipelineOrchestrator(project_root=root)
        orch.writer = WriterAgent()
        scenes = [_make_scene(1, _long_scene_text("内容")), _make_scene(2, _long_scene_text("内容"))]
        flags = [True, False]
        orch._write_polish_flags(2, scenes, flags)
        state_path = root / "chapters" / "state" / "chapter_2_polish.json"
        tmp_path = state_path.with_suffix(".tmp")
        assert not tmp_path.exists(), "tmp file should not remain after atomic write"
        orch.close()


def test_orchestrator_imports_scene_targets():
    import novel_engine.pipeline.pipeline_orchestrator as _o
    assert callable(getattr(_o, "derive_scene_targets", None))
