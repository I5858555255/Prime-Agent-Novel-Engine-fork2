# -*- coding: utf-8 -*-
"""CC28 3a：零 LLM 确定性断句修复（PunctuationSplitRepair）单测。

覆盖裁决要求：
- 80 汉字无标点 → 检测报红；
- “向下沉，向下坠，向下陷”短排比不报；
- 对话省略号“……”不报；“谁？”“跑！”不误伤；
- 修复后超长 run 被切开、且 strip 全部标点后汉字逐字序列不变（零改字）；
- r40 ch0 scene4 真实数百字流水段可被零 LLM 修复到过门；
- 引号内对话不被插入标点。
"""
from novel_engine.quality import punctuation_health as ph


def test_detect_plain_80_chars_run_on():
    text = "的" * 80  # 纯连续 80 字、零标点零空白
    r = ph.check_paragraph_punctuation(text)
    assert r["is_unhealthy"] is True
    assert r["max_unpunctuated_run"] >= 80


def test_short_parallelism_not_flagged():
    text = "他只觉得自己不断地向下沉，向下坠，向下陷。"
    r = ph.check_paragraph_punctuation(text)
    assert r["is_unhealthy"] is False


def test_dialogue_ellipsis_not_flagged():
    assert ph.check_paragraph_punctuation("“这么小……”那人低声说。")["is_unhealthy"] is False
    assert ph.check_paragraph_punctuation("“谁？”“跑！”")["is_unhealthy"] is False


def test_repair_preserves_every_character():
    text = "的" * 90  # 零边界词，强制走硬兜底拆段
    fixed, k = ph.repair_paragraph_long_runs(text)
    assert k == 0  # 全部是结构拆段（句号换行），不计逗号名额
    # 关键：去掉所有标点/空白后必须逐字相同
    assert ph.strip_all_punctuation(fixed) == text
    # 每段 run 被换行/句号打断，不再有超硬上限的 run
    assert ph.check_text_punctuation(fixed) == []


def test_repair_real_r40_scene4_runon_resolves_and_preserves_text():
    # 取自 r40 ch0 scene4 的真实无标点流水段（数百字）
    text = (
        "浓雾如墨汁泼洒在地面上缓缓流动缠绕着老松树虬结盘错的根须每一缕都透着湿冷的腥气"
        "像是一张张无形的嘴在黑暗中咀嚼吞噬一切光线"
        "老松树根部有一处凹陷凹陷里蜷缩着一个襁褓襁褓已被血污浸透边缘粘连在一起看不出原本的颜色"
        "襁褓中是一个刚出生的婴孩皮肤皱红四肢细小柔软得仿佛随时会断裂"
        "此刻婴孩正拼命扭动身躯喉咙里挤出尖锐刺耳的啼哭声声音穿透浓重的雾霭在山林间回荡"
        "惊起几只夜宿林鸟扑棱棱飞走"
    )
    assert ph.check_paragraph_punctuation(text)["is_unhealthy"] is True
    fixed, stats = ph.repair_text_punctuation(text)
    # 零改字：只增标点/换行
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)
    assert stats["changed_paragraphs"] >= 1
    assert stats["residual_unhealthy"] == 0
    # 修复后整章逐段复检全部健康
    assert ph.check_text_punctuation(fixed) == []


def test_repair_does_not_touch_dialogue_inside_quotes():
    dialogue = "“这一整句对话里面完全不加任何逗号句号停顿也要保持原样不改哦”"
    para = "他站在门口沉默了很久终于低声说了一句话" + dialogue + "然后转身离开这个地方走回屋里不再回头看去"
    fixed, _k = ph.repair_paragraph_long_runs(para)
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(para)
    # 引号内对话文本必须原样保留（不允许在其中插入标点）
    assert dialogue in fixed


def test_repair_healthy_paragraph_unchanged():
    text = "监护仪发出一声长鸣。他想抬手，手臂却沉得像灌了铅。"
    fixed, k = ph.repair_paragraph_long_runs(text)
    assert fixed == text and k == 0


def test_compliance_helper_rejects_any_word_change():
    orig = "他向山下走去很快看见了灯火" * 3
    fixed, _ = ph.repair_paragraph_long_runs(orig)
    assert ph.is_punctuation_only_fix(orig, fixed) is True
    # 若有任何字被改动，合规校验必须失败
    tampered = fixed.replace("灯火", "火焰", 1)
    assert ph.is_punctuation_only_fix(orig, tampered) is False
