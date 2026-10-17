# -*- coding: utf-8 -*-
"""CC round-25 offline tests:
- P0-1 近空章内救援阶梯 L2(聚焦重写)/L3(单beat拆分)/L4(整章重排门槛≥2场耗尽)
- P0-2 整章一次性 hook/style/innovation 文学性重写（指令/解析/确定性锚点替换）
- P0-3 scene_craft_elements 确定性轮换与 writer 技法注入（非范文、不新增检测门）
全部零 LLM：LLM 由 fake writer / 纯字符串驱动。
"""
import json
import random
import tempfile
from pathlib import Path

from novel_engine.agents.scene_schema import SceneOutput, build_scene_prompt
from novel_engine.agents import craft_elements as ce
from novel_engine.agents import literary_pass as lp
from novel_engine.core.errors import ChapterResampleRequiredError
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


_POOL = "的一是在了不人有我他这中大来上个国地地道说也子就去得那要下生会自之着过子时年出么" * 8


def _valid_text(seed: int, n: int = 2400) -> str:
    rng = random.Random(seed)
    chars = [rng.choice(_POOL) for _ in range(n)]
    for k in range(40, n - 1, 47):
        chars[k] = "。"
    return "".join(chars).rstrip("。") + "。"


def _prose(n: int = 420) -> str:
    rng = random.Random(n)
    chars = [rng.choice(_POOL) for _ in range(n)]
    for k in range(20, n - 1, 37):
        chars[k] = "。"
    return "".join(chars).rstrip("。") + "。"


def _valid_scene(sid: int, seed: int) -> SceneOutput:
    return SceneOutput(sid, _valid_text(seed), "章末钩子悬念。", ["b1", "b2", "b3"],
                       beats_covered=["c1", "c2", "c3"], structured=True, finish_reason="stop")


def _near(sid: int) -> SceneOutput:
    return SceneOutput(sid, "近空。", "", [], beats_covered=[], structured=True, finish_reason="stop")


class _FakeWriter:
    """L1 始终摆烂近空；L2/L3 可按场景配置返回。"""
    def __init__(self, focused=None, beats=None):
        self.calls_l1 = 0
        self.focused_calls = []
        self.beat_calls = []
        self._focused = focused or {}
        self._beats = beats or {}

    def generate_scene(self, task_card, bp, syn, **kw):
        self.calls_l1 += 1
        return _near(int(bp["scene_num"]))

    def focused_rewrite_scene(self, task_card, bp, target):
        sid = int(bp["scene_num"])
        self.focused_calls.append(sid)
        return self._focused.get(sid, "")

    def beat_split_rewrite_scene(self, task_card, bp, target):
        sid = int(bp["scene_num"])
        self.beat_calls.append(sid)
        return self._beats.get(sid, "")


def _orch(writer):
    tmp = tempfile.mkdtemp()
    Path(tmp, "config").mkdir(parents=True, exist_ok=True)
    Path(tmp, "config", "runtime_config.json").write_text(
        json.dumps({"llm": {"use_mock": True}, "chapter_target_chars": 10000}),
        encoding="utf-8")
    o = PipelineOrchestrator.__new__(PipelineOrchestrator)
    o.root = tmp
    o.writer = writer
    o._current_synopsis_text = ""
    return o


def _card():
    return {
        "chapter_num": 1,
        "timeline_anchor": {"max_time_progression": "当日"},
        "scene_blueprints": [
            {"scene_num": i, "goal": f"目标{i}", "location": "雾隐村",
             "characters": ["陆烬", "陈老根"], "beats": ["b1", "b2", "b3"]}
            for i in (1, 2, 3)
        ],
    }


# ---------- P0-3 craft_elements ----------

def test_craft_rotation_adjacent_and_across_chapters_distinct():
    def hooks(ch):
        tc = {"chapter_num": ch, "scene_blueprints": [{"scene_num": i} for i in range(1, 5)]}
        ce.ensure_scene_craft_elements(tc)
        return [b["scene_craft_elements"]["hook_type"] for b in tc["scene_blueprints"]]
    h0 = hooks(0)
    assert len(set(h0)) == 4  # 章内相邻不重复
    h1 = hooks(1)
    assert h1[0] != h0[0]     # 同一场位相邻章也不同
    # 全部取自合法枚举
    for h in h0 + h1:
        assert h in ce.HOOK_KEYS


