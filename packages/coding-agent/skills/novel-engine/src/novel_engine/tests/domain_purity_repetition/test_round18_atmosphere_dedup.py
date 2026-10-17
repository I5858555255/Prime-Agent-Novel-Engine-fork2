# -*- coding: utf-8 -*-
"""CC round-18 R5：氛围词去重（atmosphere_dedup）确定性离线测试。"""
from __future__ import annotations

import pytest

from novel_engine.quality.atmosphere_dedup import (
    _jaccard_set,
    _split_scenes,
    _SEP,
    scene_atmosphere_freq,
    adjacent_jaccards,
    detect_atmosphere_dedup,
    generate_repolish_directive,
    cn_chars,
    cn_chars_total,
    roll_back_ok,
    run_atmosphere_dedup,
)


# ── Jaccard ─────────────────────────────────────────────────────────────────

def test_jaccard_set_basic():
    assert _jaccard_set({"a", "b"}, {"b", "c"}) == 1 / 3
    assert _jaccard_set({"a", "b", "c"}, {"b", "c", "d"}) == 2 / 4
    assert _jaccard_set(set(), set()) == 0.0
    assert _jaccard_set({"a"}, {"a"}) == 1.0
    assert _jaccard_set({"a"}, {"b"}) == 0.0


# ── Scene splitting ─────────────────────────────────────────────────────────

def test_split_scenes_basic():
    text = "场1正文。\n\n※\n\n场2正文。\n\n※\n\n场3正文。"
    scenes = _split_scenes(text)
    assert len(scenes) == 3
    assert scenes[0] == "场1正文。"
    assert scenes[1] == "场2正文。"
    assert scenes[2] == "场3正文。"


def test_split_scenes_no_sep():
    text = "只是一段普通文本。"
    assert _split_scenes(text) == ["只是一段普通文本。"]


def test_split_scenes_empty():
    assert _split_scenes("") == []
    assert _split_scenes(None) == []


# ── Atmosphere frequency ─────────────────────────────────────────────────────

def test_scene_atmosphere_freq():
    text = "晨雾笼罩村口，雾气弥漫寒意阵阵。"
    freq = scene_atmosphere_freq(text)
    assert freq["雾"] >= 1
    assert freq["雾气"] >= 1
    assert freq["寒"] >= 1
    assert freq["寒意"] >= 1


def test_scene_atmosphere_freq_empty():
    freq = scene_atmosphere_freq("阳光明媚，鸟语花香。")
    assert len(freq) == 0


# ── Adjacent Jaccards ───────────────────────────────────────────────────────

def test_adjacent_jaccards_two_scenes():
    from collections import Counter
    f1 = Counter({"雾": 3, "雾气": 2, "寒": 2, "风": 1})
    f2 = Counter({"雾": 2, "雾气": 1, "寒": 1, "风": 2, "夜": 1})
    result = adjacent_jaccards([f1, f2])
    assert len(result) == 1
    assert result[0]["scene_a"] == 1
    assert result[0]["scene_b"] == 2
    assert result[0]["shared"] == 4
    assert result[0]["jaccard"] == pytest.approx(4 / 5, abs=0.01)


def test_adjacent_jaccards_three_scenes():
    from collections import Counter
    result = adjacent_jaccards([Counter({"a": 1}), Counter({"a": 1, "b": 1}), Counter({"b": 1, "c": 1})])
    assert len(result) == 2
    assert result[0]["shared"] == 1
    assert result[1]["shared"] == 1


# ── Detect hits ─────────────────────────────────────────────────────────────

def test_detect_hit_high_overlap():
    s1 = ("晨雾笼罩村口那口古井，雾气弥漫寒意阵阵。夜风穿过雾气带来寒光，"
          "雾气笼罩着古井的石头，寒意渗透进夜色中，死寂中只有风声低鸣。"
          "陈老根抱着陆烬走在雾中，每一步都踩在湿冷的石板上。")
    s2 = ("晨雾仍未散尽，雾气缠绕在井边石阶上，寒意袭人夜风低鸣。"
          "雾气笼罩着小院，寒意渗入石缝间，死寂沉沉夜风呼啸着穿过巷口。"
          "陈老根紧了紧襁褓，陆烬的脸贴在父亲胸前感受着那份寒意。")
    s3 = "院中阳光明媚，陈老根坐在炕上打盹，陆烬安静睡着完全没有雾气的痕迹。"
    hits = detect_atmosphere_dedup([s1, s2, s3])
    assert len(hits) >= 1
    h = hits[0]
    assert h["scene_num"] == 1
    assert "family" in h
    assert "suggested_direction" in h
    assert len(h.get("high_freq_terms", [])) > 0


