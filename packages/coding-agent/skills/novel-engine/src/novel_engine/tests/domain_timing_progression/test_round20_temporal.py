# -*- coding: utf-8 -*-
"""CC round-20 offline tests：时间锚越界三层判定与最小切除自救。"""
from novel_engine.quality.temporal_continuity import (
    classify_time_reference, excise_from_text,
)
from novel_engine.core.errors import (
    ChapterQualityGapError, ChapterResampleRequiredError,
)

ANCHOR = "当夜（从深夜到寅时末，约三四个时辰内完成），不跨日"
OPEN_ANCHOR = "数日内，可跨夜"


# ---------- 三层判定（真机正反例） ----------

def test_absolute_flashback_flashforward_violation():
    for s in ["十六岁那年他还在孤儿院。", "三年后他才明白这一点。", "多年以后，村里换了模样。",
              "他想起童年的夏天。", "次年春天雪才化。", "12岁那年他离了家。"]:
        assert classify_time_reference(s, ANCHOR) == "VIOLATION", s


def test_legitimate_same_night_takes_precedence():
    # 即便伴随其他可疑字串，单夜合法时间词优先判合法
    for s in ["到了寅时末，风声渐息。", "三炷香后，陈老根推门而入。", "片刻之后他睁开眼。",
              "一顿饭功夫，雪小了。", "夜半子时，孩子哭了。", "鸡叫三遍，天还没亮。"]:
        assert classify_time_reference(s, ANCHOR) == "LEGITIMATE", s


def test_dawn_boundary_relative_to_anchor():
    s = "次日清晨，雪停了。"
    assert classify_time_reference(s, ANCHOR) == "VIOLATION"          # 封顶寅时末/不跨日
    assert classify_time_reference(s, OPEN_ANCHOR) == "NEUTRAL"      # 锚点允许跨夜


def test_present_scene_child_age_neutral():
    # 当前场景对人物年龄的描述不是回溯
    assert classify_time_reference("孩童约莫七八岁，蜷在草垛里。", ANCHOR) == "NEUTRAL"
    assert classify_time_reference("他叹了口气，什么也没说。", ANCHOR) == "NEUTRAL"


# ---------- 切除粒度 ----------

def test_excise_minimal_clause_preserves_rest():
    t = "夜风很冷，十六岁那年他还在孤儿院挨饿，他攥紧了拳。"
    out, n = excise_from_text(t, ANCHOR)
    assert n >= 1
    assert "孤儿院" not in out and "十六岁" not in out
    assert "夜风很冷" in out and "攥紧了拳" in out       # 合法子句保留
    assert out.endswith("。")


def test_excise_drops_pure_flashback_whole_sentence():
    t = "三年后他才明白这一切。陈老根蹲下身。"
    out, n = excise_from_text(t, ANCHOR)
    assert "三年后" not in out and "明白" not in out
    assert "陈老根蹲下身" in out
    assert n >= 1


def test_excise_leaves_legitimate_text_untouched():
    t = "寅时末风停了，三炷香后陈老根开口，孩子约莫七八岁。"
    out, n = excise_from_text(t, ANCHOR)
    assert n == 0 and out == t


def test_excise_multiline_paragraphs_preserved():
    t = "他睁眼。\n十六岁那年的旧事涌上来，又被他压下。\n雪还在下。"
    out, n = excise_from_text(t, ANCHOR)
    assert n >= 1
    assert "十六岁" not in out
    assert "他睁眼" in out and "雪还在下" in out
    assert "\n" in out


def test_no_violation_returns_zero_and_original():
    t = "一段完全干净的当夜叙述，没有任何越界词。"
    out, n = excise_from_text(t, ANCHOR)
    assert n == 0 and out == t


# ---------- gap 异常语义 ----------

def test_gap_error_is_resample_subclass_with_flags():
    e = ChapterQualityGapError("x", chapter=0, scene_ids=[3],
                               violations=[{"scene_id": 3, "kind": "continuity_inversion"}])
    assert isinstance(e, ChapterResampleRequiredError)
    assert e.replan == "continuity_gap" and e.gap_kind == "continuity_gap"
    assert e.violations[0]["scene_id"] == 3
