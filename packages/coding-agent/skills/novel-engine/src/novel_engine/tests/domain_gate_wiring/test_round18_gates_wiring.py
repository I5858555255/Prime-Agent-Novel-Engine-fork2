# -*- coding: utf-8 -*-
"""CC round-18 phase3：D1-D7 全量整改测试。"""
from __future__ import annotations
import json, os
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest
_SEP = chr(10)+chr(10)+chr(9832)+chr(10)+chr(10)

from novel_engine.tests.fixtures_real_data import (
    CHAPTER_4_SYNTHETIC,
    CHAPTER_5_SYNTHETIC,
)
# CC round-19 P9：ch5 危机门固定快照（来源于 draft/chapter_5_partial.jsonl records[4:8]）
# 替代硬编码的运行时路径 novel_engine/chapters/draft/failed/chapter_5/
_FIXTURES_ROOT = Path(__file__).resolve().parent / 'fixtures' / 'ch5_crisis_gate'


def _make_orch(tmp_path, use_mock=True):
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'runtime_config.json').write_text(
        json.dumps({'llm': {'use_mock': use_mock}, 'review_llm': {'use_mock': use_mock},
                    'fallback_llm': {'use_mock': use_mock}, 'chapter_target_chars': 7500}),
        encoding='utf-8')
    (tmp_path / 'config' / 'llm_providers.json').write_text(
        json.dumps({'active_profile': 'test', 'profiles': {'test': {
            'base_url': 'https://test.example.com/v1', 'api_key_env': 'TEST_KEY_R18',
            'timeout_s': 60, 'max_retries': 1, 'default_extra_body': {},
            'phases': {'scenes': {'models': ['test-model'], 'response_format': None},
                        'polish': {'models': ['test-model'], 'response_format': None, 'concurrency': 4},
                        'planning': {'models': ['test-model'], 'response_format': None},
                        'review': {'models': ['test-model'], 'response_format': None}}}}}),
        encoding='utf-8')
    (tmp_path / 'config' / 'forbidden.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'config' / 'quality_policy.json').write_text(
        json.dumps({'publication_line': 88, 'soft_publication_line': 85,
                     'min_ratio': 0.85, 'max_ratio': 1.2, 'tolerance_chars': 500}),
        encoding='utf-8')
    os.environ['TEST_KEY_R18'] = 'sk-test-r18'
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}
    return orch


def _write_leak_terms_infant(tmp_path, hard_block=None, chapters=(1, 9)):
    d = tmp_path / 'config' / 'leak_terms'
    d.mkdir(parents=True, exist_ok=True)
    cfg = {'arc': 'infant', 'chapters': list(chapters), 'negation_window': 20,
           'negation_markers': ['不是','并非','倒非','绝不','绝非','不叫','不信','不懂','不知',
                                '不涉','谈不上','并无','毫无','所谓','之说','传言','讹传',
                                '如果','假如','传说中','据说','听闻','书中记载','莫','勿',
                                '休','岂','哪有','未曾','尚未','不许','禁止','没有'],
           'idiom_whitelist': ['魂飞魄散','失魂落魄','魂不附体','魂不守舍','魂牵梦绕',
                               '心惊胆战','吓得魂'],
           'hard_block': hard_block or [], 'soft_warn': ['修炼','境界','门派','灵气'],
           'night_anchor_markers': ['子时','丑时','寅时','当夜','当晚','是夜','数时辰',
                                    '两三个时辰','几个时辰','夜间','夜里','深夜','半夜'],
           'dawn_markers': ['天亮','天明','黎明','拂晓','破晓'],
           'future_markers': ['等','待到','直到','再说','才行','还早','未到','还没',
                              '尚未','还未','盼','迟早','总要','终究会','等到']}
    (d / 'infant.json').write_text(json.dumps(cfg, ensure_ascii=False), encoding='utf-8')
    return cfg


def _make_ch5_card():
    return {'chapter_num': 5, 'core_goal': '陈老根发现陆烬对浊气异常敏感，首次体质伏笔',
            'conflicts': {'internal': '隐忧 vs 保护', 'external': '村民疏远'},
            'emotion_curve': {'start': '疲惫', 'middle': '察觉', 'climax': '决定', 'end': '钩子'},
            'scene_blueprints': [
                {'scene_num': 1, 'location': '茅屋', 'characters': ['陈老根', '陆烬'],
                 'goal': '安抚夜啼', 'conflict': '物资匮乏', 'emotion': '疲惫', 'word_count_target': 1800},
                {'scene_num': 2, 'location': '村口井边', 'characters': ['陈老根', '陆烬', '赵老四'],
                 'goal': '打水遇村民', 'conflict': '刻意疏远', 'emotion': '警觉', 'word_count_target': 2000},
                {'scene_num': 3, 'location': '院中', 'characters': ['陈老根', '陆烬'],
                 'goal': '观察陆烬', 'conflict': '异常沉静', 'emotion': '疑惑', 'word_count_target': 1800},
                {'scene_num': 4, 'location': '屋内', 'characters': ['陈老根', '陆烬'],
                 'goal': '决定抚养', 'conflict': '面对未来', 'emotion': '决心', 'word_count_target': 1600}],
            'foreshadow_actions': [{'foreshadow_id': 'F001', 'action': '陆烬对井边浊气本能侧头避开', 'intensity': '隐晦提示'}],
            'chapter_events': [
                {'one_line_summary': '陈老根独自在家给陆烬换洗喂食'},
                {'one_line_summary': '陈老根去村口古井打水，偶遇赵老四等村民'},
                {'one_line_summary': '陈老根在院中观察陆烬，发现其目光沉静'},
                {'one_line_summary': '陈老根在屋内看着熟睡的陆烬，决定默默抚养'}]}


def _long_text(n=60):
    return '这是补充文字以确保长度达标避免截断门干扰漏检断言的检测文本片段。' * n


