"""回归测试：清洗层必须拦截 round7/round8 的 4 类泄漏样本。"""
from novel_engine.quality.repetition_detector import (
    purify_novel_for_publish, verify_no_scaffolding, detect_director_speak_leak,
)
from novel_engine.quality.scope_gate import detect_scope_violations


def _test_root():
    import pathlib
    return str(pathlib.Path(__file__).resolve().parents[1])

def test_ch2_beat_point():
    raw = "**节拍点1（冲突酝酿）：村民聚集，议论声起**\n\n正文段落。"
    purified = purify_novel_for_publish(raw)
    assert "节拍点" not in purified, f"节拍点未清洗: {purified}"
    assert verify_no_scaffolding(purified) == []


def test_ch3_scene_summary():
    raw = "****\n\n【场景小结】\n场景剧情概述：...\n作者意图：...\n伏笔说明：...\n\n正文。"
    purified = purify_novel_for_publish(raw)
    assert "场景小结" not in purified
    assert "****" not in purified
    assert verify_no_scaffolding(purified) == []


def test_ch4_word_count():
    raw = "【当前字数：约 1850 字】\n\n【本节拍点完成】- 场景冲突/细节呈现/伏笔铺垫\n\n正文。"
    purified = purify_novel_for_publish(raw)
    assert "当前字数" not in purified
    assert "本节拍" not in purified
    assert verify_no_scaffolding(purified) == []


def test_ch7_scene_header():
    raw = "【场景 1：陈老根家，简陋的土屋内】\n\n正文段落。"
    purified = purify_novel_for_publish(raw)
    assert "【场景" not in purified
    assert "陈老根家" not in purified or "正文段落" in purified
    assert verify_no_scaffolding(purified) == []


def test_verify_catches_leak():
    raw = "**节拍点1（冲突酝酿）：村民聚集，议论声起**"
    assert verify_no_scaffolding(raw) != []
    raw2 = "【场景小结】\nxxx"
    assert verify_no_scaffolding(raw2) != []


def test_director_speak_real_case_ch25():
    """ch25 真实泄漏实例必须命中。"""
    text = "这就是他必须执行的第一个外部动作：翻阅旧笔记。"
    issues = detect_director_speak_leak(text)
    assert issues, f"ch25 实例未命中: {issues}"
    assert any("导演话语" in i or "规划" in i for i in issues)
    # verify_no_scaffolding 也应整体命中（进 [泄漏] 分支）
    assert verify_no_scaffolding(text) != []


def test_director_speak_variants():
    """不同措辞的规划式表述应被结构化检测覆盖（非逐词打地鼠）。"""
    variants = [
        "接下来要做的是把药草晾干。",
        "按照安排他需要去村口等车。",
        "这一步的任务是查看那本册子。",
        "本章核心目标是让陆烬发现异常。",
        "他需要在黎明前执行完这些安排。",
    ]
    for v in variants:
        assert detect_director_speak_leak(v), f"变体未命中: {v}"


def test_director_speak_no_false_positive():
    """正常叙事表述不得误报。"""
    clean = [
        "他走到院子中央，弯腰拾起柴刀。",
        "陆烬蹲在沙地旁，用枯枝写下几个字。",
        "陈老根眯着眼，把烟锅在鞋底上磕了磕。",
        "那本册子就藏在墙缝里，他摸出来翻看。",
    ]
    for c in clean:
        assert detect_director_speak_leak(c) == [], f"误报: {c}"


def test_scope_gate_sentence_ctx_keeps_term():
    """CC 2026-10-05 ch56：task_card extra_hard_terms 注入"吐纳"后，
    长段落中 term 位于 120 字之后时 hard issue 的 sentence 必须仍含 term。
    同时验证：普通物件传递（"传给旁边半大孩子"）不触发 P6-1 传授误报。"""
    para = ("老妇哆哆嗦嗦接过碗，只抿了一小口，便传给旁边半大孩子。" * 5
            + "陈老根在山崖边独自吐纳，气机沉入丹田。")
    r = detect_scope_violations(para, 56, None, _test_root(),
                                extra_hard_terms=["吐纳"])
    assert r["hard"], "extra_hard_terms 注入吐纳 + 正文含吐纳应 hard 命中"
    for h in r["hard"]:
        assert h["term"] in h["sentence"], (
            f"引用句必须含 term: term={h['term']!r} sentence={h['sentence']!r}")
    # 无"吐纳"正文的普通传递不应触发传授误报（P6-1 收紧）
    para2 = "老妇将碗传给旁边的孩子，孩子捧着喝了。" * 3
    r2 = detect_scope_violations(para2, 56, None, _test_root(),
                                 extra_hard_terms=["吐纳"])
    assert r2["hard"] == [], f"普通物件传递误报: {r2['hard']}"