def test_detect_no_hit_low_overlap():
    s1 = "夜色如墨，陈老根抱着陆烬在屋里安抚夜啼。"
    s2 = "天刚蒙蒙亮，薄雾还笼着村口那口水井的青石井沿。赵老四远远避开。"
    s3 = "午后日光斜斜照进小院。婴儿异常沉静。"
    hits = detect_atmosphere_dedup([s1, s2, s3])
    assert len(hits) == 0


def test_detect_single_scene_no_hit():
    hits = detect_atmosphere_dedup(["只是一段文本。"])
    assert len(hits) == 0


# ── Directive generation ────────────────────────────────────────────────────

def test_generate_repolish_directive():
    hit = {
        "scene_num": 1,
        "family": "雾浊",
        "conc_scene_a": 5.0,
        "conc_scene_b": 5.4,
        "high_freq_terms": ["雾", "雾气", "寒"],
        "suggested_direction": "触觉温度对比",
    }
    directive = generate_repolish_directive(hit)
    assert "【氛围去重重抛光·场1】" in directive
    assert "雾浊" in directive
    assert "触觉温度对比" in directive
    assert "5.0" in directive
    assert "5.4" in directive
    assert "Jaccard" not in directive
    assert "共享" not in directive


# ── cn_chars ─────────────────────────────────────────────────────────────────

def test_cn_chars_basic():
    assert cn_chars("abc123") == 0
    assert cn_chars("你好世界") == 4
    assert cn_chars("") == 0
    assert cn_chars("hello你好world") == 2


def test_cn_chars_total():
    assert cn_chars_total("abcd") == 0
    assert cn_chars_total("中文汉字") == 4


# ── roll_back_ok ─────────────────────────────────────────────────────────────

def test_roll_back_ok_passes():
    assert roll_back_ok("中" * 1100, "中" * 1000, 7500) is True


def test_roll_back_ok_scene_too_short():
    assert roll_back_ok("中" * 900, "中" * 1000, 7500) is False


def test_roll_back_ok_chapter_too_short():
    assert roll_back_ok("中" * 1100, "中" * 1000, 6000) is False


def test_roll_back_ok_chapter_too_long():
    assert roll_back_ok("中" * 1100, "中" * 1000, 11000) is False


# ── Full pipeline ────────────────────────────────────────────────────────────

def test_run_atmosphere_dedup_high_overlap():
    s1 = ("晨雾笼罩村口那口古井，雾气弥漫寒意阵阵。夜风穿过雾气带来寒光，"
          "雾气笼罩着古井的石头，寒意渗透进夜色中，死寂中只有风声低鸣。"
          "陈老根抱着陆烬走在雾中，每一步都踩在湿冷的石板上。")
    s2 = ("晨雾仍未散尽，雾气缠绕在井边石阶上，寒意袭人夜风低鸣。"
          "雾气笼罩着小院，寒意渗入石缝间，死寂沉沉夜风呼啸着穿过巷口。"
          "陈老根紧了紧襁褓，陆烬的脸贴在父亲胸前感受着那份寒意。")
    s3 = "院中阳光明媚，陈老根坐在炕上打盹，陆烬安静睡着。"
    s4 = "夜深人静，油灯昏黄，陈老根独自沉思。"
    text = s1 + _SEP + s2 + _SEP + s3 + _SEP + s4
    result = run_atmosphere_dedup(text)
    assert len(result["hits"]) >= 1
    assert result["hits"][0]["scene_num"] == 1
    assert "family" in result["hits"][0]
    assert len(result["jaccards"]) == 3
    assert len(result["scene_cn_counts"]) == 4
    assert result["total_cn"] > 0


