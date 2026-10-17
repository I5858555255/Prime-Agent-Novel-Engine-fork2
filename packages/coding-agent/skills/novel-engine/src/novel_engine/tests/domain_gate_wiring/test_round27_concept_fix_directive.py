# -*- coding: utf-8 -*-
"""CC round-27: _scene_issues_to_directives must not crash on concept_unlocks terms.

The method is an instance method (was @staticmethod before round-27 added
self.root / self._concept_db usage inside the [canon硬词] branch). This test
drives the exact path: a [canon硬词] diagnostic whose term belongs to a
concept_unlocks concept must produce a directive that mentions the unlock
context, without raising.
"""
import pathlib

from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _make_orchestrator():
    inst = object.__new__(PipelineOrchestrator)
    inst.root = SRC_ROOT
    inst._concept_db = None
    return inst


def test_canon_term_with_concept_context_no_crash_and_mentions_unlock():
    inst = _make_orchestrator()
    issues = ["[canon硬词] 吐纳：陆烬看着养父在草铺上吐纳，心中疑惑。"]
    out = inst._scene_issues_to_directives(issues, "scene_text", 0)
    assert out, "should produce at least one directive"
    joined = "\n".join(out)
    # 概念上下文应被附加（cultivation_system ch6 解锁）
    assert "概念" in joined or "解锁" in joined
    assert "吐纳" in joined


def test_plain_hardword_still_no_crash():
    inst = _make_orchestrator()
    issues = ["[canon硬词] 丹田：婴儿体内丹田隐隐发热。"]
    out = inst._scene_issues_to_directives(issues, "scene_text", 0)
    assert out
    assert "丹田" in "\n".join(out)


def test_non_canon_issue_types_unaffected():
    inst = _make_orchestrator()
    issues = ["high_freq_repeat: '他笑了笑' x3"]
    out = inst._scene_issues_to_directives(issues, "scene_text", 0)
    assert out
    assert "重复" in "\n".join(out)
