# -*- coding: utf-8 -*-
"""守卫测试：确保 Auto-fix failed 日志中不混入内部异常（防吞错退化）。

背景：pipeline_orchestrator.py L979 的唯一一处 logger.warning(f"Auto-fix failed ({_pe}); ...")
位于 fix-loop 外层 except Exception as _pe 兜底块。任何内部代码 bug（UnboundLocalError、
TypeError、NameError 等）被捕获后都只表现为这条 warning，测试的 pass/fail 数字完全看不到——
上一轮 final_det UnboundLocalError 就是靠人工读日志才抓到的。

本文件目的：
1. 端到端守卫：跑一次正常 fix-loop 流程，确认日志中不出现"内部异常被吞"的 warning。
2. 反例单测：直接验证异常检测逻辑能正确区分"正常降级日志"与"内部异常日志"，
   保证守卫不是永远通过的空壳。
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

_INTERNAL_EXCEPTION_PATTERNS = [
    r"UnboundLocalError",
    r"cannot access local variable",
    r"TypeError",
    r"NameError",
    r"AttributeError",
    r"IndexError",
    r"KeyError",
    r"missing \d+ required positional argument",
    r"list indices must be integers or slices",
    r"string indices must be integers",
]

_SEP = chr(10) + chr(10) + chr(9832) + chr(10) + chr(10)


# ============================================================================
# 守卫逻辑（供端到端测试和单测复用）
# ============================================================================

def find_internal_auto_fix_failures(log_lines: list[str]) -> list[str]:
    """从日志行列表中筛选出"Auto-fix failed"且原因命中内部异常模式的行。

    参数:
        log_lines: 原始日志行列表（每行一条字符串）。

    返回:
        命中的日志行列表。若为空，说明没有内部异常被静默吞掉。
    """
    matched = []
    for line in log_lines:
        if "Auto-fix failed" not in line:
            continue
        # 提取括号内的原因文本（支持 _pe 直接打印的格式 "Auto-fix failed (ExceptionType: message)"）
        reason_match = re.search(r"Auto-fix failed \((.+?)\)", line)
        if not reason_match:
            continue
        reason_text = reason_match.group(1)
        for pattern in _INTERNAL_EXCEPTION_PATTERNS:
            if re.search(pattern, reason_text):
                matched.append(line)
                break
    return matched


# ============================================================================
# 反例单测：验证守卫逻辑本身有效（不依赖完整 orchestrator）
# ============================================================================

class TestGuardLogic:
    """纯逻辑测试：确保 find_internal_auto_fix_failures 不误判、不漏判。"""

    def test_clean_score_log_not_flagged(self):
        """正常降级日志（分数信息）不应被判定为内部异常。"""
        lines = [
            'Auto-fix failed (score=66.7); keeping pre-fix (score=66.7)',
        ]
        assert find_internal_auto_fix_failures(lines) == []

    def test_non_exception_warning_not_flagged(self):
        """非异常类 warning 不应被误报。"""
        lines = [
            'Auto-fix failed (ValueError: invalid literal for int()); keeping pre-fix',
        ]
        # ValueError 不在内部异常模式列表中（它是业务校验错误，非代码 bug）
        result = find_internal_auto_fix_failures(lines)
        assert result == []

    def test_unbound_local_error_detected(self):
        """UnboundLocalError 应被捕获（复刻 final_det 历史 bug）。"""
        lines = [
            "Auto-fix failed (UnboundLocalError: cannot access local variable 'final_det' "
            "where it is not associated with a value); keeping pre-fix (score=82.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1
        assert "UnboundLocalError" in result[0]
        assert "final_det" in result[0]

    def test_type_error_list_indices_detected(self):
        """TypeError list indices 应被捕获（复刻 t1 历史 bug）。"""
        lines = [
            "Auto-fix failed (TypeError: list indices must be integers or slices, not str); "
            "keeping pre-fix (score=85.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1
        assert "list indices must be integers or slices" in result[0]

    def test_name_error_detected(self):
        """NameError 应被捕获。"""
        lines = [
            "Auto-fix failed (NameError: name 'some_undefined_var' is not defined); "
            "keeping pre-fix (score=80.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1

    def test_attribute_error_detected(self):
        """AttributeError 应被捕获。"""
        lines = [
            "Auto-fix failed (AttributeError: 'NoneType' object has no attribute 'get'); "
            "keeping pre-fix (score=80.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1

    def test_index_error_detected(self):
        """IndexError 应被捕获。"""
        lines = [
            "Auto-fix failed (IndexError: list index out of range); keeping pre-fix (score=80.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1

    def test_key_error_detected(self):
        """KeyError 应被捕获。"""
        lines = [
            "Auto-fix failed (KeyError: 'nonexistent_key'); keeping pre-fix (score=80.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1

    def test_missing_required_argument_detected(self):
        """缺少必需位置参数应被捕获。"""
        lines = [
            "Auto-fix failed (TypeError: missing 1 required positional argument: 'task_card'); "
            "keeping pre-fix (score=80.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1

    def test_string_indices_error_detected(self):
        """string indices must be integers 应被捕获。"""
        lines = [
            "Auto-fix failed (TypeError: string indices must be integers, not str); "
            "keeping pre-fix (score=80.0)",
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1

    def test_multiple_lines_only_bad_one_flagged(self):
        """混合日志：正常降级行不干扰，仅异常行被标记。"""
        lines = [
            'Auto-fix failed (score=66.7); keeping pre-fix (score=66.7)',
            "Auto-fix failed (TypeError: list indices must be integers or slices, not str); "
            "keeping pre-fix (score=85.0)",
            'Auto-fix failed (score=70.0); keeping pre-fix (score=70.0)',
        ]
        result = find_internal_auto_fix_failures(lines)
        assert len(result) == 1
        assert "TypeError" in result[0]

    def test_empty_input_returns_empty(self):
        """空输入返回空列表。"""
        assert find_internal_auto_fix_failures([]) == []

    def test_no_auto_fix_lines_returns_empty(self):
        """不含 Auto-fix 的行全部忽略。"""
        lines = [
            "Normal info log",
            "WARNING: some other warning",
            "ERROR: unrelated error",
        ]
        assert find_internal_auto_fix_failures(lines) == []


# ============================================================================
# 端到端守卫测试：跑真实 fix-loop，用 caplog 捕获日志
# ============================================================================

def _make_orch_e2e(tmp_path):
    """创建带 mock LLM 的 orchestrator（复用 round19 模式）。"""
    (tmp_path / 'config').mkdir(parents=True, exist_ok=True)
    (tmp_path / 'config' / 'runtime_config.json').write_text(
        json.dumps({'llm': {'use_mock': True}, 'review_llm': {'use_mock': True},
                     'fallback_llm': {'use_mock': True}, 'chapter_target_chars': 7500,
                     'pipeline': {'merge_synopsis_into_directing': True}}),
        encoding='utf-8')
    (tmp_path / 'config' / 'llm_providers.json').write_text(
        json.dumps({'active_profile': 'test', 'profiles': {'test': {
            'base_url': 'https://test.example.com/v1', 'api_key_env': 'TEST_KEY_E2E',
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
    os.environ['TEST_KEY_E2E'] = 'sk-test-e2e'
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}
    orch._frozen_task_cards = {}
    orch._literary_pass_used = set()
    return orch


def _build_clean_text():
    """构建无问题的干净文本（足够长以越过截断门，无重复块）。"""
    pad1 = ''.join(f'陈老根抱着陆烬入睡第{k}次。夜色沉沉村中寂静。陆烬呼吸平稳。' for k in range(200))
    pad2 = ''.join(f'次日清晨陆烬精神饱满第{m}次。对周遭一切毫无不适。陈老根甚感欣慰。' for m in range(200, 400))
    return (
        '陈老根抱着陆烬入睡。夜色沉沉，村中寂静无声。陆烬呼吸平稳，毫无异常。\n\n'
        + pad1 + '\n\n'
        '次日清晨，陆烬精神饱满，对周遭一切毫无不适。陈老根甚感欣慰。\n\n'
        + pad2
    )


def test_e2e_no_internal_exception_swallowed(caplog, tmp_path):
    """端到端守卫：跑正常 fix-loop 流程，确认无内部异常被 except 吞掉。

    若 fix-loop 内部出现 UnboundLocalError/TypeError 等代码 bug，
    会被外层 except Exception as _pe 捕获并记录 warning，
    本测试通过检查 caplog 来拦截此类静默退化。
    """
    import logging
    caplog.set_level(logging.WARNING, logger="novel_engine")

    orch = _make_orch_e2e(tmp_path)
    task_card = {
        'chapter_num': 5,
        'scene_blueprints': [{'scene_num': 1}, {'scene_num': 2}],
        'chapter_events': [],
        'synopsis': '陈老根发现陆烬对浊气异常敏感，首次体质伏笔，决定抚养。',
    }
    text = _build_clean_text()
    orch._frozen_task_cards = {5: task_card}

    def _fast_write(task_card, synopsis):
        text1 = text + "\n[场景1补充内容以确保长度足够。陈老根心中感慨万千。]" * 20
        text2 = text + "\n[场景2补充内容以确保长度足够。夜色沉沉万籁俱寂。]" * 20
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

    with patch.object(orch, '_patch_weak_scenes', return_value=None):
        result = orch.generate_single_chapter(5)

    # 收集所有 novel_engine logger 的 WARNING 级别日志
    warning_lines = [r.message for r in caplog.records if r.levelname == "WARNING"]

    # 守卫检查：不应有内部异常被 Auto-fix failed 吞掉
    flagged = find_internal_auto_fix_failures(warning_lines)
    if flagged:
        pytest.fail(
            f"Auto-fix failed 日志暴露内部异常（被 except 吞掉），请检查代码 bug：\n"
            + "\n".join(f"  - {line}" for line in flagged)
        )

    # 附加：正常流程下不应出现任何 Auto-fix failed 日志（无异常发生）
    auto_fix_lines = [l for l in warning_lines if "Auto-fix failed" in l]
    assert auto_fix_lines == [], (
        f"正常章节不应触发 Auto-fix failed 日志，但捕获到：\n"
        + "\n".join(f"  - {line}" for line in auto_fix_lines)
    )
    # 流程应正常完成
    assert result.get('success') is True, f"章节生成应成功，实际: {result}"
