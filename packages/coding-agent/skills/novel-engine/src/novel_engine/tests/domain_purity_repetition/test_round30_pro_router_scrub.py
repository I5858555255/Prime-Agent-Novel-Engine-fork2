# -*- coding: utf-8 -*-
"""CC30（DS 五问 Q1/Q2/Q3/Q5）零 LLM 单测：按场 pro 路由+预算、退化窗口状态机、脚手架清洗。"""
from novel_engine.quality import scene_model_router as sr
from novel_engine.quality import degrade_window as dw
from novel_engine.quality import scaffold_scrub as ss


# ============ Q1 按场切 pro ============
def test_short_retry_once_no_switch_twice_yes():
    assert sr.should_switch_scene_pro({"short_retry_count": 1}, {}) is False
    assert sr.should_switch_scene_pro({"short_retry_count": 2}, {}) is True


def test_scene_reasoning_only_two_switch():
    assert sr.should_switch_scene_pro({"reasoning_only_count": 2}, {}) is True


def test_chapter_reasoning_three_forces_unfinished():
    ch = {"reasoning_only_total": 3}
    assert sr.should_switch_scene_pro({"completed": False}, ch) is True
    # 已完成场不重出
    assert sr.should_switch_scene_pro({"completed": True}, ch) is False


def test_latin_stream_direct_pro():
    assert sr.should_switch_scene_pro({"latin_stream_hit": True}, {}) is True


def test_beats_zero_after_retry_pro():
    assert sr.should_switch_scene_pro({"beats_covered": 0, "retry_count": 0}, {}) is False
    assert sr.should_switch_scene_pro({"beats_covered": 0, "retry_count": 1}, {}) is True


def test_force_pro_chapter():
    assert sr.should_switch_scene_pro({}, {"force_pro": True}) is True
    assert sr.should_switch_scene_pro({}, {"force_pro": False}) is False


def test_chapter_fuse_consecutive_three_below82():
    f = sr.should_force_whole_chapter_pro
    assert f(recent_best_scores=[81.5, 81.0, 80.5]) is True
    assert f(recent_best_scores=[80.0, 83.0, 80.5]) is False
    assert f(recent_final_gap_flags=[True, False, False, True, False]) is True


def test_normal_scene_no_pro():
    assert sr.should_switch_scene_pro(
        {"short_retry_count": 0, "reasoning_only_count": 0, "beats_covered": 3}, {}) is False


# ============ Q3 预算 ============
def test_pro_budget_soft_hard_rolling():
    b = sr.ProSceneBudget()
    # 每章2个 pro 场：4章=8（边界OK），第5章将达10 -> 软警告
    for ch in range(1, 5):
        b.record_chapter(ch, 2)
    assert b.plan_chapter(5, [True, True, False, False])["window_projected"] == 10
    plan = b.plan_chapter(5, [True, True, False, False])
    assert plan["budget_status"] == "SOFT_WARN_10"
    # 已用到12后再来 → 硬停
    b.record_chapter(5, 4)  # 模拟整章/多用，窗口累计12
    p2 = b.plan_chapter(5, [True, True, False, False])
    assert p2["window_projected"] > sr.PRO_SCENES_HARD and p2["over_budget"] is True


def test_whole_chapter_counts_four_and_capped():
    b = sr.ProSceneBudget()
    p = b.plan_chapter(1, [True, True, True, False])
    assert p["whole_chapter_pro"] is True and p["pro_scene_count"] == 4
    b.record_chapter(1, 4, whole_chapter_pro=True)
    b.record_chapter(2, 4, whole_chapter_pro=True)  # 用满每10章2个整章名额
    p3 = b.plan_chapter(3, [True, True, True, True])
    # 名额用尽 → 降级按场最多2，不再整章
    assert p3["whole_chapter_pro"] is False and p3["pro_scene_count"] == 2


def test_rolling_50_chapter_ratio():
    b = sr.ProSceneBudget()
    # 构造 50 章：前33章各1 pro 场，42..49 各1（共8，最近10章=20%不触发软警告），
    # 合计41/200=20.5%>20% → 滚动警告。
    for ch in range(1, 34):
        b.record_chapter(ch, 1)
    for ch in range(42, 50):
        b.record_chapter(ch, 1)
    assert b.rolling_ratio(50) > 0.20
    p = b.plan_chapter(50, [False] * 4)
    assert p["budget_status"] == "ROLLING_WARN_50"


