# -*- coding: utf-8 -*-
"""修复G1 回归：A 类整章重写 _low_score_rewrite 出口的拉丁复检。

ch378 归因：修复D 只覆盖 scene regen 出口 _regen_scene_for_id；A 类整章重写
（generate_full_chapter 直出）产物漏跑 latin 门，带英文（如 'hook'）进入后续
通道并最终被 final gate BLOCK。修复：_low_score_rewrite 在 _ensure_chinese 后
复用 latin_leak_gate 检测，整句英文或拉丁泄漏 → 该重写产物不采纳（return False），
不进入 finalize/review。
hermetic：stub orchestrator + fake writer，零 LLM。
"""
from __future__ import annotations

from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

_ENGLISH = ("The purple fog pressed against the outside of his body "
            "blocking all light from the broken wall")
_FRAGMENT = "他暗暗下了决心，hook：章末钩子必须承接前文。"
_CHINESE = "陈老根顿了顿枣木棍，笃的一声，三名村民退后半步，谁也没敢再往前凑。"


class _FakeWriter:
    def __init__(self, text: str):
        self.text = text

    def generate_full_chapter(self, task_card, synopsis, _x, temperature_override=None):
        return self.text

    def polish_chapter(self, novel, task_card, llm_client=None):
        return novel


class _StubOrchestrator:
    def __init__(self, writer):
        self.writer = writer
        self._policy = {"low_score_rewrite": {"floor": 60.0}, "fix_threshold": 60}
        self.polish_router = None
        self.finalized = False
        self.reviewed = False
        self._low_score_rewritten_score = None

    def _rollback_world_to(self, _n):
        return None

    def _journal_validated_scenes(self, _n):
        return None

    def _ensure_chinese(self, novel):
        # stub：假定已有中文硬门处理过；被测点是其后的 latin 复检
        return novel

    def _novel_string(self, novel):
        return novel if isinstance(novel, str) else str(novel)

    def _finalize_current_novel(self, _n):
        self.finalized = True
        return "finalized"

    def _stage_review(self, _n, *_a, **_k):
        self.reviewed = True
        return {"score": 99.0, "verdict": "pass"}


def _run_rewrite(text: str):
    stub = _StubOrchestrator(_FakeWriter(text))
    fn = PipelineOrchestrator._low_score_rewrite.__get__(stub)
    return stub, fn(325, {"chapter_num": 325}, {}, {})


def test_lsr_rejects_full_english_output():
    """整句英文产物 -> 复检命中 -> return False，不进入 finalize/review。"""
    stub, ok = _run_rewrite(_ENGLISH)
    assert ok is False, "english-heavy rewrite must be discarded"
    assert stub.finalized is False, "must not finalize a latin-leaked rewrite"
    assert stub.reviewed is False, "must not review a latin-leaked rewrite"


def test_lsr_rejects_fragment_leak():
    """残片/字段词泄漏（hook）-> 同样弃稿不采纳。"""
    stub, ok = _run_rewrite(_FRAGMENT)
    assert ok is False, "fragment leak must be discarded"
    assert stub.finalized is False


def test_lsr_accepts_clean_chinese():
    """纯中文产物 -> 进入 finalize/review 正常路径。"""
    stub, ok = _run_rewrite(_CHINESE)
    assert ok is True, "clean chinese rewrite must proceed"
    assert stub.finalized is True
    assert stub.reviewed is True
