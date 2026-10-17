from novel_engine.agents.scene_schema import extract_beats_fallback, parse_scene, slim_scene_blueprint, build_scene_prompt


def test_slim_scene_blueprint_drops_density_fields_keeps_core():
    """CC round-32：蓝图瘦身移除被专用约束块覆盖的大字段，保留场景定义核心。"""
    bp = {
        "scene_num": 2, "narrative_time": "凌晨", "location": "酒馆暗巷",
        "characters": ["上官烈"], "goal": "g", "conflict": "c", "emotion": "e",
        "beats": ["b1", "b2"],
        "concrete_events": [{"event": "x", "observable_action": "y"}],
        "named_interactions": [{"characters": ["a"], "interaction_type": "t", "brief": "z"}],
        "info_reveal_points": [{"type": "伏笔", "content": "q"}],
        "scene_craft_elements": {"x": 1},
        "scene_progression_contract": {"irreversible_change": "不可逆"},
    }
    slim = slim_scene_blueprint(bp)
    assert "concrete_events" not in slim
    assert "named_interactions" not in slim
    assert "info_reveal_points" not in slim
    assert "scene_craft_elements" not in slim
    for k in ("scene_num", "narrative_time", "location", "characters", "goal",
              "conflict", "emotion", "beats", "scene_progression_contract"):
        assert k in slim, f"core field {k} lost"
    assert bp.get("concrete_events"), "原对象不被修改"


def test_build_scene_prompt_slims_blueprint():
    """场景 prompt 的蓝图 JSON 不含瘦身字段（避免超长 prompt 压缩弱模型输出）。"""
    tc = {"chapter_num": 148, "scene_blueprints": [
        {"scene_num": 1, "narrative_time": "子时", "location": "a", "characters": ["x"],
         "goal": "g1", "conflict": "c1", "emotion": "e1", "beats": ["b"],
         "concrete_events": [{"event": "e", "observable_action": "o"}],
         "info_reveal_points": [{"type": "t", "content": "c"}]},
        {"scene_num": 2, "narrative_time": "凌晨", "location": "b", "characters": ["y"],
         "goal": "g2", "conflict": "c2", "emotion": "e2", "beats": ["b1", "b2"],
         "concrete_events": [{"event": "e2", "observable_action": "o2"}]},
    ]}
    prompt = build_scene_prompt(tc, tc["scene_blueprints"][0], tc["scene_blueprints"])
    assert '"concrete_events"' not in prompt
    assert '"info_reveal_points"' not in prompt
    assert '"goal"' in prompt and '"beats"' in prompt
    assert '"g1"' in prompt  # goal 值保留
    assert '"子时"' in prompt  # narrative_time 保留


def test_fallback_extracts_from_text_not_self_report():
    text = "陈老根抱起婴儿走出迷雾，脚步沉重而坚定。村民在槐树下质疑婴儿的来历，议论纷纷不肯散去。"
    beats = extract_beats_fallback(text)
    assert any("陈老根" in b or "婴儿" in b for b in beats)
    assert len(beats) >= 1


def test_fallback_sorts_descending_by_keyword_count():
    lo = "夜色如墨寒风穿林而过久久不散"
    mid1 = "陈老根抱起婴儿走出迷雾，脚步沉重而坚定"
    mid2 = "村民在槐树下质疑婴儿的来历，议论纷纷不肯散去"
    hi = "老槐树下村民围住陈老根，质问婴儿的来历，争吵声惊醒了沉睡的村庄"
    text = "。".join([lo, mid1, mid2, hi]) + "。"
    beats = extract_beats_fallback(text, limit=3)
    assert len(beats) == 3
    assert "争吵声" in beats[0]
    assert not any("夜色如墨" in b for b in beats)

STRICT = ["deepseek-ai/DeepSeek-V3.2"]


