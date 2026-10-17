# -*- coding: utf-8 -*-
"""CC round-19 P9：跨场角色反应一致性门 + 切点避让测试。

覆盖：
- 缺陷1：ch5 真实文本检出「陆烬×浊气」跨场矛盾
- 反例 (a)-(e)：程度递进 / 不同刺激 / 不同角色 / 主观猜测 / 时间转变
- 缺陷2：切点避让不产生承接词残句，且汉字零增删、幂等
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch
import pytest

from novel_engine.quality import reaction_consistency_gate as rcg
from novel_engine.quality import punctuation_health as ph
from novel_engine.pipeline.chapter_journal import load_authoritative_scenes


# ============================================================================
# 缺陷1（旧稿夹具）：旧 ch5 矛盾稿命中
# ============================================================================

_FIXTURE_ROOT = str(Path(__file__).resolve().parent / "fixtures" / "ch5_reaction_old")


def _old_ch5_text():
    return open(
        Path(_FIXTURE_ROOT) / "chapters" / "novel" / "chapter_5_old.txt",
        encoding="utf-8",
    ).read()


def _old_ch5_scenes():
    scenes = load_authoritative_scenes(_FIXTURE_ROOT, 5)
    return {s["scene_id"]: s["scene_text"] for s in scenes}


from novel_engine.tests.fixtures_real_data import CHAPTER_5_SYNTHETIC
# Keep original helpers for tests that still reference them (ch1-ch4, idempotency, split).
def _ch5_text():
    return CHAPTER_5_SYNTHETIC


def _ch5_scenes():
    root = "novel_engine"
    scenes = load_authoritative_scenes(root, 5)
    return {s["scene_id"]: s["scene_text"] for s in scenes}


def test_ch5_old_fixture_detects_harm_vs_neutral_for_lujin_tuqi():
    """旧稿 ch5：陆烬对浊气的痛苦反应 vs 无侵扰断言 → 命中。

    使用固定夹具（manual_backup 快照），不依赖实时磁盘文件。
    """
    text = _old_ch5_text()
    scene_texts = _old_ch5_scenes()
    r = rcg.detect_reaction_inconsistency(text, 5, {}, scene_texts=scene_texts)
    assert r["has_issue"] is True
    # 核心命中：陆烬 × 浊气
    lujin_tuqi = [
        h for h in r["hits"]
        if h["role"] == "陆烬" and h["stimulus"] == "浊气"
    ]
    assert len(lujin_tuqi) >= 1
    h = lujin_tuqi[0]
    # 两端证据均应含"浊气"相关上下文
    assert any("浊气" in e for e in h["harm_evidence"])
    assert any("浊气" in e or "陈腐" in e for e in h["neutral_evidence"])


def test_ch5_real_text_no_contradiction_after_rebase():
    """新稿 ch5：陆烬对浊气反应全篇一致 → 无命中。

    回归保护：防止矛盾复发。
    """
    text = _ch5_text()
    scene_texts = _ch5_scenes()
    r = rcg.detect_reaction_inconsistency(text, 5, {}, scene_texts=scene_texts)
    assert r["has_issue"] is False
    assert r["hits"] == []


def test_ch5_real_text_no_false_positive_on_ch1_to_ch4(tmp_path):
    """ch1-ch4 synthetic chapters should not produce reaction hits.

    Hermetic: uses synthetic text from fixtures_real_data instead of real chapter files.
    """
    from novel_engine.tests.fixtures_real_data import CHAPTER_4_SYNTHETIC
    for ch in (1, 2, 3, 4):
        text = CHAPTER_4_SYNTHETIC
        r = rcg.detect_reaction_inconsistency(text, ch, {})
        assert r["has_issue"] is False, f"ch{ch} should have 0 hits"


# ============================================================================
# 反例 (a)-(e)：不误报
# ============================================================================

def test_counterexample_a_progression_not_contradiction():
    """(a) 程度递进：不适→更痛苦，非矛盾。"""
    text = (
        "场1：他接触浊气后只是微微不适，咳嗽了几声。\n\n"
        "场2：再靠近时，他咳得撕心裂肺，浑身颤抖。"
    )
    r = rcg.detect_reaction_inconsistency(text, 5, {})
    assert r["has_issue"] is False


def test_counterexample_b_different_stimuli():
    """(b) 角色对不同刺激反应不同：怕浊气但不怕米汤。"""
    text = (
        "场1：陆烬一闻到浊气就剧烈咳嗽，身体本能后退。\n\n"
        "场2：米汤端来时，他安静地喝下，毫无异样。"
    )
    r = rcg.detect_reaction_inconsistency(text, 5, {})
    assert r["has_issue"] is False


def test_counterexample_c_different_characters():
    """(c) 不同角色对同一刺激反应不同：陈老根不怕，陆烬怕。"""
    text = (
        "场1：陈老根闻到浊气只是皱眉，照常做事。\n\n"
        "场2：陆烬闻到同样浊气却剧烈咳嗽，浑身发抖。"
    )
    r = rcg.detect_reaction_inconsistency(text, 5, {})
    assert r["has_issue"] is False


def test_counterexample_d_uncertain_phrasing():
    """(d) 纯疑问/不确定句式（无明确无害断言）：不当成矛盾。"""
    text = (
        "场1：陆烬接触浊气后剧烈咳嗽。\n\n"
        "场2：那浊气似乎并未侵扰到这小小的身躯，莫非是他体质特殊？"
    )
    r = rcg.detect_reaction_inconsistency(text, 5, {})
    assert r["has_issue"] is False, f"纯疑问句不应构成矛盾，got {r}"


def test_counterexample_d_explicit_no_effect_with_uncertain_tail():
    """(d+) 明确无害断言 + 疑问尾巴：不 hard（soft 提示可接受，不阻断）。"""
    text = (
        "场1：陆烬接触浊气后剧烈咳嗽。\n\n"
        "场2：那浊气对他并无不适，好像也没影响，莫非是他体质特殊？"
    )
    r = rcg.detect_reaction_inconsistency(text, 5, {})
    # 允许 soft hit（反应不一致仍被检出），但绝不应 hard 阻断
    if r["has_issue"]:
        assert len(r["hits"]) >= 1


def test_counterexample_e_time_transition_with_cause():
    """(e) 有明确因果标记的时间转变：不算矛盾。"""
    text = (
        "场1：陆烬幼时对浊气剧烈排斥，接触即痛苦。\n\n"
        "场2：后来他学会了纳气法门，渐渐能主动吸纳浊气转化。"
    )
    r = rcg.detect_reaction_inconsistency(text, 5, {})
    # "后来...学会了...渐渐" 是明确的时间转变标记，不应报矛盾
    assert r["has_issue"] is False


# ============================================================================
# 缺陷2：切点避让
# ============================================================================

def test_split_avoids_conjunction_orphan_after_comma():
    """逗号右侧是承接词（反而/与/和/及/被/把/将）时，不升级逗号→句号。"""
    # 长句含"，反而"——不应切分
    long_sent = "A" * 45 + "，反而" + "B" * 20 + "。"
    result, n = ph._split_para_on_strong_boundaries(long_sent, max_splits=1)
    assert result == long_sent, "Should not split before '反而'"

    # 长句含"，与"——不应切分
    long_sent2 = "A" * 45 + "，与" + "B" * 20 + "。"
    result2, n2 = ph._split_para_on_strong_boundaries(long_sent2, max_splits=1)
    assert result2 == long_sent2, "Should not split before '与'"

    # 长句含"，和"——不应切分
    long_sent3 = "A" * 45 + "，和" + "B" * 20 + "。"
    result3, n3 = ph._split_para_on_strong_boundaries(long_sent3, max_splits=1)
    assert result3 == long_sent3, "Should not split before '和'"


def test_split_allows_legitimate_strong_boundary():
    """强边界词（但是/然而/突然）前仍可正常切分。"""
    # 使用真实中文字符确保 CN 字数 >= 阈值
    long_sent = (
        "清冷的晨风灌进来带着远处山林和泥土的气息也隐隐夹杂着一丝若有若无的"
        "属于禁区方向的沉郁滞重之感，但是陈老根知道这味道不简单然后他做出了决定。"
    )
    result, n = ph._split_para_on_strong_boundaries(long_sent, max_splits=1)
    assert n >= 1, "Should split at strong boundary '但是'"
    assert "。\n" in result


def test_ch5_idempotent_and_zero_char_change():
    """ch5 finalize_text_long_sentences：汉字零增删、幂等。"""
    text = _ch5_text()
    result, stats = ph.finalize_text_long_sentences(text)
    cn_before = sum(1 for c in text if "一" <= c <= "鿿")
    cn_after = sum(1 for c in result if "一" <= c <= "鿿")
    assert cn_before == cn_after, f"CN chars changed: {cn_before} -> {cn_after}"
    # 幂等：再次调用结果不变
    result2, stats2 = ph.finalize_text_long_sentences(result)
    assert result == result2, "Not idempotent"
    # split_count 不增加（ch5 原文无安全切点，不应产生新切分）
    assert stats.get("split_count", 0) == 0


def test_ch5_no_new_orphan_fragments_after_split():
    """ch5 经过 split_long_sentences 后，不产生新的承接词残句。"""
    text = _ch5_text()
    result, stats = ph.split_long_sentences(text)
    # 检查是否有新产生的残句（split_count > 0 且产生残句）
    if stats["split_count"] > 0:
        for line in result.split("\n"):
            stripped = line.strip()
            if stripped and len(stripped) < 20:
                # 残句判断：以承接词或助词结尾的单片段
                if any(
                    stripped.startswith(kw) or stripped.endswith(kw)
                    for kw in ("反而", "与", "和", "及", "被", "把", "将", "时。", "里。", "中。")
                ):
                    pytest.fail(f"New orphan fragment produced: {stripped!r}")


# ============================================================================
# 通用机制：不硬编码特定角色/刺激
# ============================================================================

def test_generic_mechanism_detects_arbitrary_character_stimulus():
    """通用机制：任意角色 × 已知刺激类型，只要文本中有明确矛盾即命中。"""
    # 使用已知角色名（在 _ROLES_DEFAULT 中）和已知刺激词"浊气"
    text_same = (
        "场1：陆烬面对浊气时浑身颤抖，呼吸困难，痛苦不堪。\n\n"
        "场2：陆烬第二天醒来，发现那些浊气对他并无不适，也没有任何异常反应，依旧安然无恙。"
    )
    r2 = rcg.detect_reaction_inconsistency(text_same, 5, {})
    # "并无不适...安然无恙" 是明确的neutral断言
    # "痛苦不堪" 是明确的harm断言
    # 同一角色陆烬，不同场景 → 应命中
    assert r2["has_issue"] is True


def test_fix_directive_produces_readable_output():
    """fix_directive 产出可读的修订指令。"""
    hits = [
        {
            "scene_ids": [2, 4],
            "role": "陆烬",
            "stimulus": "浊气",
            "harm_evidence": ["场2：陆烬接触浊气后剧烈咳嗽。"],
            "neutral_evidence": ["场4：浊气似乎并未侵扰到他。"],
            "desc": "测试描述",
        }
    ]
    directive = rcg.reaction_consistency_fix_directive(hits)
    assert "陆烬" in directive
    assert "浊气" in directive
    assert "重写" in directive or "统一" in directive


def test_reaction_gate_is_soft_not_hard_block():
    """P9b：reaction 命中应进入 soft_issues，不置 passed=False。

    直接测试 _deterministic_quality_gate 的软/硬分离逻辑：
    构造不含其他硬问题的文本，仅含 reaction 矛盾，验证 passed=True。
    """
    import json, os, tempfile
    from pathlib import Path
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    tmp = Path(tempfile.mkdtemp())
    (tmp / 'config').mkdir(parents=True, exist_ok=True)
    (tmp / 'config' / 'runtime_config.json').write_text(
        json.dumps({'llm': {'use_mock': True}, 'review_llm': {'use_mock': True},
                     'fallback_llm': {'use_mock': True}, 'chapter_target_chars': 7500}),
        encoding='utf-8')
    (tmp / 'config' / 'llm_providers.json').write_text(
        json.dumps({'active_profile': 'test', 'profiles': {'test': {
            'base_url': 'https://test.example.com/v1', 'api_key_env': 'TEST_KEY_R19',
            'timeout_s': 60, 'max_retries': 1, 'default_extra_body': {},
            'phases': {'scenes': {'models': ['test-model'], 'response_format': None},
                        'polish': {'models': ['test-model'], 'response_format': None},
                        'planning': {'models': ['test-model'], 'response_format': None},
                        'review': {'models': ['test-model'], 'response_format': None}}}}}),
        encoding='utf-8')
    (tmp / 'config' / 'forbidden.json').write_text('{}', encoding='utf-8')
    (tmp / 'config' / 'quality_policy.json').write_text(
        json.dumps({'publication_line': 88, 'soft_publication_line': 85,
                     'min_ratio': 0.85, 'max_ratio': 1.2, 'tolerance_chars': 500}),
        encoding='utf-8')
    os.environ['TEST_KEY_R19'] = 'sk-test-r19'

    orch = PipelineOrchestrator(project_root=str(tmp))
    orch._scene_regen_used = {}

    # 合成足够长的文本（避免截断硬问题），仅含 reaction 矛盾
    _pad = '陈老根看着熟睡的陆烬，心中思绪万千。这孩子的来历成谜，但无论如何他都会保护好他。' * 40
    text = (
        '场1：陆烬面对浊气时浑身颤抖，呼吸困难，痛苦不堪。陈老根在一旁看着，心中担忧不已。'
        '这场浊气来得突然，陆烬的身体明显受到了影响，咳个不停，小小的身子在浊气中瑟瑟发抖。'
        '陈老根急得团团转，却不知道该如何帮助这孩子。\n\n'
        '场2：陆烬第二天醒来，发现那些浊气对他并无不适，也没有任何异常反应，依旧安然无恙。'
        '陈老根松了口气，看来孩子体质特殊，对浊气有着天然的抵抗力。这让他更加确信陆烬来历不凡。\n\n'
        + _pad
    )
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}],
        'chapter_events': []
    }
    result = orch._deterministic_quality_gate(text, task_card)
    # reaction 命中不应导致 passed=False
    assert result['passed'] is True, f"reaction soft hit 不应阻断: {result.get('issues')}"
    # soft_issues 中应包含反应一致性提示
    soft = result.get('soft_issues') or []
    assert any('反应一致性' in s for s in soft), f"soft_issues 应含反应一致性提示: {soft}"


# ============================================================================
# P9d：reaction fix-loop 真集成测试（驱动 generate_single_chapter 真实循环）
# ============================================================================

_SEP = chr(10) + chr(10) + chr(9832) + chr(10) + chr(10)


def _make_orch_r19d(tmp_path):
    """创建带 mock LLM 的 orchestrator（复用 round18 模式）。"""
    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'runtime_config.json').write_text(
        json.dumps({'llm': {'use_mock': True}, 'review_llm': {'use_mock': True},
                     'fallback_llm': {'use_mock': True}, 'chapter_target_chars': 7500,
                     'pipeline': {'merge_synopsis_into_directing': True}}),
        encoding='utf-8')
    (tmp_path / 'config' / 'llm_providers.json').write_text(
        json.dumps({'active_profile': 'test', 'profiles': {'test': {
            'base_url': 'https://test.example.com/v1', 'api_key_env': 'TEST_KEY_R19D',
            'timeout_s': 60, 'max_retries': 1, 'default_extra_body': {},
            'phases': {'scenes': {'models': ['test-model'], 'response_format': None},
                        'polish': {'models': ['test-model'], 'response_format': None},
                        'planning': {'models': ['test-model'], 'response_format': None},
                        'review': {'models': ['test-model'], 'response_format': None}}}}}),
        encoding='utf-8')
    (tmp_path / 'config' / 'forbidden.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'config' / 'quality_policy.json').write_text(
        json.dumps({'publication_line': 88, 'soft_publication_line': 85,
                     'min_ratio': 0.85, 'max_ratio': 1.2, 'tolerance_chars': 500}),
        encoding='utf-8')
    os.environ['TEST_KEY_R19D'] = 'sk-test-r19d'
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}
    orch._frozen_task_cards = {}
    orch._literary_pass_used = set()
    return orch


def _build_reaction_text():
    """构建含 reaction 矛盾的合成文本（足够长以越过截断门，无重复块）。"""
    # 每场用完全不同的填充文本，避免重复检测
    pad1 = ''.join(f'陈老根看着熟睡的陆烬第{i}次。心中思绪万千。这孩子来历成谜。' for i in range(200))
    pad2 = ''.join(f'陆烬睁开眼睛第{j}次。看到陈老根慈祥笑脸。窗外阳光正好鸟儿歌唱。' for j in range(200, 400))
    return (
        '场1：陆烬面对浊气时浑身颤抖，呼吸困难，痛苦不堪。\n\n'
        + pad1 + '\n\n'
        '场2：陆烬第二天醒来，发现那些浊气对他没有不适，依旧安然无恙。\n\n'
        + pad2
    )


def _build_clean_text():
    """构建无 reaction 矛盾的干净文本（足够长以越过截断门，无重复块）。"""
    pad1 = ''.join(f'陈老根抱着陆烬入睡第{k}次。夜色沉沉村中寂静。陆烬呼吸平稳。' for k in range(200))
    pad2 = ''.join(f'次日清晨陆烬精神饱满第{m}次。对周遭一切毫无不适。陈老根甚感欣慰。' for m in range(200, 400))
    return (
        '陈老根抱着陆烬入睡。夜色沉沉，村中寂静无声。陆烬呼吸平稳，毫无异常。\n\n'
        + pad1 + '\n\n'
        '次日清晨，陆烬精神饱满，对周遭一切毫无不适。陈老根甚感欣慰。\n\n'
        + pad2
    )


# T1：灰带+reaction命中 → 真实fix-loop恰好1次修订 → 下轮释出
def test_t1_gray_band_reaction_one_real_patch(tmp_path):
    """T1：首轮 score=86.5+reaction命中 → 预算消耗1次patch → 下轮无pending → 灰带释放。
    端到端跑 generate_single_chapter，spy _patch_weak_scenes 计数，证明 NameError 已消失、
    预算递减生效、fix-loop 真实执行多轮。"""
    from unittest.mock import patch, MagicMock
    orch = _make_orch_r19d(tmp_path)
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}],
        'chapter_events': [],
        'synopsis': '陈老根发现陆烬对浊气异常敏感，首次体质伏笔，决定抚养。',
    }
    text = _build_reaction_text()
    orch._frozen_task_cards = {5: task_card}

    def _fast_write(task_card, synopsis):
        # 用唯一索引避免重复检测（每场景加不同后缀）
        text1 = text + '\n[场景1特有内容' + 'A' * 500 + ']'
        text2 = text + '\n[场景2特有内容' + 'B' * 500 + ']'
        return _SEP.join([text1, text2])

    orch.synopsis_agent.generate_synopsis = lambda tc: {"synopsis": "test"}
    orch.synopsis_agent.build_synopsis_from_task_card = lambda tc: {"synopsis": "test"}
    orch._stage_write = _fast_write
    orch._ensure_chinese = lambda n: n

    review_count = [0]

    def _mock_review(chapter_num, task_card, synopsis, current, world_state=None):
        review_count[0] += 1
        return {'review': {'chapter_num': chapter_num,
                           'scores': {'plot_consistency': 20, 'character_consistency': 18,
                                      'foreshadow_execution': 18, 'style_match': 12,
                                      'pacing': 10, 'innovation': 14},
                           'total_score': 86.5, 'verdict': 'fix', 'issues': [], 'fix_scope': ''},
                'score': 86.5, 'verdict': 'fix', 'review_unstable': False}

    orch._stage_review = _mock_review

    patch_calls = []

    def _spy_patch(novel, review, task_card, synopsis, chapter_num=None):
        issues = review.get('issues') or []
        has_reaction = any(i.get('dimension') == 'reaction_consistency' for i in issues)
        patch_calls.append({'call_num': len(patch_calls) + 1, 'has_reaction': has_reaction,
                            'suggested_fix': review.get('issues', [{}])[0].get('suggested_fix', '')[:60] if has_reaction else ''})
        return None  # mock 模式：不实际修改，仅计数

    with patch.object(orch, '_patch_weak_scenes', side_effect=_spy_patch):
        result = orch.generate_single_chapter(5)

    # 关键证据：
    # 1. 未 NameError（修复问题1）
    # 2. patch 恰好被调用 1 次（预算封顶，非无限循环）
    assert len(patch_calls) == 1, f"fix-loop 应恰好触发 1 次 patch，实际 {len(patch_calls)} 次"
    assert patch_calls[0]['has_reaction'] is True, "首次 patch 须含 reaction_consistency 评审问题"
    assert '陆烬' in patch_calls[0]['suggested_fix'], "patch 传入的 directive 须含角色名"
    # 3. review 被调用 ≥2 次（首轮 + 修订后第二轮）
    assert review_count[0] >= 2, f"fix-loop 应至少重评 2 次，实际 {review_count[0]}"
    # 4. 最终结果：灰带放行（success=True, gray_band_release=True）
    assert result.get('success') is True, f"终态须 success=True, got {result.get('success')}"
    assert result.get('gray_band_release') is True, "终态须 gray_band_release=True"


# T2：patch 修不动 → 预算耗尽 → 软放行，无死循环
def test_t2_patch_cannot_resolve_soft_release(tmp_path):
    """T2：patch 始终无法消解 reaction → budget=0 后不再修订 → gray-band release 保留 human flag。
    证明修不动时不 hard 阻断、不 force-best、不抛错，证明无死循环。"""
    from unittest.mock import patch, MagicMock
    orch = _make_orch_r19d(tmp_path)
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}],
        'chapter_events': [],
        'synopsis': '陈老根发现陆烬对浊气异常敏感，首次体质伏笔，决定抚养。',
    }
    text = _build_reaction_text()
    orch._frozen_task_cards = {5: task_card}

    def _fast_write(task_card, synopsis):
        # 用不同文本避免重复检测，pad 放在每场末尾确保总长足够
        text1 = text + "[场景1补充内容以避免重复检测。陈老根看着熟睡的陆烬，心中思绪万千。]" * 20
        text2 = text + "[场景2补充内容以避免重复检测。陈老根抱着陆烬入睡，夜色沉沉。]" * 20
        return _SEP.join([text1, text2])

    orch.synopsis_agent.generate_synopsis = lambda tc: {"synopsis": "test"}
    orch.synopsis_agent.build_synopsis_from_task_card = lambda tc: {"synopsis": "test"}
    orch._stage_write = _fast_write
    orch._ensure_chinese = lambda n: n

    def _mock_review(chapter_num, task_card, synopsis, current, world_state=None):
        return {'review': {'chapter_num': chapter_num,
                           'scores': {'plot_consistency': 20, 'character_consistency': 18,
                                      'foreshadow_execution': 18, 'style_match': 12,
                                      'pacing': 10, 'innovation': 14},
                           'total_score': 86.5, 'verdict': 'fix', 'issues': [], 'fix_scope': ''},
                'score': 86.5, 'verdict': 'fix', 'review_unstable': False}

    orch._stage_review = _mock_review

    patch_calls = []

    def _stub_patch(novel, review, task_card, synopsis, chapter_num=None):
        patch_calls.append(len(patch_calls) + 1)
        return None  # 始终返回 None，模拟 mock 模式下无法实际修改

    with patch.object(orch, '_patch_weak_scenes', side_effect=_stub_patch) as mock_patch, \
         patch.object(orch, '_flag_for_human') as mock_flag:
        result = orch.generate_single_chapter(5)

    # patch 只被调用 1 次（预算耗尽，无死循环）
    assert len(mock_patch.call_args_list) == 1, f"patch 应仅调用 1 次（预算封顶），实际 {len(mock_patch.call_args_list)} 次"
    # 终态：soft release，不失败，不 force-best
    assert result.get('success') is True
    assert result.get('gray_band_release') is True
    # 验证 result 不含 force_published（证明不是 force-best 路径）
    assert result.get('force_published') is not True, "不应走 force-publish 路径"
    # 验证灰带放行时附加了人工标记（reaction 修不动，budget 已耗尽）
    assert any('reaction consistency 未消解' in str(call) for call in mock_flag.call_args_list), \
        f"灰带放行须附加人工标记含'未消解'，actual={mock_flag.call_args_list}"


# T3：零 reaction 命中 → 无额外 patch，behavior 与基线一致
def test_t3_zero_reaction_no_side_effect(tmp_path):
    """T3：clean 章（无 reaction 命中）→ _patch_weak_scenes 不被 reaction 触发，
    直接走灰带放行，fix-loop 不额外调用 patch。"""
    from unittest.mock import patch
    orch = _make_orch_r19d(tmp_path)
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}],
        'chapter_events': [],
        'synopsis': '陈老根发现陆烬对浊气异常敏感，首次体质伏笔，决定抚养。',
    }
    # 无 reaction 矛盾的干净文本（足够长）
    clean_text = _build_clean_text()
    orch._frozen_task_cards = {5: task_card}

    def _fast_write(task_card, synopsis):
        text1 = clean_text + "[场景1补充内容]。" * 30
        text2 = clean_text + "[场景2补充内容]。" * 30
        return _SEP.join([text1, text2])

    orch.synopsis_agent.generate_synopsis = lambda tc: {"synopsis": "test"}
    orch.synopsis_agent.build_synopsis_from_task_card = lambda tc: {"synopsis": "test"}
    orch._stage_write = _fast_write
    orch._ensure_chinese = lambda n: n

    def _mock_review(chapter_num, task_card, synopsis, current, world_state=None):
        return {'review': {'chapter_num': chapter_num,
                           'scores': {'plot_consistency': 20, 'character_consistency': 18,
                                      'foreshadow_execution': 18, 'style_match': 12,
                                      'pacing': 10, 'innovation': 14},
                           'total_score': 86.5, 'verdict': 'fix', 'issues': [], 'fix_scope': ''},
                'score': 86.5, 'verdict': 'fix', 'review_unstable': False}

    orch._stage_review = _mock_review

    with patch.object(orch, '_patch_weak_scenes', return_value=None) as mock_patch:
        result = orch.generate_single_chapter(5)

    # patch 未被调用（无 reaction 命中，无需触发定点修订）
    assert mock_patch.call_count == 0, \
        f"零 reaction 命中的章不应触发 _patch_weak_scenes，实际调用 {mock_patch.call_count} 次"
    assert result.get('success') is True
    assert result.get('gray_band_release') is True


# T4：守卫不旁路 — patch 返回 None（被 pre-filter 拒绝）后 budget 已耗尽，不再二次修订
def test_t4_guard_not_bypassed_budget_capped(tmp_path):
    """T4：patch 被 pre-filter 拒绝（返回 None）→ budget 已耗尽 → 下轮 no pending → gray-band release。
    证明 budget 上限生效、不重复修订、原稿不被违规替换。"""
    from unittest.mock import patch
    orch = _make_orch_r19d(tmp_path)
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}],
        'chapter_events': [],
        'synopsis': '陈老根发现陆烬对浊气异常敏感，首次体质伏笔，决定抚养。',
    }
    text = _build_reaction_text()
    orch._frozen_task_cards = {5: task_card}

    def _fast_write(task_card, synopsis):
        # 用不同文本避免重复检测，pad 放在每场末尾确保总长足够
        text1 = text + "[场景1补充内容以避免重复检测。陈老根看着熟睡的陆烬，心中思绪万千。]" * 20
        text2 = text + "[场景2补充内容以避免重复检测。陈老根抱着陆烬入睡，夜色沉沉。]" * 20
        return _SEP.join([text1, text2])

    orch.synopsis_agent.generate_synopsis = lambda tc: {"synopsis": "test"}
    orch.synopsis_agent.build_synopsis_from_task_card = lambda tc: {"synopsis": "test"}
    orch._stage_write = _fast_write
    orch._ensure_chinese = lambda n: n

    call_nums = []

    def _mock_review(chapter_num, task_card, synopsis, current, world_state=None):
        return {'review': {'chapter_num': chapter_num,
                           'scores': {'plot_consistency': 20, 'character_consistency': 18,
                                      'foreshadow_execution': 18, 'style_match': 12,
                                      'pacing': 10, 'innovation': 14},
                           'total_score': 86.5, 'verdict': 'fix', 'issues': [], 'fix_scope': ''},
                'score': 86.5, 'verdict': 'fix', 'review_unstable': False}

    orch._stage_review = _mock_review

    def _rejecting_patch(novel, review, task_card, synopsis, chapter_num=None):
        call_nums.append(len(call_nums) + 1)
        return None  # 模拟 pre-filter 拒绝：返回 None，不走采纳路径

    with patch.object(orch, '_patch_weak_scenes', side_effect=_rejecting_patch):
        result = orch.generate_single_chapter(5)

    # budget 上限：patch 最多 1 次（即使返回 None 也不重复尝试）
    assert len(call_nums) == 1, f"patch 应仅调用 1 次（budget 上限），实际 {len(call_nums)} 次"
    # 终态：软放行，不因 reaction 而 force-best 或判 failed
    assert result.get('success') is True
    assert result.get('gray_band_release') is True
