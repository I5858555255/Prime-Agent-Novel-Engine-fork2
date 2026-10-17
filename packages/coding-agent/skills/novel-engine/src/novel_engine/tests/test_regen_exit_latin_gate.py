# -*- coding: utf-8 -*-
"""修复D 回归：统一重生出口 _regen_scene_for_id 对拉丁泄漏的拦截。

ch325 第 4 次失败归因：latin_leak_gate 只对主流程原始场景跑一次，density/punct/
continuity 等后续通道的重生产物不再复检英文，859 处英文绕过 latin 门直进
final gate BLOCK。修复：所有 scene regen 都经 _regen_scene_for_id 出口，产物含
拉丁泄漏（整句=语言跑飞、残片=混入）→ 带语言 directive 重试 1 次；重试成功返回
干净场景，仍泄漏记录 hard（final gate 兜底隔离），不改变异常控制流。
hermetic：stub orchestrator + fake writer，零 LLM。
"""
from __future__ import annotations

from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
from novel_engine.agents.scene_schema import SceneOutput

_ENGLISH_WATERFALL = ("The purple fog pressed against the outside of his body "
                      "blocking all light from the broken wall")
_CHINESE_PROSE = ("陈老根顿了顿枣木棍，笃的一声，三名村民退后半步，"
                  "谁也没敢再往前凑。")
_FRAGMENT = "他暗暗下了决心，he decision 不对外透露。"


class _FakeWriter:
    def __init__(self, first_text: str, retry_text: str | None = None):
        self.calls: list[dict] = []
        self.first_text = first_text
        self.retry_text = retry_text

    def generate_scene(self, task_card, bp, synopsis_text, negative_examples=None,
                       all_blueprints=None, temperature_override=None,
                       fix_directive=None, frequency_penalty=None, presence_penalty=None):
        self.calls.append({
            "fix_directive": fix_directive,
            "negative_examples": negative_examples,
        })
        if fix_directive is not None and self.retry_text is not None:
            text = self.retry_text
        else:
            text = self.first_text
        return SceneOutput(scene_id=int(bp.get("scene_num", 1) or 1),
                           scene_text=text, hook="", beats=[])


class _StubOrchestrator:
    def __init__(self, writer):
        self.writer = writer
        self._current_synopsis_text = ""
        self._last_deterministic_issues: list[str] = []


def _run_regen(first_text: str, retry_text: str | None = None):
    stub = _StubOrchestrator(_FakeWriter(first_text, retry_text))
    fn = PipelineOrchestrator._regen_scene_for_id.__get__(stub)
    task_card = {"chapter_num": 325, "scene_blueprints": [
        {"scene_num": 1, "goal": "守卫村口", "location": "村口", "characters": [], "beats": []}]}
    out = fn(task_card, 1, [])
    return stub, out


def test_exit_retries_full_english_once_and_returns_clean():
    """首次整句英文 -> 语言跑飞 directive 重试 -> 干净中文返回，无 hard。"""
    stub, out = _run_regen(_ENGLISH_WATERFALL, retry_text=_CHINESE_PROSE)
    assert stub.writer.calls, "regen must be invoked"
    assert len(stub.writer.calls) == 2, "full-english regen must retry once"
    d = stub.writer.calls[1]["fix_directive"] or ""
    assert "语言跑飞" in d, "full-english retry must use language-runaway directive"
    assert "purple" in d, "directive must carry the real english sample"
    assert (stub.writer.calls[1]["negative_examples"] or []) and \
        any("严禁" in (ne or "") for ne in stub.writer.calls[1]["negative_examples"]), \
        "negative examples must carry the forbidden english sample"
    assert "purple" not in (out.scene_text or "").lower()
    assert stub._last_deterministic_issues == [], "clean retry must not record hard"


def test_exit_records_hard_when_english_persists():
    """首次+重试都整句英文 -> 记 hard，返回末次文本（final gate 兜底 BLOCK）。"""
    stub, out = _run_regen(_ENGLISH_WATERFALL, retry_text=_ENGLISH_WATERFALL)
    assert len(stub.writer.calls) == 2
    assert any("非中文" in h and "325" not in h for h in stub._last_deterministic_issues), \
        f"persistent english must record hard, got {stub._last_deterministic_issues}"
    assert "purple" in (out.scene_text or "").lower()


def test_exit_fragment_uses_fragment_directive():
    """零星残片 -> 常规 directive 重试（不含语言跑飞字样）。"""
    stub, out = _run_regen(_FRAGMENT, retry_text=_CHINESE_PROSE)
    assert len(stub.writer.calls) == 2
    d = stub.writer.calls[1]["fix_directive"] or ""
    assert "语言跑飞" not in d, "fragment must not be treated as language runaway"
    assert "英文字母/英文单词" in d
    assert "decision" not in (out.scene_text or "")
    assert stub._last_deterministic_issues == []


def test_exit_clean_scene_returns_without_retry():
    """纯中文产物 -> 直接返回，仅一次调用，无 hard 无重试。"""
    stub, out = _run_regen(_CHINESE_PROSE)
    assert len(stub.writer.calls) == 1
    assert out.scene_text == _CHINESE_PROSE
    assert stub._last_deterministic_issues == []
