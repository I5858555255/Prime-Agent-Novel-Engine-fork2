# -*- coding: utf-8 -*-
"""ch295/ch66 归因回归：WRITE REPLAN（密度真空洞）必须有进程内重排通道。

背景：场景内容密度两轮无改善 → WRITE REPLAN → 旧实现直接 skip in-process retries
返回 FAILED，坏任务卡要等 runner 外部重跑整章（浪费一整轮往返）。修复：_run_with_retry
对 quality_replan 做进程内重排一次（绕过 director 缓存重新规划 + 重走 writer），
限 1 次，仍失败才返回。零 LLM：stub orchestrator 记录调用序列与 replan 语义。
"""
from __future__ import annotations

import os

from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


class _StubOrchestrator:
    """鸭子类型 stub：依次返回预设结果，记录 generate_single_chapter 调用次数。"""

    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls = 0
        self.config = {}
        self._attempt_index = 0

    def generate_single_chapter(self, chapter_num: int) -> dict:
        r = dict(self.responses[self.calls])
        r["_call"] = self.calls
        self.calls += 1
        return r

    def _record_slo_failure(self, chapter_num: int, reason: str) -> None:
        self.slo_failures = getattr(self, "slo_failures", []) + [(chapter_num, reason)]


def test_replan_triggers_in_process_second_pass():
    """首次 WRITE REPLAN → 进程内重排一次（第二次生成），成功则返回成功结果。"""
    stub = _StubOrchestrator([
        {"quality_replan": True, "replan_scene_ids": [3], "replan_reason": "scene_near_empty_cluster"},
        {"success": True, "score": 76.0},
    ])
    os.environ.pop("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER", None)
    result = PipelineOrchestrator._run_with_retry(stub, 295)
    assert stub.calls == 2, "replan must trigger exactly one in-process second pass"
    assert result.get("success") is True
    assert os.environ.get("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER") == "295", \
        "replan must bypass director cache for the chapter"


def test_replan_still_failing_returns_and_limits_to_one():
    """重排后仍 quality_replan → 直接返回，不继续烧 retry 配额（限 1 次）。"""
    stub = _StubOrchestrator([
        {"quality_replan": True, "replan_scene_ids": [2]},
        {"quality_replan": True, "replan_scene_ids": [2]},
        {"success": True, "score": 99.0},  # 若继续重试会成功——但限 1 次不得走到
    ])
    os.environ.pop("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER", None)
    result = PipelineOrchestrator._run_with_retry(stub, 66)
    assert stub.calls == 2, "second replan must return immediately (limit 1)"
    assert result.get("quality_replan") is True


def test_replan_final_gate_after_replan_returns():
    """重排后撞终检硬阻断 → 返回 gap 结果（不继续重试）。"""
    stub = _StubOrchestrator([
        {"quality_replan": True, "replan_scene_ids": [1]},
        {"final_gate_blocked": True, "score": 64.1},
    ])
    os.environ.pop("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER", None)
    result = PipelineOrchestrator._run_with_retry(stub, 325)
    assert stub.calls == 2
    assert result.get("final_gate_blocked") is True


def test_no_replan_keeps_single_pass():
    """无质量重排：单次成功即返回（回归，不引入额外调用）。"""
    stub = _StubOrchestrator([{"success": True, "score": 80.4}])
    os.environ.pop("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER", None)
    result = PipelineOrchestrator._run_with_retry(stub, 326)
    assert stub.calls == 1
    assert result.get("success") is True


class _StubOrchestratorRaiseOnSecond:
    """首次 WRITE REPLAN、第二次生成抛异常：验证 replan 异常出口的 result 结构。

    对应 run_496 ch370 收尾 KeyError 根因：in-process replan 的第二次
    generate_single_chapter 抛异常时，旧代码构造 {"success": False, "errors": [...]}
    缺 "chapter" key，导致 run_volume SUMMARY 后逐条明细打印 r["chapter"] 崩溃。
    """

    def __init__(self):
        self.calls = 0
        self.config = {}
        self._attempt_index = 0

    def generate_single_chapter(self, chapter_num: int) -> dict:
        self.calls += 1
        if self.calls == 1:
            return {"quality_replan": True, "replan_scene_ids": [4],
                    "replan_reason": "density_block"}
        raise RuntimeError("'str' object has no attribute 'get'")

    def _record_slo_failure(self, chapter_num: int, reason: str) -> None:
        self.slo_failures = getattr(self, "slo_failures", []) + [(chapter_num, reason)]


def test_replan_exception_result_keeps_chapter_key():
    """replan 二次生成抛异常 → 返回 result 必须含 "chapter"（run_volume 明细打印依赖）。"""
    stub = _StubOrchestratorRaiseOnSecond()
    os.environ.pop("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER", None)
    result = PipelineOrchestrator._run_with_retry(stub, 370)
    assert stub.calls == 2, "replan second pass raised -> must return (limit 1)"
    assert result.get("chapter") == 370, "replan exception result must carry chapter key"
    assert result.get("success") is False
    assert any(str(e).startswith("REPLAN:") for e in result.get("errors", []))