def test_craft_preserves_model_info_reversal_and_is_idempotent():
    tc = {"chapter_num": 2, "scene_blueprints": [{
        "scene_num": 1,
        "scene_craft_elements": {"info_reversal_point": {"present": True, "content": "陈老根实为武者"}}}]}
    ce.ensure_scene_craft_elements(tc)
    cr = tc["scene_blueprints"][0]["scene_craft_elements"]
    assert cr["hook_position"] == "scene_end"
    assert cr["info_reversal_point"] == {"present": True, "content": "陈老根实为武者"}
    hook_before = cr["hook_type"]
    ce.ensure_scene_craft_elements(tc)  # 幂等：再来一遍不改变
    assert tc["scene_blueprints"][0]["scene_craft_elements"]["hook_type"] == hook_before
    assert tc["scene_blueprints"][0]["scene_craft_elements"]["info_reversal_point"]["content"] == "陈老根实为武者"


def test_build_scene_prompt_injects_craft_type_not_sample_prose():
    bp = {"scene_num": 1, "location": "雾隐村", "characters": ["陆烬"],
          "beats": ["b1", "b2", "b3"]}
    tc = {"chapter_num": 1, "scene_blueprints": [bp]}
    ce.ensure_scene_craft_elements(tc)
    p = build_scene_prompt(tc, tc["scene_blueprints"][0], tc["scene_blueprints"])
    assert "文学性技法要求" in p
    assert "不是范文" in p
    # 无 craft 的旧卡：不注入（向后兼容，不扰动既有 prompt 断言）
    bp2 = {"scene_num": 1, "location": "雾隐村", "characters": ["陆烬"], "beats": ["b1", "b2", "b3"]}
    tc2 = {"chapter_num": 1, "scene_blueprints": [bp2]}
    p2 = build_scene_prompt(tc2, tc2["scene_blueprints"][0], tc2["scene_blueprints"])
    assert "文学性技法要求" not in p2
    # CC25 末尾最强约束（防空 content/提前结束）
    assert "禁止空 content" in p


# ---------- P0-1 ladder ----------

def test_l2_focused_rewrite_rescues_without_chapter_resample():
    w = _FakeWriter(focused={1: _prose()})
    o = _orch(w)
    scenes = [_near(1), _valid_scene(2, 22), _valid_scene(3, 33)]
    o._validate_and_regen_scenes(scenes, _card(), 1)  # 不抛
    assert w.focused_calls == [1]      # 场1 进入 L2
    assert w.beat_calls == []          # L2 成功则不进 L3
    assert len(scenes[0].scene_text) >= 300


def test_l3_beat_split_used_when_l2_still_empty():
    w = _FakeWriter(focused={1: ""}, beats={1: _prose(360)})
    o = _orch(w)
    scenes = [_near(1), _valid_scene(2, 22), _valid_scene(3, 33)]
    o._validate_and_regen_scenes(scenes, _card(), 1)
    assert w.focused_calls == [1]
    assert w.beat_calls == [1]
    assert len(scenes[0].scene_text) >= 300


def test_level4_resample_only_when_two_scenes_exhaust_l2_l3():
    w = _FakeWriter()  # 所有场 L1/L2/L3 全失败
    o = _orch(w)
    scenes = [_near(1), _near(2), _valid_scene(3, 33)]
    try:
        o._validate_and_regen_scenes(scenes, _card(), 1)
        assert False, "expected ChapterResampleRequiredError"
    except ChapterResampleRequiredError as e:
        assert sorted(e.scene_ids) == [1, 2]
    assert sorted(w.focused_calls) == [1, 2]
    assert sorted(w.beat_calls) == [1, 2]