# ============ Q2 退化窗口 ============
def test_single_waste_no_cooldown():
    w = dw.DegradeWindow()
    assert w.observe_parse(True) is None


def test_three_reasoning_in_20_triggers_p1():
    w = dw.DegradeWindow()
    seq = [True] * 6 + [False] * 14  # 空票率达阈值
    actions = [w.observe_call(x) for x in seq]
    fired = [a for a in actions if a]
    assert fired and fired[0]["level"] in ("P1", "P0") and fired[0]["conc"] == 2


def test_three_short_in_12_triggers_p1():
    w = dw.DegradeWindow()
    seq = [True] * 6 + [False] * 6  # 50%
    actions = [w.observe_scene(x) for x in seq]
    fired = [a for a in actions if a]
    assert fired and fired[0]["level"] == "P1"


def test_eight_waste_in_20_triggers_p0():
    w = dw.DegradeWindow()
    seq = [True] * 12 + [False] * 8  # 60%+
    actions = [w.observe_parse(x) for x in seq]
    p0 = [a for a in actions if a and a["level"] == "P0"]
    assert p0 and p0[0]["conc"] == 1 and p0[0]["cooldown_seconds"] == 180


def test_cooldown_max_two_per_hour_third_no_sleep():
    w = dw.DegradeWindow()

    def fire_p0(now):
        w.parse.clear()
        w._p0_fired = False
        w.level = 0
        w._elevated_obs = 0
        for x in [True] * 12:
            a = w.observe_parse(x, now=now)
            if a and a["level"] == "P0":
                return a
        return None

    first = fire_p0(0)
    second = fire_p0(200)
    third = fire_p0(400)  # 与前两次同处1小时窗口 → 第3次不再冷却
    assert first["cooled"] is True
    assert second["cooled"] is True
    assert third["level"] == "P0" and third["cooldown_seconds"] == 0 and third["cooled"] is False


def test_recovery_restores_concurrency():
    w = dw.DegradeWindow()
    for x in [True] * 6 + [False] * 14:
        w.observe_call(x, now=0)
    # 连续健康：清空坏窗口并给3次有效初稿
    w.calls.clear(); w.scenes.clear(); w.parse.clear()
    rec = None
    for _ in range(3):
        rec = w.observe_valid_draft(now=1000)
    assert rec is not None and rec["level"] == "RECOVER" and rec["conc"] == 4 and rec["flash"] is True


# ============ Q5 脚手架字段词清洗 ============
def test_single_id_with_json_context_removed():
    r = ss.scrub_scaffold_latin('他低声道"id":3，随后离开')
    assert "id" not in r["text"] and "他低声道" in r["text"] and "随后离开" in r["text"]
    assert r["status"] == "scrubbed"


def test_compound_beats_covered_removed():
    r = ss.scrub_scaffold_latin("正文内容 beats_covered 结尾")
    assert "beats_covered" not in r["text"] and "正文内容" in r["text"]


def test_whitelist_not_removed():
    s = "他拿着 iPhone 看 NBA 直播，用 WiFi"
    r = ss.scrub_scaffold_latin(s)
    assert r["status"] == "unchanged" and "iPhone" in r["text"] and "NBA" in r["text"] and "WiFi" in r["text"]


def test_two_field_words_drop_clause():
    r = ss.scrub_scaffold_latin("他走过来，id与covered出现，然后离开。")
    assert "id" not in r["text"] and "covered" not in r["text"]
    assert "他走过来" in r["text"] and "然后离开" in r["text"]
    assert "，，" not in r["text"]


def test_whole_paragraph_pollution_rollback():
    r = ss.scrub_scaffold_latin('"id":3 beats covered beats_covered score')
    assert r["status"] == "rollback_best"


def test_non_field_english_kept():
    # 普通英文（非字段词、非白名单）不被当作字段词删除
    r = ss.scrub_scaffold_latin("招牌上写着 Cafe 与 Bakery，中文为主")
    assert "Cafe" in r["text"] and "Bakery" in r["text"]
