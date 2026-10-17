# -*- coding: utf-8 -*-
"""pkg2e: 场景强边界 / 空场景判废 / 钩子归位 / 越界检测 的专门测试。

全部离线、确定性，零网络。
"""
import json
import tempfile
from pathlib import Path

import pytest

from novel_engine.agents.scene_schema import (
    OVERLAP_RATIO,
    _summarize_anchor,
    _truncate_at_sentence_boundary,
    build_scene_prompt,
    detect_scene_overlap,
    parse_scene,
    validate_scene_text,
)
from novel_engine.agents.writer_agent import SceneOutput
from novel_engine.core.errors import SceneUnrecoverableError
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


_POOL = "".join(dict.fromkeys(
    "雾隐村晨暮寒山古道荒林清泉白石幽篁松涛孤舟蓑笠烟波江渚野渡横笛残阳冷月疏星"
    "微雨落霞归雁断鸿远岫空翠湿衣青衫布衣草履芒鞋竹杖破钵灯影书声剑气琴心茶烟"
    "棋韵墨香画卷诗魂酒痕梦影归客行旅羁愁别绪离怀乡心云涛岩壑林霏涧碧岚光"))


def _uniq_cjk(seed, n_chars):
    """生成 n_chars 个中文，任意 8 字子串不重复、跨 seed 的 12-gram 互不相交。"""
    n = len(_POOL)
    base = 2000
    out = []
    total = 0
    i = 0
    while total < n_chars:
        g = seed * base + i
        a = g % n
        b = (a + 1 + (g // n) % (n - 2)) % n
        c = b
        k = 1
        while c == a or c == b:
            c = (b + k) % n
            k += 1
        out.append(_POOL[a] + _POOL[b] + _POOL[c])
        total += 3
        i += 1
    return "".join(out)[:n_chars]


def _card(n=3):
    bps = [
        {"scene_num": i + 1, "goal": f"目标{i+1}", "location": f"地点{i+1}",
         "characters": ["陆烬"], "beats": [f"beat{i+1}a", f"beat{i+1}b"],
         "word_count_target": 2500}
        for i in range(n)
    ]
    return {"chapter_num": 1, "chapter_hook": "章末的悬念钩子", "scene_blueprints": bps}, bps


# ---------- A1: prompt 边界：hook 只给末场景，其余给 forbidden 清单 ----------
def test_prompt_hook_only_visible_to_last_scene():
    card, bps = _card(4)
    p_last = build_scene_prompt(card, bps[-1], bps)
    assert "章末钩子" in p_last and "章末的悬念钩子" in p_last
    for bp in bps[:-1]:
        p = build_scene_prompt(card, bp, bps)
        assert "章末的悬念钩子" not in p, "非末场景不得看到章末钩子"
        assert "不得提前发生场景" in p, "非末场景必须含跨场景禁写清单"
        assert f"场景{bp['scene_num'] + 1}的核心目标" in p


def test_prompt_first_scene_anchor_note_and_prev_anchor():
    card, bps = _card(3)
    p1 = build_scene_prompt(card, bps[0], bps)
    assert "本章为开篇" in p1
    p2 = build_scene_prompt(card, bps[1], bps)
    assert "前情锚点" in p2 and "目标1" in p2  # 前场目标作为锚点
    assert "严禁复述" in p2


def test_prompt_negative_examples_block():
    card, bps = _card(2)
    p = build_scene_prompt(card, bps[0], bps, negative_examples=["重复的整章重写A", "串场事件B"])
    assert "负例" in p and "重复的整章重写A" in p and "串场事件B" in p


# ---------- A2: 锚点 200-250，按句边界回退 ----------
def test_truncate_at_sentence_boundary():
    s = "短句。" * 100  # 400 chars, all sentence ends at multiples of 3
    out = _truncate_at_sentence_boundary(s, 250)
    assert len(out) <= 250
    assert out.endswith("。")
    assert len(out) >= 125  # 不截断得过于激进


def test_truncate_short_passthrough():
    assert _truncate_at_sentence_boundary("一句话。", 250) == "一句话。"


def test_summarize_anchor_bounded():
    bp = {"scene_num": 2, "goal": "前一场景的核心目标",
          "beats": [f"情节描述{i}" for i in range(50)]}
    anchor = _summarize_anchor(bp, max_chars=250)
    assert len(anchor) <= 250
    assert "目标" in anchor


# ---------- C: validate_scene_text 空场景/占位/退化 ----------
def test_validate_rejects_too_short():
    passed, issues = validate_scene_text("只有几个字。", scene_target=2500)
    assert not passed
    assert any("length_too_short" in i for i in issues)


def test_validate_rejects_placeholder_and_repeat():
    p1, i1 = validate_scene_text("正文" + "待补充" + "x" * 400, scene_target=500)
    assert any("placeholder_detected" in i for i in i1)
    repeated = ("他猛地转身挥剑。" * 20)  # 同一句反复
    p2, i2 = validate_scene_text(repeated, scene_target=50)
    assert not p2 and any("high_freq_repeat" in i for i in i2)


def test_validate_flags_midsentence_truncation():
    base = _uniq_cjk(3, 2000)  # 长度达标但末尾无句末标点（疑似 finish_reason=length 截断）
    _, i1 = validate_scene_text(base, 2500)
    assert any("truncated" in x for x in i1)
    _, i2 = validate_scene_text(base + "。", 2500)
    assert not any("truncated" in x for x in i2)


def test_validate_accepts_healthy_unique_prose():
    prose = _uniq_cjk(0, 2000) + "。"
    passed, issues = validate_scene_text(prose, scene_target=2500)
    assert passed, issues


# ---------- A3: 越界 n-gram 检测 ----------
def test_overlap_clean_scenes_empty():
    sc = [SceneOutput(i + 1, _uniq_cjk(i + 1, 1500), "", []) for i in range(3)]
    assert detect_scene_overlap(sc) == {}


def test_overlap_flags_duplicated_scenes():
    dup = "".join(f"完全相同的越界情节文字第{j:04d}段不断出现导致高度重复。" for j in range(120))
    sc = [SceneOutput(1, dup, "", []), SceneOutput(2, dup, "", [])]
    res = detect_scene_overlap(sc)
    assert 1 in res and 2 in res
    assert all(f"{OVERLAP_RATIO:.0%}" in d or "overlap" in d for d in res[1])


# ---------- D: assembly 只拼 scene_text，绝不追加 hook ----------
def test_assemble_does_not_append_hooks():
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    sc = [SceneOutput(1, "正文甲。", "钩子甲", []),
          SceneOutput(2, "正文乙。", "钩子乙", [])]
    out = PipelineOrchestrator._assemble_chapter_text(orch, sc)
    assert out == "正文甲。\n\n正文乙。"
    assert "钩子甲" not in out and "钩子乙" not in out


# ---------- strict JSON：Agnes 的结构化输出不得泄漏 JSON 分隔符到 scene_text ----------
def test_parse_scene_strict_pretty_json_no_fragments():
    import json as _json
    raw = _json.dumps(
        {"scene_id": 2, "scene_text": "陆烬推开木门，院中一片寂静。" * 6,
         "hook": "门外忽然传来脚步声。", "beats": ["推门", "察静"]},
        ensure_ascii=False, indent=4)
    o = parse_scene(raw, "agnes-2.5-flash", ["agnes-2.5-flash"])
    assert o.scene_id == 2
    assert o.hook == "门外忽然传来脚步声。"
    assert '",' not in o.scene_text and '",' not in o.hook
    assert '\n    "' not in o.scene_text  # pretty-JSON 缩进分隔符不得进入正文
    assert o.scene_text.startswith("陆烬")


# ---------- strict JSON：Agnes 围栏+松散 JSON（字符串内真实换行）也必须干净解析 ----------
def test_parse_scene_strict_fenced_loose_json_repaired():
    # 注意：scene_text 的值里是“真实换行”，属于非法严格 JSON，靠 json_repair 容错
    raw = (
        "```json\n"
        "{\n"
        '  "scene_id": 1,\n'
        '  "scene_text": "陆烬在井边醒来，风声很紧。\n\n他撑着地坐起身，指尖触到湿冷的泥。",\n'
        '  "hook": "井口传来脚步。",\n'
        '  "beats": ["醒来", "坐起"]\n'
        "}\n"
        "```"
    )
    o = parse_scene(raw, "agnes-2.5-flash", ["agnes-2.5-flash"])
    assert o.scene_id == 1
    assert "```" not in o.scene_text and '",' not in o.scene_text and '\n    "' not in o.scene_text
    assert "陆烬在井边醒来" in o.scene_text and "湿冷的泥" in o.scene_text
    # lenient 路径同样能解析围栏 JSON 信封
    o2 = parse_scene(raw, "some-other-model", ["agnes-2.5-flash"])
    assert o2.scene_id == 1 and "```" not in o2.scene_text


# ---------- word_count_target 畸形类型容错（模型偶发 list/str/float） ----------
def test_build_scene_prompt_tolerates_nonint_target():
    tc = {"chapter_num": 1, "chapter_hook": "h"}
    bp = {"scene_num": 1, "goal": "g", "location": "loc", "characters": ["陆烬"],
          "beats": ["b1", "b2"], "word_count_target": [2500]}
    p = build_scene_prompt(tc, bp, [bp])
    assert "目标字数：2500字" in p and "1750" in p
    bp2 = dict(bp); bp2["word_count_target"] = "2400"
    p2 = build_scene_prompt(tc, bp2, [bp2])
    assert "目标字数：2400字" in p2


# ---------- director：薄过渡场景仅在超过5场景时才确定性合并（4-5场景不减量） ----------
def test_merge_thin_scene_only_when_more_than_five():
    from novel_engine.agents.chapter_director import merge_thin_scene_blueprints
    rich = lambda n, goal: {"scene_num": n, "location": "村", "characters": ["C001"],
                            "goal": goal, "conflict": "c", "emotion": "e",
                            "beats": ["第一拍事件", "第二拍事件", "第三拍事件"]}
    # 4 场景里即便有一个薄场景也不合并（交给上游任务卡充实度校验/重生）
    four = [
        rich(1, "主角在混沌中消散又被执念拉回，经历完整的死亡到转生过渡"),
        rich(2, "主角以婴儿感官苏醒，逐一辨认湿冷、腥雾与人声，确认重生"),
        {"scene_num": 3, "location": "村", "characters": ["C009"], "goal": "更夫听见啼哭",
         "conflict": "", "emotion": "", "beats": []},
        rich(4, "守夜人发现土地庙中的裸身婴儿并议论异象"),
    ]
    out4 = merge_thin_scene_blueprints(four, 10000)
    assert len(out4) == 4

    # 6 场景且含薄场景：薄场景并入邻场景降到 5。CC round-21 起单场目标被 scene_target
    # 下限（2150）抬升，ask 侧之和可超过章目标，不再严格守恒（章长度门另按 chapter_target）。
    six = [rich(i, f"足够充实的第{i}个场景目标描述内容") for i in range(1, 6)]
    six.append({"scene_num": 6, "location": "庙", "characters": ["C009"],
                "goal": "短暂过渡", "conflict": "", "emotion": "", "beats": []})
    out6 = merge_thin_scene_blueprints(six, 10000)
    assert len(out6) == 5
    assert [b["scene_num"] for b in out6] == [1, 2, 3, 4, 5]
    assert [int(b["word_count_target"]) for b in out6] == [2150] * 5


def test_merge_keeps_all_rich_scenes_and_min_two():
    from novel_engine.agents.chapter_director import merge_thin_scene_blueprints
    rich = lambda n: {"scene_num": n, "goal": "一个足够充实的目标描述" * 2,
                      "characters": [], "beats": ["a", "b"], "location": "", "conflict": "", "emotion": ""}
    four = [rich(1), rich(2), rich(3), rich(4)]
    assert len(merge_thin_scene_blueprints(four, 10000)) == 4
    # 仅2个场景时即便其一偏薄也不强并（director 校验要求至少2场景）
    two = [rich(1), {"scene_num": 2, "goal": "短", "beats": [], "characters": []}]
    out2 = merge_thin_scene_blueprints(two, 10000)
    assert len(out2) == 2


# ---------- C2: 近空场景有界重生 2 次后抛 SceneUnrecoverableError，且不进入扩写 ----------
def test_near_empty_scene_halts_after_two_regen_without_expand(monkeypatch):
    tmp = tempfile.mkdtemp()
    Path(tmp, "config").mkdir(parents=True, exist_ok=True)
    Path(tmp, "config", "runtime_config.json").write_text(
        json.dumps({"llm": {"use_mock": True}, "chapter_target_chars": 10000}),
        encoding="utf-8")

    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.root = tmp

    class _StubWriter:
        def __init__(self):
            self.calls = 0
            self.last_kwargs = None

        def generate_scene(self, task_card, bp, syn, **kw):
            self.calls += 1
            self.last_kwargs = kw
            # 无论重生几次都返回近空文本（模拟模型持续产出近空稿）
            return SceneOutput(int(bp.get("scene_num", 0) or 0), "近空。", "", [])

    stub = _StubWriter()
    orch.writer = stub

    # 标记是否误触扩写（近空场景绝不允许进入扩写分支）
    def _boom(*a, **k):
        raise AssertionError("near-empty scene must not reach expand path")
    monkeypatch.setattr("novel_engine.pipeline.pipeline_orchestrator.call_llm", _boom)

    card, bps = _card(3)
    scenes = [SceneOutput(i + 1, "近空。", "", []) for i in range(3)]
    with pytest.raises(SceneUnrecoverableError) as ei:
        orch._validate_and_regen_scenes(scenes, card, 1)
    assert ei.value.scene_id == 1
    assert stub.calls == 2  # 仅重生 2 次即停机
    # 重生时必须带负例（上一次违规描述）
    assert stub.last_kwargs and stub.last_kwargs.get("negative_examples")