def test_single_exhausted_scene_kept_alive_not_halt():
    w = _FakeWriter()  # 仅场1耗尽；场2/3合格 → 不整章重排
    o = _orch(w)
    scenes = [_near(1), _valid_scene(2, 22), _valid_scene(3, 33)]
    o._validate_and_regen_scenes(scenes, _card(), 1)  # 不抛
    assert w.focused_calls == [1] and w.beat_calls == [1]


# ---------- P0-2 literary pass (pure parse/apply) ----------

def test_select_and_strip_literary_issues():
    review = {"issues": [
        {"dimension": "hook_strength", "description": "场3结尾平淡", "suggested_fix": "加悬念", "scene_ids": [3]},
        {"dimension": "style_match", "description": "scene2文风不符", "scene_ids": [2]},
        {"dimension": "plot", "description": "情节缺一环", "scene_ids": [1]},
        {"dimension": "innovation", "description": "套话", "scene_ids": []},
    ]}
    sel = lp.select_literary_issues(review)
    dims = sorted(x["dimension"] for x in sel)
    assert dims == ["hook", "innovation", "style"]
    assert sel[0]["scene_ids"] == [3]
    kept = lp.strip_literary_from_review(review)["issues"]
    assert [i["dimension"] for i in kept] == ["plot"]


def test_literary_weak_by_raw_score():
    assert lp.literary_weak({"scores": {"hook": 1}}) is True   # 1/8 < .85
    assert lp.literary_weak({"scores": {"plot": 1}}) is False


def test_literary_prompt_carries_concrete_named_issues():
    issues = [{"dimension": "hook", "scene_ids": [3], "description": "场3结尾平淡缺悬念",
               "suggested_fix": "停在威胁将现"}]
    p = lp.build_literary_prompt([(3, "某正文。")], issues, 5)
    assert "场3结尾平淡缺悬念" in p
    assert "严禁改变情节走向" in p


def test_parse_literary_edits_tolerant():
    raw = "```json\n" + json.dumps({"edits": [
        {"scene_id": 2, "op": "replace_tail", "anchor": "他缓缓抬头看向",
         "replacement": "他猛地顿住，黑暗里那点光正一寸寸逼近。"}]}, ensure_ascii=False) + "\n```"
    edits = lp.parse_literary_edits(raw)
    assert len(edits) == 1 and edits[0]["scene_id"] == 2 and edits[0]["op"] == "replace_tail"
    assert lp.parse_literary_edits("not json") == []


def test_apply_replace_tail_and_span():
    tail_old = "他缓缓起身走向门口，夜色很深，一切都结束了。"
    span_old = "村中死气沉沉毫无半点生气令人昏昏欲睡。"
    _pad = _valid_text(99, n=320)  # 大段不变正文，确保总改动远低于 12% 总护栏
    texts = {3: _pad + "前文若干内容。" + span_old + "中段承接。" + tail_old}
    edits = [
        {"scene_id": 3, "op": "replace_tail", "anchor": "他缓缓起身走向门口",
         "replacement": "他指尖刚碰到门闩，门外那口属于自己的棺材，轻轻响了一下。"},
        {"scene_id": 3, "op": "replace_span", "anchor": "村中死气沉沉", "anchor_end": "昏昏欲睡。",
         "replacement": "村巷静得只剩自己的脚步，连一声犬吠也无。"},
    ]
    new, applied, notes = lp.apply_literary_edits(texts, edits)
    assert applied == 2, notes
    assert "棺材" in new[3] and "村巷静得" in new[3]
    assert "死气沉沉" not in new[3] and "一切都结束了" not in new[3]
    assert new[3].startswith(_pad)


def test_apply_rejects_non_unique_anchor_and_latin_replacement():
    texts = {1: "重复的句子。重复的句子。结尾收束于此。"}
    edits = [
        {"scene_id": 1, "op": "replace_span", "anchor": "重复的句子", "anchor_end": "重复的句子。",
         "replacement": "english replacement here"},      # 锚点非唯一 + 替换含英文
    ]
    new, applied, notes = lp.apply_literary_edits(texts, edits)
    assert applied == 0
    assert new == texts