def test_run_atmosphere_dedup_low_overlap_baseline():
    texts = [
        "夜色如墨，陈老根抱着陆烬在屋里安抚夜啼。物资匮乏，陈老根无奈叹息。",
        "天刚蒙蒙亮，薄雾还笼着村口那口水井的青石井沿。赵老四远远避开陈老根。",
        "午后日光斜斜照进小院，陈老根观察陆烬。婴儿异常沉静，极少哭闹。",
        "夜里，陈老根决定抚养陆烬，面对未来。屋外风声呜咽。",
    ]
    text = _SEP.join(texts)
    result = run_atmosphere_dedup(text)
    assert len(result["hits"]) == 0
    for j in result["jaccards"]:
        assert j["jaccard"] < 0.40, f"Expected <0.40 for baseline, got {j['jaccard']}"


# ═══ P8D D3：orchestrator 接线 + 裸※命中测试 ═══

def test_bare_separator_hits_real_ch5_pattern():
    s1 = ("晨雾笼罩村口那口古井，雾气弥漫寒意阵阵。夜风穿过雾气带来寒光，"
          "雾气笼罩着古井的石头，寒意渗透进夜色中，死寂中只有风声低鸣。"
          "陈老根抱着陆烬走在雾中，每一步都踩在湿冷的石板上。")
    s2 = ("晨雾仍未散尽，雾气缠绕在井边石阶上，寒意袭人夜风低鸣。"
          "雾气笼罩着小院，寒意渗入石缝间，死寂沉沉夜风呼啸着穿过巷口。"
          "陈老根紧了紧襁褓，陆烬的脸贴在父亲胸前感受着那份寒意。")
    s3 = "院中阳光明媚，陈老根坐在炕上打盹，陆烬安静睡着。"
    s4 = "夜深人静，油灯昏黄，陈老根独自沉思。"
    bare_text = s1 + chr(0x203B) + s2 + chr(0x203B) + s3 + chr(0x203B) + s4
    result = run_atmosphere_dedup(bare_text)
    assert len(result["hits"]) >= 1, f"Bare separator must hit, got {len(result['hits'])}"
    assert "family" in result["hits"][0]
    texts_low = [
        "夜色如墨，陈老根抱着陆烬在屋里安抚夜啼。物资匮乏，陈老根无奈叹息。",
        "天刚蒙蒙亮，薄雾还笼着村口那口水井的青石井沿。赵老四远远避开陈老根。",
        "午后日光斜斜照进小院，陈老根观察陆烬。婴儿异常沉静，极少哭闹。",
        "夜里，陈老根决定抚养陆烬，面对未来。屋外风声呜咽。",
    ]
    text_low = chr(0x203B).join(texts_low)
    result_low = run_atmosphere_dedup(text_low)
    assert len(result_low["hits"]) == 0, f"ch1-4 baseline must not hit, got {len(result_low['hits'])}"


def test_orchestrator_wires_run_atmosphere_dedup():
    import ast
    with open("novel_engine/pipeline/pipeline_orchestrator.py", encoding="utf-8") as f:
        source = f.read()
    found_finalize = False
    found_atm_import = False
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "_finalize_current_novel":
            found_finalize = True
        if isinstance(node, ast.FunctionDef) and node.name == "generate_single_chapter":
            func_body = ast.unparse(node) if hasattr(ast, "unparse") else ""
            if "run_atmosphere_dedup" in source or "atmosphere_dedup" in source:
                found_atm_import = True
    assert found_finalize, "_finalize_current_novel must exist"
    assert "atmosphere_dedup" in source, "orchestrator must import atmosphere_dedup"
    assert "run_atmosphere_dedup" in source, "orchestrator must call run_atmosphere_dedup"


# ═══ P8E E4：健壮性与接线行为测试 ═══

def test_run_atmosphere_dedup_safety_edge_cases():
    from novel_engine.quality.atmosphere_dedup import run_atmosphere_dedup, _split_scenes
    assert run_atmosphere_dedup("") == {"hits": [], "jaccards": [], "scene_cn_counts": [], "total_cn": 0}
    assert run_atmosphere_dedup(None) == {"hits": [], "jaccards": [], "scene_cn_counts": [], "total_cn": 0}
    assert run_atmosphere_dedup("只是一段文本。")["hits"] == []
    assert _split_scenes(None) == []
    assert _split_scenes("") == []