def test_d1_ch6_night_tuna_exempt(tmp_path):
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['军中','气感','内视','吐纳','教头','医官'])
    orch = _make_orch(tmp_path)
    good = '是夜子时，陈老根独自盘膝坐在炕边吐纳呼吸，气息绵长。陆烬在床边悄悄睁眼看着，不敢出声。' + _long_text()
    r = detect_scope_violations(good, 6, timeline_anchor=None, root=orch.root)
    hard_terms = [h['term'] for h in r['hard']]
    assert '吐纳' not in hard_terms, f'ch6 night tuna exempt failed: {hard_terms}'
    assert '内视' not in hard_terms


def test_d1_ch6_neishi_still_blocked(tmp_path):
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['军中','气感','内视','吐纳','教头','医官'])
    orch = _make_orch(tmp_path)
    bad = '陈老根夜间吐纳完毕，又运起内视，看见陆烬体内的浊气。' + _long_text()
    r = detect_scope_violations(bad, 6, timeline_anchor=None, root=orch.root)
    hard_terms = [h['term'] for h in r['hard']]
    assert '内视' in hard_terms, f'内视 must be blocked: {hard_terms}'


def test_d1_ch5_tuna_blocked(tmp_path):
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳','气感','内视'])
    orch = _make_orch(tmp_path)
    bad = '陈老根夜里吐纳呼吸。' + _long_text()
    r = detect_scope_violations(bad, 5, timeline_anchor=None, root=orch.root)
    assert '吐纳' in [h['term'] for h in r['hard']]


def test_d1_ch6_day_tuna_blocked(tmp_path):
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    bad = '白天陈老根在院中吐纳呼吸。' + _long_text()
    r = detect_scope_violations(bad, 6, timeline_anchor=None, root=orch.root)
    assert '吐纳' in [h['term'] for h in r['hard']]


def test_d1_ch6_teach_tuna_blocked(tmp_path):
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    bad = '陈老根教陆烬吐纳呼吸。' + _long_text()
    r = detect_scope_violations(bad, 6, timeline_anchor=None, root=orch.root)
    assert '吐纳' in [h['term'] for h in r['hard']]




def test_p4_2a_standard_same_sentence_exempt(tmp_path):
    """P4-2 原始句式①：标准同名同句 → 豁免（not hard）。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=["吐纳"])
    orch = _make_orch(tmp_path)
    text = "是夜子时，陈老根独自盘膝坐在炕边吐纳呼吸，气息绵长。陆烬在旁偷看。" + _long_text()
    r = detect_scope_violations(text, 6, timeline_anchor=None, root=orch.root)
    assert "吐纳" not in [h["term"] for h in r["hard"]], f"P4-2a should be exempt, got hard={[h['term'] for h in r['hard']]!r}"


def test_p4_2b_cross_sentence_pronoun_exempt(tmp_path):
    """P4-2 原始句式②：跨句他字指代陈老根 → 豁免（not hard）。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=["吐纳"])
    orch = _make_orch(tmp_path)
    text = "是夜子时，陈老根独自盘膝坐在炕边。他缓缓吐纳，呼吸绵长。陆烬在旁偷看。" + _long_text()
    r = detect_scope_violations(text, 6, timeline_anchor=None, root=orch.root)
    assert "吐纳" not in [h["term"] for h in r["hard"]], f"P4-2b should be exempt, got hard={[h['term'] for h in r['hard']]!r}"


def test_p4_2c_bare_verb_no_child_exempt(tmp_path):
    """P4-2 原始句式③：给火塘添柴（无婴儿受事）+ 独自吐纳 → 豁免（not hard）。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=["吐纳"])
    orch = _make_orch(tmp_path)
    text = "夜里，陈老根给火塘添了根柴，掩好门，便独自吐纳起来。陆烬在旁悄悄看着。" + _long_text()
    r = detect_scope_violations(text, 6, timeline_anchor=None, root=orch.root)
    assert "吐纳" not in [h["term"] for h in r["hard"]], f"P4-2c should be exempt, got hard={[h['term'] for h in r['hard']]!r}"


def test_p4_2d_lu_jin_mimics_tuna_hard(tmp_path):
    """P4-2 原始句式④：陆烬学陈老根自己吐纳 → hard（必须阻断）。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=["吐纳"])
    orch = _make_orch(tmp_path)
    text = "深夜，陆烬学陈老根的样子，自己在床上吐纳。" + _long_text()
    r = detect_scope_violations(text, 6, timeline_anchor=None, root=orch.root)
    assert "吐纳" in [h["term"] for h in r["hard"]], f"P4-2d must be hard-blocked, got {[h['term'] for h in r['hard']]!r}"


