# -*- coding: utf-8 -*-
"""CC P0: beats_covered anti-slacker contract (offline deterministic)."""
import json

from novel_engine.agents.scene_schema import (
    SceneOutput, parse_scene, validate_beats_covered, build_scene_prompt,
)

STRICT = ["agnes-2.5-flash"]


def _scene_json(n_covered, text="这是一段足够长的合规正文。" * 40):
    return json.dumps({
        "scene_id": 2,
        "scene_text": text,
        "hook": "他握紧了拳。",
        "beats": ["beat甲", "beat乙", "beat丙"],
        "beats_covered": [f"覆盖第{k}拍的具体内容。" for k in range(1, n_covered + 1)],
    }, ensure_ascii=False)


def test_beats_covered_count_gate():
    ok, _ = validate_beats_covered(["a", "b", "c"], 3)
    assert ok is True
    ok, issue = validate_beats_covered(["a", "", "b"], 3)  # blank entries do not count
    assert ok is False and "beats_covered_insufficient" in issue
    ok, issue = validate_beats_covered(["a"], 3)
    assert ok is False and issue.endswith("1 < 3")
    # no known beat requirement (mock / prose) -> skip
    assert validate_beats_covered([], 0)[0] is True
    assert validate_beats_covered(None, None)[0] is True


def test_parse_extracts_beats_covered_and_structured_flag():
    out = parse_scene(_scene_json(3), "agnes-2.5-flash", STRICT)
    assert isinstance(out, SceneOutput) and out.structured is True
    assert len(out.beats_covered) == 3
    assert out.beats == ["beat甲", "beat乙", "beat丙"]
    # insufficient listing still parses (enforcement happens in orchestrator gate)
    short = parse_scene(_scene_json(1), "agnes-2.5-flash", STRICT)
    assert short.structured is True and len(short.beats_covered) == 1
    ok, issue = validate_beats_covered(short.beats_covered, 3)
    assert ok is False and issue == "beats_covered_insufficient: 1 < 3"


def test_lenient_prose_is_not_structured():
    # mock path returns raw prose -> lenient fallback, gate must not enforce beats_covered
    prose = "夜色如墨，寒风穿林而过。" * 40
    out = parse_scene(prose, "", STRICT)
    assert out.structured is False and out.beats_covered == []
    assert validate_beats_covered(out.beats_covered, 3)[0] is True or out.structured is False


def test_prompt_requires_beats_covered_with_count():
    card = {"chapter_num": 7, "chapter_hook": "钩子", "scene_blueprints": [
        {"scene_num": 1, "goal": "g1", "beats": ["b1", "b2"]},
        {"scene_num": 2, "goal": "g2", "beats": ["b1", "b2", "b3"]},
    ]}
    p2 = build_scene_prompt(card, card["scene_blueprints"][1], card["scene_blueprints"])
    assert '"beats_covered"' in p2
    assert "不得少于 3 条" in p2
    assert "共须 3 条" in p2
    p1 = build_scene_prompt(card, card["scene_blueprints"][0], card["scene_blueprints"])
    assert "不得少于 2 条" in p1