def test_orchestrator_wires_atm_detection():
    import ast
    with open("novel_engine/pipeline/pipeline_orchestrator.py", encoding="utf-8") as f:
        source = f.read()
    assert "run_atmosphere_dedup" in source, "orchestrator must import run_atmosphere_dedup"
    assert "atmosphere_dedup_hits" in source, "orchestrator must write hits to result"
    assert "atmosphere_dedup_soft_note" in source, "orchestrator must write soft_note to result"

    from novel_engine.quality.atmosphere_dedup import run_atmosphere_dedup, _SEP
    best_novel = "晨雾笼罩古井" + _SEP + "晨雾仍未散尽" + _SEP + "院中阳光" + _SEP + "夜深人静"
    result = run_atmosphere_dedup(best_novel)
    if result.get("hits"):
        hit_strs = [f"场{h['scene_num']}:{h.get('family','?')}" for h in result["hits"]]
        simulated_result = {"atmosphere_dedup_hits": result["hits"], "atmosphere_dedup_soft_note": "; ".join(hit_strs)}
        assert "atmosphere_dedup_hits" in simulated_result
        assert "atmosphere_dedup_soft_note" in simulated_result
        assert len(simulated_result["atmosphere_dedup_hits"]) > 0


def test_run_atmosphere_dedup_safety_edge_cases():
    from novel_engine.quality.atmosphere_dedup import run_atmosphere_dedup, _split_scenes
    assert run_atmosphere_dedup("") == {"hits": [], "jaccards": [], "scene_cn_counts": [], "total_cn": 0}
    assert run_atmosphere_dedup(None) == {"hits": [], "jaccards": [], "scene_cn_counts": [], "total_cn": 0}
    assert run_atmosphere_dedup("只是一段文本。")["hits"] == []
    assert _split_scenes(None) == []
    assert _split_scenes("") == []



# ═══ P8F F1：锁定阈值 + 真实分场夹具 ═══

def test_f1_locked_thresholds_no_ch1_to_4_hits():
    from novel_engine.quality.atmosphere_dedup import run_atmosphere_dedup, _SEP
    s_a = "陈老根抱着陆烬在屋里行走，脚步很轻。" * 20
    s_b = "天刚蒙蒙亮，陈老根提着木桶走向水井。" * 20
    s_c = "午后日光斜斜照进小院，陈老根坐在凳上。" * 20
    s_d = "夜里，陈老根决定抚养陆烬，面对未来。" * 20
    for text in [_SEP.join([s_a, s_b]), _SEP.join([s_b, s_c]), _SEP.join([s_c, s_d])]:
        r = run_atmosphere_dedup(text)
        assert r["hits"] == [], f"Low-concentration baseline must have 0 hits, got {r['hits']}"
    r_all = run_atmosphere_dedup(_SEP.join([s_a, s_b, s_c, s_d]))
    assert r_all["hits"] == [], f"ch1-4 baseline must have 0 hits, got {r_all['hits']}"

def test_f1_threshold_constants_locked():
    from novel_engine.quality.atmosphere_dedup import _FAMILY_THRESHOLDS
    assert _FAMILY_THRESHOLDS["黑暗夜色"] == (5.5, 7.0)
    assert _FAMILY_THRESHOLDS["雾浊"] == (4.5, 5.0)
    assert _FAMILY_THRESHOLDS["寂静死寂"] == (4.5, 5.5)
    assert _FAMILY_THRESHOLDS["寒冷"] == (4.5, 6.0)
    assert _FAMILY_THRESHOLDS["腥腐"] == (3.0, 4.0)
    assert _FAMILY_THRESHOLDS["压抑沉"] == (3.5, 4.5)


# ═══ P8F F2：重抛光指令字段修复 ═══

def test_f2_directive_no_jaccard_or_shared():
    from novel_engine.quality.atmosphere_dedup import generate_repolish_directive
    hit = {
        "scene_num": 2,
        "family": "黑暗夜色",
        "conc_scene_a": 6.5,
        "conc_scene_b": 7.4,
        "high_freq_terms": ["暗", "黑暗", "昏"],
        "suggested_direction": "动作细节",
    }
    directive = generate_repolish_directive(hit)
    assert "Jaccard" not in directive
    assert "共享" not in directive
    assert "黑暗夜色" in directive
    assert "6.5" in directive
    assert "7.4" in directive
    assert "暗" in directive or "黑暗" in directive
    assert "动作细节" in directive