def test_strict_parses_clean_json():
    raw = '{"scene_id": 2, "scene_text": "夜色如墨。", "hook": "远处传来脚步声。", "beats": ["夜探"]}'
    out = parse_scene(raw, "deepseek-ai/DeepSeek-V3.2", STRICT)
    assert out.scene_text == "夜色如墨。"
    assert out.hook == "远处传来脚步声。"


def test_lenient_accepts_fenced_fallback():
    raw = "```scene\n夜色如墨。\n```"
    out = parse_scene(raw, "Qwen/Qwen3.5-27B", STRICT)
    assert out.scene_text == "夜色如墨。"


def test_strict_rejects_prose():
    import pytest
    with pytest.raises(ValueError):
        parse_scene("夜色如墨，纯散文无JSON。", "deepseek-ai/DeepSeek-V3.2", STRICT)


# ---- Task B2: writer emits structured scenes (stub-client no-markers test first) ----
import json as _json

import pytest as _pytest

from novel_engine.agents.writer_agent import WriterAgent

# Step-3 contract line (must appear verbatim in the scene prompt).
CONTRACT_LINE = '只输出严格JSON {"scene_id","scene_text","hook","beats","beats_covered"}；scene_text 为纯叙事，禁任何【】括号指令；hook 为完整自然语句，可为空'


def _strict_json(scene_id=1, hook="远处传来脚步声。"):
    return _json.dumps(
        {"scene_id": scene_id, "scene_text": "夜色如墨，寒风穿林而过。",
         "hook": hook, "beats": ["夜探"]},
        ensure_ascii=False,
    )


class _StubClient:
    """Brief-specified stub: chat_completion returns strict JSON + stamped head model."""

    # Declared bound phase model -> _scene_strict_models() treats the stamped model as
    # strict without reading on-disk config, keeping these tests provider-independent.
    models = "deepseek-ai/DeepSeek-V3.2"

    def __init__(self, payloads):
        self.payloads = [payloads] if isinstance(payloads, str) else list(payloads)
        self.calls = 0
        self.last_messages = None

    def chat_completion(self, messages, temperature=None, **kwargs):
        self.calls += 1
        self.last_messages = messages
        payload = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        return {"content": payload, "_model_used": "deepseek-ai/DeepSeek-V3.2"}


@_pytest.fixture
def stub_client():
    return _StubClient(_strict_json())


def test_writer_output_has_no_markers(stub_client):
    w = WriterAgent(llm_client=stub_client)
    scene = w.generate_scene(task_card={"chapter_num": 1}, scene_blueprint={"scene_num": 1}, chapter_synopsis="x")
    body = scene.scene_text + scene.hook
    assert "【" not in body and "】" not in body and "章末钩子" not in body
    assert scene.scene_id == 1


def test_writer_prompt_demands_json_contract(stub_client):
    w = WriterAgent(llm_client=stub_client)
    w.generate_scene(task_card={"chapter_num": 1}, scene_blueprint={"scene_num": 1}, chapter_synopsis="x")
    prompt = "\n".join(m.get("content", "") for m in stub_client.last_messages)
    assert CONTRACT_LINE in prompt


def test_writer_strict_failure_regenerates_once():
    stub = _StubClient(["夜色如墨，纯散文无JSON。", _strict_json()])
    w = WriterAgent(llm_client=stub)
    scene = w.generate_scene(task_card={"chapter_num": 1}, scene_blueprint={"scene_num": 1}, chapter_synopsis="x")
    assert stub.calls == 2
    assert scene.scene_text == "夜色如墨，寒风穿林而过。"


def test_writer_strict_failure_twice_raises():
    stub = _StubClient(["夜色如墨，纯散文无JSON。", "还是散文。"])
    w = WriterAgent(llm_client=stub)
    with _pytest.raises(ValueError):
        w.generate_scene(task_card={"chapter_num": 1}, scene_blueprint={"scene_num": 1}, chapter_synopsis="x")
    assert stub.calls == 2