def test_replace_tail_without_anchor_targets_last_sentence():
    # CC25 硬化：replace_tail 免锚点，系统自动替换该场景最后一整句
    pad = _valid_text(7, n=300)
    texts = {3: pad + "村巷静。他缓缓走向门口，一切都结束了。"}
    edits = [{"scene_id": 3, "op": "replace_tail", "anchor": "",
              "replacement": "门外那口属于自己的棺材，忽然轻轻响了一下。"}]
    new, applied, notes = lp.apply_literary_edits(texts, edits)
    assert applied == 1, notes
    assert "一切都结束了" not in new[3]
    assert new[3].endswith("忽然轻轻响了一下。")
    assert new[3].startswith(pad + "村巷静。")


def test_replace_tail_falls_back_when_anchor_not_verbatim():
    # 复现 r29：flash 给的 anchor 与原文并非逐字一致（count=0），仍应确定性落到末句替换
    pad = _valid_text(8, n=300)
    texts = {3: pad + "风声停了。他转身回屋，今夜就这样过去。"}
    edits = [{"scene_id": 3, "op": "replace_tail", "anchor": "模型凭印象写的非原文开头",
              "replacement": "黑暗里，那双一直注视着他的眼睛终于眨了一下。"}]
    new, applied, notes = lp.apply_literary_edits(texts, edits)
    assert applied == 1, notes
    assert "今夜就这样过去" not in new[3]
    assert new[3].endswith("眨了一下。")


def test_replace_tail_allows_short_punchy_hook_r32():
    # 复现 r32：被替换的铺垫尾句很长（~90字），模型给短促有力的钩子句（~24字），
    # 旧的 0.5 长度下限会误杀；新下限 0.18 应放行（整章长度由编排层字数门兜底）。
    pad = _valid_text(9, n=320)
    long_tail = "他站在原地想了很久，关于今夜的种种经过，关于这些年村里那些说不清道不明的旧事，心里竟一点波澜也没有了。"
    texts = {4: pad + "雨还在下。" + long_tail}
    punchy = "他低头一看，怀里的婴儿，正对他笑。"
    edits = [{"scene_id": 4, "op": "replace_tail", "anchor": "", "replacement": punchy}]
    new, applied, notes = lp.apply_literary_edits(texts, edits)
    assert applied == 1, notes
    assert new[4].endswith(punchy)
    assert "一点波澜也没有了" not in new[4]


def test_replace_span_lenient_lcs_and_optional_anchor_end_r32():
    # flash 锚点尾部多带一个语气字（非逐字），但核心短语逐字且唯一 -> LCS 兜底定位；
    # 且 anchor_end 缺省时仅替换该短语本身。
    pad = _valid_text(6, n=300)
    texts = {2: pad + "他抬头。村巷静得只剩自己的脚步，他握紧了刀。"}
    raw = (
        '{"edits":[{"scene_id":2,"op":"replace_span",'
        '"anchor":"村巷静得只剩自己的脚步啊",'
        '"replacement":"巷子空得像坟，只剩脚步在响。"}]}'
    )
    edits = lp.parse_literary_edits(raw)
    assert len(edits) == 1 and not edits[0]["anchor_end"]
    new, applied, notes = lp.apply_literary_edits(texts, edits)
    assert applied == 1, notes
    assert "像坟" in new[2]
    assert "村巷静得只剩自己的脚步" not in new[2]
    assert new[2].startswith(pad + "他抬头。") and "他握紧了刀" in new[2]


def test_replace_span_paraphrase_without_verbatim_core_still_skips():
    # 安全边界：模型整句意译、没有足够长（>=6）的逐字公共段 -> 必须跳过，不可乱替换。
    texts = {1: "他沿着田埂慢慢往家走，露水打湿了裤脚。天色将明未明。"}
    edits = [{"scene_id": 1, "op": "replace_span",
              "anchor": "主人公在乡间小路上归途思索人生",   # 与原文无逐字公共段
              "replacement": "他踏着晨露归去，心中百转千回。"}]
    new, applied, notes = lp.apply_literary_edits(texts, edits)
    assert applied == 0 and new == texts