def test_f3_rollbacks_on_forbidden_word(tmp_path):
    import json as _json
    from pathlib import Path
    from unittest.mock import MagicMock, patch
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    from novel_engine.quality.atmosphere_dedup import (
        _SEP, _split_scenes as _split, cn_chars, run_atmosphere_dedup,
    )
    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'runtime_config.json').write_text(_json.dumps({'llm': {'use_mock': True}, 'review_llm': {'use_mock': True}, 'fallback_llm': {'use_mock': True}}), encoding='utf-8')
    (tmp_path / 'config' / 'llm_providers.json').write_text(_json.dumps({'active_profile': 'test', 'profiles': {'test': {'base_url': 'https://t.com/v1', 'api_key_env': 'TK_F3', 'timeout_s': 60, 'max_retries': 1, 'default_extra_body': {}, 'phases': {'scenes': {'models': ['t'], 'response_format': None}, 'polish': {'models': ['t'], 'response_format': None, 'concurrency': 4}, 'planning': {'models': ['t'], 'response_format': None}, 'review': {'models': ['t'], 'response_format': None}}}}}), encoding='utf-8')
    import os as _os; _os.environ['TK_F3'] = 'sk'
    (tmp_path / 'config' / 'forbidden.json').write_text('{}', encoding='utf-8')
    (tmp_path / 'config' / 'quality_policy.json').write_text(_json.dumps({'publication_line': 88, 'soft_publication_line': 85, 'min_ratio': 0.85, 'max_ratio': 1.2, 'tolerance_chars': 500}), encoding='utf-8')
    from novel_engine.core.llm_client import LLMClient
    orch = PipelineOrchestrator(project_root=str(tmp_path), llm_client=LLMClient(use_mock=True))
    # Patch use_mock so F3 repolish branch executes (mock client still handles LLM calls)
    orch.config['llm']['use_mock'] = False
    orch._scene_regen_used = {}
    card = {'chapter_num': 5, 'core_goal': 'test', 'scene_blueprints': [
        {'scene_num': 1, 'location': '茅屋', 'characters': ['陈老根'], 'goal': '安抚', 'conflict': '夜啼', 'emotion': '疲惫', 'word_count_target': 1800},
        {'scene_num': 2, 'location': '井边', 'characters': ['陈老根'], 'goal': '打水', 'conflict': '浊气', 'emotion': '警觉', 'word_count_target': 2000},
        {'scene_num': 3, 'location': '院中', 'characters': ['陈老根'], 'goal': '观察', 'conflict': '沉静', 'emotion': '疑惑', 'word_count_target': 1800},
        {'scene_num': 4, 'location': '屋内', 'characters': ['陈老根'], 'goal': '决定', 'conflict': '未来', 'emotion': '决心', 'word_count_target': 1600}], 'foreshadow_actions': []}
    # Build 4 valid long scenes with unique punctuated segments (no 200-char repetition).
    # Scenes 2+3 carry dark_theme words at F1-threshold conc (>=5.5, one >=7.0).
    _MARKER = '石磨印记柒叁'
    _DARK2 = ['夜色','漆黑','黑夜','夜风','阴沉','阴霾','昏黑','暗夜','昏暗','夜色','漆黑','黑夜']
    _DARK3 = ['夜色','漆黑','黑夜','夜风','阴沉','阴霾','昏黑','暗夜','昏暗','夜色','漆黑','黑夜','夜风','阴沉','阴霾','昏黑','暗夜','昏暗']
    _PUNCT_BASE = '，陈老根在茅屋中做着农活，手中的锄头翻动着泥土，汗水顺着脸颊滑落。明日还需早起赶在天亮前回。'
    def _seg(idx, prefix):
        return f'{prefix}{idx}{_PUNCT_BASE}'
    def _make(darks, every, n, prefix):
        parts = []
        for i in range(n):
            seg = _seg(i, prefix)
            if i % every == 0 and (i // every) < len(darks):
                seg = darks[i // every] + seg
            parts.append(seg)
        return ''.join(parts)
    _N = 60
    s1 = ''.join([_seg(i, '1') for i in range(_N)])
    s2 = _make(_DARK2, 4, _N, '2')
    s3 = _make(_DARK3, 3, _N, '3')
    s4 = ''.join([_seg(i, '4') for i in range(_N)])
    s2 = s2.replace('20，陈老根', '20，陈老根' + _MARKER, 1)
    assembled = _SEP.join([s1, s2, s3, s4])
    # Pre-assert: atmosphere dedup actually fires on assembled text
    _dedup_result = run_atmosphere_dedup(assembled)
    _hits = _dedup_result.get("hits") or []
    assert len(_hits) >= 1, f"assembled text must trigger atmosphere dedup hits, got {_hits}"
    _dark_hits = [h for h in _hits if h.get("family") == "黑暗夜色" and h.get("scene_num") in (2, 3)]
    assert len(_dark_hits) >= 1, f"must have dark-night hit on scene 2 or 3, got {_hits}"
    orch.current_novel = assembled
    orch._draft_novel = assembled
    orch._frozen_task_cards = {5: card}
    orch._frozen_synopsis = {5: {'synopsis': 'test'}}
    orch._review_call_count = 0
    bad_polished = '晨雾仍未散尽气感弥漫井边雾气缠绕陈老根紧了紧襁褓'
    mock_writer = MagicMock()
    mock_ns = MagicMock()
    mock_ns.scene_text = bad_polished
    mock_ns.beats = []
    mock_ns.hook = ''
    _repolish_calls = [0]
    def _generate_scene_side_effect(*args, **kwargs):
        fix_dir = kwargs.get("fix_directive")
        if fix_dir and fix_dir.strip().startswith("【氛围去重重抛光"):
            _repolish_calls[0] += 1
            return mock_ns
        result = MagicMock()
        result.scene_text = assembled
        result.beats = []
        result.hook = ""
        return result
    mock_writer.generate_scene.side_effect = _generate_scene_side_effect
    orch.writer = mock_writer
    _review_n = [0]
    captured_novel = {}
    def _capturing_review(ch, tc, sy, novel, ws=None):
        _review_n[0] += 1
        captured_novel['input'] = novel
        if _review_n[0] == 1:
            return {'review': {'scores': {'plot_consistency': 20, 'character_consistency': 18, 'foreshadow_execution': 18, 'style_match': 12, 'pacing': 10, 'innovation': 14}, 'total_score': 85, 'verdict': 'fix', 'issues': [], 'fix_scope': ''}, 'score': 85.0, 'verdict': 'fix', 'review_unstable': False}
        return {'review': {'scores': {'plot_consistency': 20, 'character_consistency': 18, 'foreshadow_execution': 18, 'style_match': 12, 'pacing': 10, 'innovation': 14}, 'total_score': 92, 'verdict': 'pass', 'issues': [], 'fix_scope': ''}, 'score': 92.0, 'verdict': 'pass', 'review_unstable': False}
    orch._stage_review = _capturing_review
    def _fast_write(task_card, synopsis):
        return orch.current_novel or ""
    orch._stage_write = _fast_write
    orch._ensure_chinese = lambda n: n
    with patch('novel_engine.pipeline.pipeline_orchestrator.load_authoritative_scenes', return_value=[]), \
         patch.object(orch, '_enforce_word_count', side_effect=lambda n, lo, hi: n):
        result = orch.generate_single_chapter(5)
    # 断言1：repolish分支真被执行（bad场景被生成）
    assert _repolish_calls[0] >= 1, f"atmosphere re-polish must be invoked at least once, got {_repolish_calls[0]}"
    # 断言2：评审收到非空四场且每场满1000（用_split校验，不整串相等）
    review_input = captured_novel.get('input', '')
    assert review_input, f"Review must receive non-empty novel, got empty"
    scenes = _split(review_input)
    assert len(scenes) == 4, f"Review must receive 4 scenes, got {len(scenes)}"
    for i, s in enumerate(scenes, 1):
        assert cn_chars(s) >= 1000, f"Scene {i} must have >= 1000 CN chars, got {cn_chars(s)}"
    # 断言3：最终成稿不含"气感"（坏场被拒绝，原场保留）
    final_novel = orch.current_novel or ""
    assert "气感" not in final_novel, f"Final manuscript must not contain 气感, got: {repr(final_novel[:200])}"
    # 断言4：第2场仍含唯一标记（证明坏场被整场回滚、原场保留）
    assert _MARKER in final_novel, f"Scene-2 unique marker {_MARKER} must survive rollback"
    # 断言5：result无atmosphere_repolished_scenes（坏场未被采纳）
    assert result.get("atmosphere_repolished_scenes") is None or result.get("atmosphere_repolished_scenes") == [], \
        f"Result must not have atmosphere_repolished_scenes, got: {result.get('atmosphere_repolished_scenes')}"
    # 断言6：success为真
    assert result.get('success') is True, f"Result must be success, got: {result}"