def test_d2_poison_crisis_hit():
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [
            json.dumps({'scene_id': 1, 'scene_text': '夜色如墨，陈老根抱着陆烬。'}),
            json.dumps({'scene_id': 2, 'scene_text': '天刚蒙蒙亮，薄雾笼着村口古井。赵老四远远避开。'}),
            json.dumps({'scene_id': 3, 'scene_text': '数日过去，是夜。村长王大牛突然捶门：「村口那口井出事了，你快去看看！」陈老根惊醒，披衣起身。'}),
            json.dumps({'scene_id': 4, 'scene_text': '同夜，陈老根平静坐在炕边，反复想了一整夜。太安静了，必须保密。护你到底。对捶门的事只字未提。'}),
        ]
        with open(os.path.join(dd, 'chapter_5_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write(chr(10).join(lines))
        r = detect_off_card_crisis('', 5, {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []}, root=tmpdir)
    assert r['has_issue'] is True, f'Expected poison hit, got {r}'
    assert len(r['off_card_crises']) >= 1


def test_d2_clean_gu_jing_daily_pass():
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [
            json.dumps({'scene_id': 1, 'scene_text': '夜色如墨，陈老根抱着陆烬。物资匮乏。'}),
            json.dumps({'scene_id': 2, 'scene_text': '天刚蒙蒙亮，薄雾笼着村口古井。赵老四远远避开陈老根。于是陈老根另寻他处打水。'}),
            json.dumps({'scene_id': 3, 'scene_text': '午后日光斜照小院，陈老根观察陆烬。婴儿异常沉静。随后陈老根起身回屋。'}),
            json.dumps({'scene_id': 4, 'scene_text': '夜里，陈老根决定抚养陆烬。面对未来，决心守护。接着吹灯睡觉。'}),
        ]
        with open(os.path.join(dd, 'chapter_4_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write(chr(10).join(lines))
        r = detect_off_card_crisis('', 4, {'chapter_num': 4, 'scene_blueprints': [], 'chapter_events': []}, root=tmpdir)
    assert r['has_issue'] is False, f'Expected clean pass, got {r}'


def test_d2_poison_with_generic_connectors_hit():
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [
            json.dumps({'scene_id': 3, 'scene_text': '深夜捶门：「井出事了快去看！」'}),
            json.dumps({'scene_id': 4, 'scene_text': '同夜，于是陈老根平静坐着。随后他想了一夜。接着沉默。对捶门只字未提。'}),
        ]
        with open(os.path.join(dd, 'chapter_5_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write(chr(10).join(lines))
        r = detect_off_card_crisis('', 5, {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []}, root=tmpdir)
    assert r['has_issue'] is True, f'Expected poison-with-connectors hit, got {r}'


def _synthetic_chapters():
    """Return a mapping of chapter number to synthetic chapter text."""
    return {4: CHAPTER_4_SYNTHETIC, 5: CHAPTER_5_SYNTHETIC}


def test_d2_ch4_real_text_regression(tmp_path):
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    ch4 = CHAPTER_4_SYNTHETIC
    paras = [p.strip() for p in ch4.split(chr(10)+chr(10)) if p.strip()]
    dd = tmp_path / 'chapters' / 'draft'
    dd.mkdir(parents=True, exist_ok=True)
    out = dd / 'chapter_4_partial.jsonl'
    for i, p in enumerate(paras[:4], 1):
        out.write_text(
            (out.read_text(encoding="utf-8") if out.exists() else "") +
            json.dumps({'scene_id': i, 'scene_text': p}) + chr(10),
            encoding="utf-8",
        )
    r = detect_off_card_crisis(ch4, 4,
                                {'chapter_num': 4, 'scene_blueprints': [], 'chapter_events': []},
                                root=tmp_path)
    assert r['has_issue'] is False, f'ch4 synthetic text must not trigger crisis, got {r}'


def test_d3_canon_in_early_deterministic_gate(tmp_path):
    from novel_engine.quality.scope_gate import reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=[
        '军中','从军','军营','军医','军中医','医官','教头',
        '将军','千总','百户','朝廷命官','当差官府',
        '气感','内视','吐纳','行气探查','气机探查','调息'])
    orch = _make_orch(tmp_path)
    task_card = _make_ch5_card()
    canon_only = (
        '陈老根年轻时在军中服过役，伤兵身上的症候他见得多了。'
        '那军中医官管那叫「浊气侵体」，他早年也听师父提过气感的事。'
        '他调动起体内气感，注入掌心，以内视看见陆烬胸腔里的灰黑雾气。'
        '内视他人脏腑是他年轻时习武时练出的本事。'
        + _long_text()
    )
    result = orch._deterministic_quality_gate(canon_only, task_card)
    assert result['passed'] is False, f"Canon-only poison must be blocked, got passed={result['passed']}"
    issue_str = ' '.join(result['issues'])
    assert any(kw in issue_str for kw in ['军中','医官','气感','内视','吐纳']), f"Expected canon hits: {result['issues']}"


def test_d3_fail_closed_crisis_gate(monkeypatch, tmp_path):
    from novel_engine.quality import cross_scene_crisis_gate as _cscg
    monkeypatch.setattr(_cscg, 'detect_off_card_crisis', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('simulated crash')))
    orch = _make_orch(tmp_path)
    result = orch._deterministic_quality_gate('陈老根抱着陆烬。夜里安抚他入睡。', _make_ch5_card())
    assert result['passed'] is False, f"Exception must fail-closed, got passed={result['passed']}"
    assert any('跨场危机' in str(v) for v in result['issues'])


def test_d3_fail_closed_naming_gate(monkeypatch, tmp_path):
    from novel_engine.quality import naming_consistency_gate as _ncg
    monkeypatch.setattr(_ncg, 'detect_naming_conflict', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('naming crash')))
    monkeypatch.setattr(_ncg, 'detect_polarity_conflict', lambda *a, **k: {'conflicts': [], 'has_issue': False})
    orch = _make_orch(tmp_path)
    result = orch._deterministic_quality_gate('陈老根抱着陆烬。夜里安抚他入睡。', _make_ch5_card())
    assert result['passed'] is False
    assert any('命名' in str(v) or '极性' in str(v) for v in result['issues'])


def test_d4_polarity_clean_contrast():
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    reset_config_cache()
    r = detect_polarity_conflict('他不避开，反而像被吸引，两者并存。', 5, root=None)
    assert r['has_issue'] is False


def test_d4_polarity_poison_no_paving():
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    reset_config_cache()
    text = '婴儿对浊气本能排斥，怕脏东西，身子骨在抗拒那股异味。然而末场陆烬的反应更像是一种吸引，仿佛他在渴求那股气息。'
    r = detect_polarity_conflict(text, 5, root=None)
    assert r['has_issue'] is True


def test_d4_polarity_clean_negated_repel():
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    reset_config_cache()
    r = detect_polarity_conflict('他并不排斥浊气，而是被吸引，两者和谐共存。', 5, root=None)
    assert r['has_issue'] is False


def test_d4_naming_reads_json_config(tmp_path):
    from novel_engine.quality.naming_consistency_gate import detect_naming_conflict, reset_config_cache
    reset_config_cache()
    text = '村口的古井日日有人取水打水，辘轳转动。但村口那口枯井早已干涸。'
    r = detect_naming_conflict(text, 5, root=None)
    assert r['has_issue'] is True
    assert len(r['conflicts']) >= 1
    assert '古井' in str(r['conflicts'][0]) and '枯井' in str(r['conflicts'][0])


def test_d5_fix_pre_filter_rejects_poison(tmp_path):
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['军中','气感','内视','吐纳','教头','医官'])
    orch = _make_orch(tmp_path)
    poisoned_patch = '陈老根想起年轻时在军中，曾见过教头用类似法子探查伤兵体内瘴毒，于是尝试调动起气感，注入掌心。'
    hard_hits = detect_scope_violations(poisoned_patch, 5, timeline_anchor=None, root=orch.root).get('hard', [])
    assert len(hard_hits) >= 1
    terms_hit = sorted({v.get('term', '') for v in hard_hits})
    assert any(kw in terms_hit for kw in ['军中','教头','气感'])


def test_d6_cache_isolation(tmp_path):
    from novel_engine.quality import scope_gate as _sg, naming_consistency_gate as _ncg
    _sg.reset_config_cache()
    _ncg.reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['军中','气感'])
    orch = _make_orch(tmp_path)
    r1 = _sg.detect_scope_violations('陈老根在军中服过役。', 5, None, orch.root)
    assert any(h['term'] == '军中' for h in r1['hard'])


def test_d7_r17_2_write_time_alignment():
    from novel_engine.quality.outline_coverage_gate import extract_must_cover_beats
    card = _make_ch5_card()
    card.pop('must_cover_beats', None)
    reconciled = extract_must_cover_beats(card)
    assert reconciled is not None
    assert len(reconciled) > 0
    fs_beats = [b for b in reconciled if b.get('category') == 'foreshadow']
    assert len(fs_beats) >= 1




def test_p5_1_env_real_lang_enforce_with_mock(tmp_path, monkeypatch):
    """P5-1：NOVEL_ENGINE_REAL_LANG_ENFORCE=1 时 mock 编排不被真机污染。
    断言 test_pipeline_incremental_patcher 形态的 mock 流水线 success=True。"""
    import os
    monkeypatch.setenv('NOVEL_ENGINE_REAL_LANG_ENFORCE', '1')
    # 复用 _make_orch + mock writer 路径，验证 _is_mock30 正确识别 LLMClient(use_mock=True)
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    from novel_engine.core.llm_client import LLMClient
    orch = _make_orch(tmp_path)
    # 手动注入 use_mock=True 的 LLMClient（模拟真实测试场景）
    mock_llm = LLMClient(use_mock=True)
    # _is_mock30 应识别为 True，不触发 pro-failover
    _real30 = os.environ.get('NOVEL_ENGINE_REAL_LANG_ENFORCE') == '1'
    _is_mock30 = mock_llm is not None and (type(mock_llm).__name__ == 'MockLLMClient' or getattr(mock_llm, 'use_mock', False))
    assert _real30 is True
    assert _is_mock30 is True, f'LLMClient(use_mock=True) must be recognized as mock, _is_mock30={_is_mock30}'
    # 清理 env
    monkeypatch.delenv('NOVEL_ENGINE_REAL_LANG_ENFORCE', raising=False)


def test_p5_1_env_empty_no_pollution(tmp_path, monkeypatch):
    """P5-1：NOVEL_ENGINE_REAL_LANG_ENFORCE 为空时，mock 编排正常。"""
    import os
    monkeypatch.delenv('NOVEL_ENGINE_REAL_LANG_ENFORCE', raising=False)
    from novel_engine.core.llm_client import LLMClient
    mock_llm = LLMClient(use_mock=True)
    _real30 = os.environ.get('NOVEL_ENGINE_REAL_LANG_ENFORCE') == '1'
    _is_mock30 = mock_llm is not None and (type(mock_llm).__name__ == 'MockLLMClient' or getattr(mock_llm, 'use_mock', False))
    assert _real30 is False
    assert _is_mock30 is True


def test_p5_2_teach_zhaolaosi_hard(tmp_path):
    """P5-2 ① 教给赵老四 → hard。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    text = '夜里，陈老根把吐纳法门教给赵老四。' + _long_text()
    r = detect_scope_violations(text, 6, None, orch.root)
    assert '吐纳' in [h['term'] for h in r['hard']], f'教赵老四 must be hard, got {r["hard"]}'


def test_p5_2_teach_lu_jin_hard(tmp_path):
    """P5-2 ② 给陆烬讲解吐纳 → hard。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    text = '夜里，陈老根给陆烬讲解吐纳法门。' + _long_text()
    r = detect_scope_violations(text, 6, None, orch.root)
    assert '吐纳' in [h['term'] for h in r['hard']]


def test_p5_2_bare_verb_still_exempt(tmp_path):
    """P5-2 ③ 给火塘添柴独自吐纳 → 放行。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    text = '夜里，陈老根给火塘添了根柴，掩好门，便独自吐纳起来。陆烬在旁悄悄看着。' + _long_text()
    r = detect_scope_violations(text, 6, None, orch.root)
    assert '吐纳' not in [h['term'] for h in r['hard']], f'给火塘添柴 must be exempt'


def test_p5_2_standard_night_exempt(tmp_path):
    """P5-2 ④ 标准夜间独自吐纳陆烬旁观 → 放行。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    text = '是夜子时，陈老根独自盘膝坐在炕边吐纳呼吸，气息绵长。陆烬在旁偷看。' + _long_text()
    r = detect_scope_violations(text, 6, None, orch.root)
    assert '吐纳' not in [h['term'] for h in r['hard']]


def test_p5_2_lu_jin_self_tuna_hard(tmp_path):
    """P5-2 ⑤ 陆烬本人吐纳 → hard。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    text = '深夜，陆烬学陈老根的样子，自己在床上吐纳。' + _long_text()
    r = detect_scope_violations(text, 6, None, orch.root)
    assert '吐纳' in [h['term'] for h in r['hard']]


def test_p5_2_ch1_tuna_hard(tmp_path):
    """P5-2 ⑥ ch1 吐纳仍 hard。"""
    from novel_engine.quality.scope_gate import detect_scope_violations, reset_config_cache
    reset_config_cache()
    _write_leak_terms_infant(tmp_path, hard_block=['吐纳'])
    orch = _make_orch(tmp_path)
    text = '陈老根夜里吐纳呼吸。' + _long_text()
    r = detect_scope_violations(text, 1, None, orch.root)
    assert '吐纳' in [h['term'] for h in r['hard']]


def test_integration_poison_blocked_no_novel_write(tmp_path):
    from novel_engine.pipeline.chapter_journal import append_scene
    orch = _make_orch(tmp_path)
    task_card = _make_ch5_card()
    poison_scenes = {
        1: '陈老根抱着陆烬在屋里。多年军中往事历历在目。那军中医官说这是浊气侵体。\n',
        2: '村口古井边打水。村人日日来此取水。但村口那口枯井早已干涸。\n',
        3: '深夜王大牛捶门：「井出事了，快去看！」陈老根调动起体内气感，以内视看见陆烬胸腔灰黑雾气。\n',
        4: '同夜陈老根静坐，对捶门只字未提。婴儿排斥浊气，却又被吸引。\n',
    }
    for sn, txt in poison_scenes.items():
        append_scene(tmp_path, 5, {'scene_id': sn, 'scene_text': txt, 'hook': '', 'beats': []})
    mock_writer = MagicMock()
    mock_out = MagicMock()
    mock_out.scene_text = chr(10).join(poison_scenes.values())
    mock_out.beats = []; mock_out.hook = ''
    mock_writer.generate_scene.return_value = mock_out
    orch.writer = mock_writer
    def _fast_write(task_card, synopsis):
        parts = [poison_scenes.get(int(d.get('scene_id', 0)), '')
                 for d in sorted([{'scene_id': k} for k in poison_scenes], key=lambda x: int(x['scene_id']))]
        return _SEP.join(parts)
    orch._stage_write = _fast_write
    orch._ensure_chinese = lambda n: n
    def _mock_review(chapter_num, task_card, synopsis, current, world_state=None):
        return {'review': {'chapter_num': chapter_num,
                           'scores': {'plot_consistency': 20, 'character_consistency': 18,
                                      'foreshadow_execution': 18, 'style_match': 12,
                                      'pacing': 10, 'innovation': 14},
                           'total_score': 92, 'verdict': 'pass', 'issues': [], 'fix_scope': ''},
                'score': 92.0, 'verdict': 'pass', 'review_unstable': False}
    orch._stage_review = _mock_review
    with patch.object(orch, '_enforce_word_count', side_effect=lambda n, lo, hi: n):
        result = orch.generate_single_chapter(5)
    assert result.get('published') is not True
    novel_file = tmp_path / 'chapters' / 'novel' / 'chapter_5.txt'
    assert not novel_file.exists()


# ===== P7-1: 危机门真实稿命中 + 干净夹具 =====

def _load_real_ch5_scenes():
    """加载真实 ch5 最终 4 场的场景文本（fixture 固定快照，非运行时目录）。"""
    with open(_FIXTURES_ROOT / 'chapter_5_partial.jsonl', encoding='utf-8') as f:
        records = [json.loads(l) for l in f]
    # fixture 文件已含 authoritative scenes 1-4，直接返回
    return {int(r['scene_id']): r['scene_text'] for r in records}


def test_p7_1_real_ch5_crisis_hit():
    """P7-1 真实 ch5：场3拍门+吼+快开门 + 场4夜间静坐无承接 → has_issue=True。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf, os
    scenes_4 = {
        1: '陈老根在屋里坐着，听着外面的风声。',
        2: '夜深了，村里很安静。',
        3: '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。',
        4: '当晚陈老根照常静坐，对昨晚的事只字未提。',
    }
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [json.dumps({'scene_id': sid, 'scene_text': txt})
                 for sid, txt in sorted(scenes_4.items())]
        with open(os.path.join(dd, 'chapter_5_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        r = detect_off_card_crisis('', 5,
                                   {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []},
                                   root=tmpdir)
    assert r['has_issue'] is True, f'Expected ch5 crisis hit, got {r}'
    assert len(r['off_card_crises']) >= 1
    assert r['off_card_crises'][0]['scene_id'] == 3


def test_p7_1_real_ch5_recall_not_exempt():
    """P7-1 真实 ch5：场4回忆井边不得豁免上门事件。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf, os
    scenes_4 = {
        1: '陈老根在屋里坐着，听着外面的风声。',
        2: '夜深了，村里很安静。',
        3: '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。',
        4: '当晚陈老根照常静坐，对昨晚的事只字未提。',
    }
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [json.dumps({'scene_id': sid, 'scene_text': txt})
                 for sid, txt in sorted(scenes_4.items())]
        with open(os.path.join(dd, 'chapter_5_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        r = detect_off_card_crisis('', 5,
                                   {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []},
                                   root=tmpdir)
    assert r['has_issue'] is True, f'回忆井边不得豁免上门危机, got {r}'


def test_p7_1_clean_dog_bark_no_false_positive():
    """P7-1 干净夹具：狗叫/鸡鸣不误报危机。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf, os
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [
            json.dumps({'scene_id': 1, 'scene_text': '夜里，远处传来几声犬吠，鸡也在叫。'}),
            json.dumps({'scene_id': 2, 'scene_text': '陈老根照常入睡。'}),
        ]
        with open(os.path.join(dd, 'chapter_5_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        r = detect_off_card_crisis('', 5,
                                   {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []},
                                   root=tmpdir)
    assert r['has_issue'] is False, f'狗叫不应误报危机, got {r}'


def test_p7_1_clean_zuchuan_old_saying_no_false_positive():
    """P7-1 干净夹具：「祖传老话」不误报危机。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf, os
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [
            json.dumps({'scene_id': 1, 'scene_text': '村里有祖传老话，说井里有东西。'}),
            json.dumps({'scene_id': 2, 'scene_text': '陈老根不信，照常过日子。'}),
        ]
        with open(os.path.join(dd, 'chapter_5_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        r = detect_off_card_crisis('', 5,
                                   {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []},
                                   root=tmpdir)
    assert r['has_issue'] is False, f'祖传老话不应误报危机, got {r}'


def test_p7_1_clean_child_hungry_no_false_positive():
    """P7-1 干净夹具：孩子饿叫热粥不误报危机。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf, os
    with _tf.TemporaryDirectory() as tmpdir:
        dd = os.path.join(tmpdir, 'chapters', 'draft')
        os.makedirs(dd, exist_ok=True)
        lines = [
            json.dumps({'scene_id': 1, 'scene_text': '陆烬饿了，大哭起来。陈老根忙去热粥。'}),
            json.dumps({'scene_id': 2, 'scene_text': '喂完粥，陆烬安静睡了。'}),
        ]
        with open(os.path.join(dd, 'chapter_5_partial.jsonl'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        r = detect_off_card_crisis('', 5,
                                   {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []},
                                   root=tmpdir)
    assert r['has_issue'] is False, f'孩子饿叫热粥不应误报危机, got {r}'


# ===== P7-2: 极性门真实稿命中 + 干净夹具 =====

def test_p7_2_real_ch5_polarity_hit():
    """P7-2 真实 ch5：场2/3排斥 + 场4吸引比喻 → has_issue=True。"""
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    text = (
        '婴儿对浊气本能排斥，怕脏东西，身子骨在抗拒那股异味。'
        '然而末场陆烬的反应更像是一种吸引，仿佛他在渴求那股气息。'
    )
    reset_config_cache()
    r = detect_polarity_conflict(text, 5, root=None)
    assert r['has_issue'] is True, f'Expected ch5 polarity hit, got {r}'
    assert len(r['conflicts']) >= 1


def test_p7_2_clean_transition_paving_no_hit():
    """P7-2 干净夹具：有转化铺垫的吸引不算矛盾。"""
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    reset_config_cache()
    text = '起初陆烬排斥浊气，但后来发现那气并不伤人，反而令他舒服，于是开始接纳。'
    r = detect_polarity_conflict(text, 5, root=None)
    assert r['has_issue'] is False, f'转化铺垫应豁免, got {r}'


def test_p7_2_clean_ch6_tuna_no_hit():
    """P7-2 干净夹具：ch6 陈老根自身吐纳与婴儿对浊气极性无关，不误报。"""
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    reset_config_cache()
    text = '是夜子时，陈老根独自盘膝坐在炕边吐纳呼吸，气息绵长。陆烬在旁偷看。'
    r = detect_polarity_conflict(text, 6, root=None)
    assert r['has_issue'] is False, f'ch6 吐纳不应误报极性, got {r}'


def test_p7_2_clean_bare_chuan_no_hit():
    """P7-2 干净夹具：裸单字"传"不得误报吸引。"""
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    reset_config_cache()
    text = '村里有祖传老话，说井底有东西。寻常人听到都会害怕。'
    r = detect_polarity_conflict(text, 5, root=None)
    assert r['has_issue'] is False, f'裸单字传不应误报, got {r}'


# ===== P7B: 危机门生产路径 + 极性门目标绑定修复 =====

def test_p7b_crisis_production_path_hit():
    """P7B 危机门生产路径：root=tmp（无journal）+ blueprints + 最终纯化正文 → has_issue=True。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import tempfile as _tf
    tmpdir = _tf.mkdtemp()
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [
            {'scene_num': 1, 'goal': '安抚夜啼', 'beats': []},
            {'scene_num': 2, 'goal': '打水遇村民', 'beats': []},
            {'scene_num': 3, 'goal': '观察陆烬', 'beats': []},
            {'scene_num': 4, 'goal': '决定抚养', 'beats': []},
        ],
        'chapter_events': []
    }
    purified = (
        '陈老根在屋里坐着，听着外面的风声。\n\n'
        '夜深了，村里很安静。\n\n'
        '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。\n\n'
        '当晚陈老根照常静坐，对昨晚的事只字未提。'
    )
    r = detect_off_card_crisis(purified, 5, task_card, root=tmpdir)
    assert r['has_issue'] is True, f'生产路径应命中危机, got {r}'
    assert len(r['off_card_crises']) >= 1
    # 危机在第2场（paragraph split: scenes 1-2 合并短段，crisis 在 scene 2）
    assert r['off_card_crises'][0]['scene_id'] == 2


def test_p7b_crisis_scene_texts_param():
    """P7B 危机门 scene_texts 参数：直接传入最终场景 → has_issue=True。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    scenes_4 = {
        1: '陈老根在屋里坐着，听着外面的风声。',
        2: '夜深了，村里很安静。',
        3: '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。',
        4: '当晚陈老根照常静坐，对昨晚的事只字未提。',
    }
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [],
        'chapter_events': []
    }
    r = detect_off_card_crisis('', 5, task_card, scene_texts=scenes_4)
    assert r['has_issue'] is True, f'scene_texts 应命中危机, got {r}'
    assert r['off_card_crises'][0]['scene_id'] == 3


def test_p7b_polarity_ch1_ch2_ch3_clean(tmp_path):
    """P7B 极性门：ch1/ch2/ch3 已提交章节不误报。"""
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    chaps = _synthetic_chapters()
    for ch in [1, 2, 3]:
        text = chaps.get(ch, CHAPTER_4_SYNTHETIC)
        reset_config_cache()
        r = detect_polarity_conflict(text, ch, root=None)
        assert r['has_issue'] is False, f'ch{ch} 不应误报极性, got {r}'

# ===== P7C: 同句转化豁免 + 危机门权威分场 =====

def test_p7c_polarity_same_sentence_explicit_transform_exempt():
    """P7C 极性门：同句显式转化须豁免 → False。"""
    from novel_engine.quality.naming_consistency_gate import detect_polarity_conflict, reset_config_cache
    reset_config_cache()
    text = '起初他对浊气本能排斥、咳着躲开，后来日子久了，竟渐渐学着将那气息丝丝纳入。'
    r = detect_polarity_conflict(text, 5, root=None)
    assert r['has_issue'] is False, f'同句显式转化应豁免, got {r}'


def test_p7c_crisis_scene_texts_param_hit():
    """P7C 危机门：scene_texts 参数直接传入最终分场 → has_issue=True。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    scenes_4 = {
        1: '陈老根在屋里坐着，听着外面的风声。',
        2: '夜深了，村里很安静。',
        3: '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。',
        4: '当晚陈老根照常静坐，对昨晚的事只字未提。',
    }
    task_card = {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []}
    r = detect_off_card_crisis('', 5, task_card, scene_texts=scenes_4)
    assert r['has_issue'] is True, f'scene_texts 应命中危机, got {r}'
    assert r['off_card_crises'][0]['scene_id'] == 3


# ===== P7D: 危机门 str/dict 契约兼容性 =====

def test_p7d_crisis_scene_texts_str_n5_real_hit():
    """P7D 危机门：scene_texts 传含※的字符串（生产真实类型）→ has_issue=True，不抛异常。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    scenes_4 = {
        1: '陈老根在屋里坐着，听着外面的风声。',
        2: '夜深了，村里很安静。',
        3: '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。',
        4: '当晚陈老根照常静坐，对昨晚的事只字未提。',
    }
    assembled = chr(0x203B).join([scenes_4[i] for i in sorted(scenes_4.keys())])
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}, {'scene_num': 3}, {'scene_num': 4}],
        'chapter_events': []
    }
    # 不应抛 AttributeError 或 TypeError
    r = detect_off_card_crisis(assembled, 5, task_card, scene_texts=assembled)
    assert r['has_issue'] is True, f'ch5 ※字符串应命中危机, got {r}'
    assert r['off_card_crises'][0]['scene_id'] == 3



def test_p7d_crisis_scene_texts_str_ch4_clean():
    """P7D 危机门：干净章（ch4）以※字符串传入 → has_issue=False，不抛异常。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    ch4_text = CHAPTER_4_SYNTHETIC
    task_card = {
        'chapter_num': 4,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}, {'scene_num': 3}, {'scene_num': 4}],
        'chapter_events': []
    }
    r = detect_off_card_crisis(ch4_text, 4, task_card, scene_texts=ch4_text)
    assert r['has_issue'] is False, f'ch4 ※字符串不应误报, got {r}'


def test_p7d_crisis_scene_texts_bad_type_raises():
    """P7D 危机门：scene_texts 传入非法类型必须抛 TypeError。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    import pytest
    with pytest.raises(TypeError, match='scene_texts'):
        detect_off_card_crisis('', 5, {'chapter_num': 5, 'scene_blueprints': [], 'chapter_events': []},
                               scene_texts=123)


def test_p7d_orchestrator_final_gate_no_fail_closed():
    """P7D 集成：orchestrator 最终门传含※字符串 scene_texts 不应触发 fail-closed gate 异常。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    scenes_4 = {
        1: '陈老根在屋里坐着，听着外面的风声。',
        2: '夜深了，村里很安静。',
        3: '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。',
        4: '当晚陈老根照常静坐，对昨晚的事只字未提。',
    }
    assembled = chr(0x203B).join([scenes_4[i] for i in sorted(scenes_4.keys())])
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}, {'scene_num': 3}, {'scene_num': 4}],
        'chapter_events': []
    }
    try:
        r = detect_off_card_crisis(assembled, 5, task_card, scene_texts=assembled)
        issues = []
        if r['has_issue']:
            issues.append('[跨场危机] off-card crisis 未承接')
        assert not any('gate异常' in iss for iss in issues), f'不应有 gate 异常: {issues}'
    except Exception as e:
        raise AssertionError(f'orchestrator 最终门调用应成功，实际抛 {type(e).__name__}: {e}')


def test_p7d_crisis_scene_texts_str_summon_door_same_scene():
    """P7D 危机门：召唤+开门同属一场的 ※字符串 → has_issue=False，不抛异常。"""
    from novel_engine.quality.cross_scene_crisis_gate import detect_off_card_crisis
    # 合成：单场文本，内含"拍门/开门"召唤触发且同场承接，无后续场
    single_scene = (
        "日头偏西，村口传来急促脚步声。\n\n"
        "「陈老根！快开门！出事了！」赵老四在门外用力拍门，嗓音嘶哑。\n\n"
        "陈老根推开木门，只见赵老四满脸尘土，气喘吁吁。"
    )
    # 同场 ※ 串（只有一个段，join 后仍是单段）
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}],
        'chapter_events': [{'one_line_summary': '赵老四拍门报信'}]
    }
    r = detect_off_card_crisis(single_scene, 5, task_card, scene_texts=single_scene)
    assert r['has_issue'] is False, f'同场召唤+开门应不误报, got {r}'


def test_p7d_orchestrator_integration_gate_with_str_scene_texts(tmp_path):
    """P7D 薄集成：经 _deterministic_quality_gate（line 1859 形态）传入含※字符串，
    不应出现 gate异常(fail-closed)，真实 ch5 应命中 [跨场危机]。"""
    import json as _json
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    # 准备 tmp project（最小配置，use_mock=True 避免 LLM 调用）
    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'runtime_config.json').write_text(
        _json.dumps({'llm': {'use_mock': True}, 'review_llm': {'use_mock': True},
                     'fallback_llm': {'use_mock': True}, 'chapter_target_chars': 7500}),
        encoding='utf-8')
    (tmp_path / 'config' / 'llm_providers.json').write_text(
        _json.dumps({'active_profile': 'test', 'profiles': {'test': {
            'base_url': 'https://test.example.com/v1', 'api_key_env': 'TEST_KEY_R18',
            'timeout_s': 60, 'max_retries': 1, 'default_extra_body': {},
            'phases': {'scenes': {'models': ['test-model'], 'response_format': None},
                        'polish': {'models': ['test-model'], 'response_format': None, 'concurrency': 4},
                        'planning': {'models': ['test-model'], 'response_format': None},
                        'review': {'models': ['test-model'], 'response_format': None}}}}}),
        encoding='utf-8')
    (tmp_path / 'config' / 'forbidden.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'config' / 'quality_policy.json').write_text(
        _json.dumps({'publication_line': 88, 'soft_publication_line': 85,
                     'min_ratio': 0.85, 'max_ratio': 1.2, 'tolerance_chars': 500}),
        encoding='utf-8')
    import os
    os.environ['TEST_KEY_R18'] = 'sk-test-r18'

    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}

    # 合成含跨场危机的 ch5 风格文本（拍门+只字未提）
    assembled_str = (
        '陈老根在屋里坐着，听着外面的风声。\n\n'
        '夜深了，村里很安静。\n\n'
        '忽然有人疯狂拍门大喊快开门出大事了陈老根被惊醒。\n\n'
        '当晚陈老根照常静坐，对昨晚的事只字未提。'
    )
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}, {'scene_num': 3}, {'scene_num': 4}],
        'chapter_events': []
    }
    # 走 line 1859 形态：_deterministic_quality_gate(text, task_card, scene_texts=assembled_str)
    result = orch._deterministic_quality_gate(assembled_str, task_card, scene_texts=assembled_str)
    issues_list = result.get('issues') or []
    issues_str = ' '.join(issues_list)
    # 不得有 gate 异常 fail-closed
    assert 'gate异常' not in issues_str, f'不应有 gate异常，got issues={issues_list}'
    # 真实 ch5 应命中跨场危机（passed=False 因为 issues 非空）
    assert result['passed'] is False, f'真实 ch5 应有硬伤阻断, got passed={result["passed"]}, issues={issues_list}'
    assert any('[跨场危机]' in iss for iss in issues_list), \
        f'应包含跨场危机命中，got issues={issues_list}'
def test_p0_force_best_else_branch_final_det_not_unbound(tmp_path):
    """回归：force-best else 分支（best_score < min_ch，非 gray_final）中
    final_det 必须已赋值，不再因 UnboundLocalError 被外层 except 静默吞掉。
    见 pipeline_orchestrator.py L941 前移修复。"""
    import json
    import os
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'runtime_config.json').write_text(
        json.dumps({'llm': {'use_mock': True}, 'review_llm': {'use_mock': True},
                    'fallback_llm': {'use_mock': True}, 'chapter_target_chars': 7500}),
        encoding='utf-8')
    (tmp_path / 'config' / 'llm_providers.json').write_text(
        json.dumps({'active_profile': 'test', 'profiles': {'test': {
            'base_url': 'https://test.example.com/v1', 'api_key_env': 'TEST_KEY_FB',
            'timeout_s': 60, 'max_retries': 1, 'default_extra_body': {},
            'phases': {'scenes': {'models': ['test-model'], 'response_format': None},
                        'polish': {'models': ['test-model'], 'response_format': None, 'concurrency': 4},
                        'planning': {'models': ['test-model'], 'response_format': None},
                        'review': {'models': ['test-model'], 'response_format': None}}}}}),
        encoding='utf-8')
    (tmp_path / 'config' / 'forbidden.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'config' / 'quality_policy.json').write_text(
        json.dumps({'publication_line': 88, 'soft_publication_line': 85,
                     'min_ratio': 0.85, 'max_ratio': 1.2, 'tolerance_chars': 500}),
        encoding='utf-8')
    os.environ['TEST_KEY_FB'] = 'sk-test-fb'

    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}

    # 不含※分隔符的短文 → _scene_texts_for_gate = None
    short_text = '陈老根在屋里坐着，听着外面的风声。\n\n夜深了，村里很安静。'
    task_card = {
        'chapter_num': 1,
        'core_goal': '测试目标',
        'scene_blueprints': [{'scene_num': 1, 'one_line_summary': '场景1'}],
        'chapter_events': []
    }

    # 直接设置属性绕过 LLM 调用
    orch._frozen_task_cards = {1: task_card}
    orch._frozen_synopsis = {1: {'synopsis': '测试大纲'}}
    orch._stage_world_sim = lambda cn: {}
    orch._stage_directing = lambda cn, ws, bypass_cache=None: task_card
    orch._stage_synopsis = lambda tc: {'synopsis': '测试大纲'}
    orch._stage_write = lambda tc, syn: short_text
    orch._ensure_chinese = lambda n: n
    orch._finalize_current_novel = lambda cn: None

    # mock review: verdict='fix'，score=80（< min_ch=88，且不在 gray band 85-88 因为 <85）
    def _mock_review(chapter_num, task_card, synopsis, current, world_state=None):
        return {
            'review': {'chapter_num': chapter_num, 'scores': {'plot_consistency': 20, 'character_consistency': 18,
                                                              'foreshadow_execution': 18, 'style_match': 12,
                                                              'pacing': 10, 'innovation': 14},
                       'total_score': 80, 'verdict': 'fix', 'issues': [], 'fix_scope': ''},
            'score': 80.0, 'verdict': 'fix', 'review_unstable': False
        }
    orch._stage_review = _mock_review

    # mock _select_final_candidate: 返回 score=80（低于 min_ch），触发 else 分支
    def _mock_select(chapter_num, best_state, result):
        return (80, short_text, short_text, {})
    orch._select_final_candidate = _mock_select

    # mock _deterministic_quality_gate: passed=False（确保 _gray_final=False）
    def _mock_det(text, tc, scene_texts=None):
        return {'passed': False, 'issues': ['测试issue']}
    orch._deterministic_quality_gate = _mock_det

    result = orch.generate_single_chapter(1)

    # 断言：不抛异常，else 分支正确执行
    assert result['success'] is True, f"expected success, got {result}"
    # force-best 路径：published=False（不入 novel/），但 force_published=True（进 draft/）
    assert result.get('force_published') is True, f"expected force_published=True, got {result}"
    assert result['success'] is True, f"expected success, got {result}"
    assert 'note' in result, f"expected 'note' in result for force-best path, got keys={list(result.keys())}"
    note = result['note']
    # note 应包含 det 快照信息
    assert 'det=' in note or 'hard gate' in note or 'best' in note.lower(),         f"note should reference det snapshot, got: {note!r}"
    # 不是 gray_band（score 80 < soft_line 85）
    assert result.get('gray_band_release') is not True,         "should NOT be gray_band (score 80 < soft_line 85)"
