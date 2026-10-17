# -*- coding: utf-8 -*-
"""修复E 回归：final pre-commit gate 的 punctuation 修复先跑零 LLM 确定性断句。

ch370 归因：213/248 字符无句读超长流水段，旧逻辑直接上 LLM 仅插标点（batched/
parallel），"仅插标点且去标点逐字相同"校验对超长段连 fail 9 次，final gate
9/13 修完剩 4 段硬阻断。修复：先跑零 LLM 确定性断句（repair_text_punctuation，
对无标点段每 45 字硬边界插句号），零 LLM 全修好则完全跳过 LLM；残余段再走
LLM 仅插标点兜底。hermetic：stub orchestrator + fake polish router，零网络。
"""
from __future__ import annotations

import types

from novel_engine.pipeline import gates as gates_mod
from novel_engine.quality import punctuation_health as ph

# 200+ 字符、无任何中文标点的超长流水段（ch370 形态，无强边界词可依赖）
_RUN_ON = ("山风从破庙东墙的缺口灌进来吹得供桌上那盏油灯的火苗忽明忽暗他盯着门外"
           "那片黑黢黢的松林想起白天在镇口听到的传言说北边官道上有商队一夜之间"
           "全没了踪迹连车辙都被人刻意抹平他握紧腰间那把柴刀指节泛白却始终没有"
           "迈出庙门半步直到后半夜月亮从云缝里漏出来他才在墙角靠着梁柱慢慢合上眼")
# 正常中文段，用于陪衬
_CLEAN = "陈老根顿了顿枣木棍，笃的一声，三名村民退后半步，谁也没敢再往前凑。"
# 带强边界词的长段（零 LLM 也能靠边界词断句）
_WITH_BOUNDARY = ("他想了想于是决定不再犹豫但是刚要迈步却听见身后传来脚步声"
                   "紧接着有人低声喊了他的名字他只得停下脚步回头")


class _FakeRouter:
    def __init__(self):
        self.calls: list[list] = []
        self.concurrency = 4

    def chat_completion(self, messages, temperature=0.0, max_tokens=1000):
        self.calls.append(messages)
        return {"content": ""}


class _StubOrchestrator:
    def __init__(self, router: _FakeRouter):
        self.config = {"llm": {"use_mock": False}}
        self.polish_router = router
        self._repair_paragraphs_punct_batched = types.MethodType(
            gates_mod._repair_paragraphs_punct_batched, self)
        self._repair_paragraphs_punct_parallel = types.MethodType(
            gates_mod._repair_paragraphs_punct_parallel, self)
        self._punct_only_repair = types.MethodType(gates_mod._punct_only_repair, self)


def _run_gate(text: str):
    router = _FakeRouter()
    stub = _StubOrchestrator(router)
    fn = types.MethodType(gates_mod._run_final_precommit_gate, stub)
    out = fn(chapter_num=370, task_card={}, final_text=text)
    return router, out


def test_final_gate_zero_llm_splits_long_run_on_without_llm():
    """213+ 字符无标点流水段：零 LLM 断句全修好，不调用 LLM，不硬阻断。"""
    text = "　　" + _RUN_ON + "\n" + _CLEAN
    router, out = _run_gate(text)
    assert router.calls == [], "zero-LLM full repair must not invoke the LLM"
    assert out["hard_blocked"] is False, f"long run-on must be repaired, got {out['violations']}"
    assert out["repaired"] is True
    # 复检：修复后文本中不再存在 ≥45 字无标点 run
    residual = ph.check_text_punctuation(out["text"])
    assert residual == [], f"residual unhealthy paras: {residual}"


def test_final_gate_zero_llm_precedes_llm_fallback():
    """混合文本：零 LLM 修掉大部分，仅剩 LLM 兜底段才走 LLM；结果仍不硬阻断。"""
    # 纯中文 + 一个纯西文引号内的"残段"之外，构造只有中文标点问题的文本
    text = "　　" + _RUN_ON + "\n" + _WITH_BOUNDARY + "\n" + _CLEAN
    router, out = _run_gate(text)
    # 若零 LLM 已全修好则无 LLM 调用；即使有兜底调用，最终也必须 repaired
    assert out["hard_blocked"] is False, f"must repair to clean, got {out['violations']}"
    assert out["repaired"] is True
    residual = ph.check_text_punctuation(out["text"])
    assert residual == []


def test_final_gate_clean_text_untouched():
    """健康文本：零 LLM 与 LLM 都不触发，原样返回。"""
    text = _CLEAN + "\n" + _CLEAN
    router, out = _run_gate(text)
    assert router.calls == []
    assert out["text"] == text
    assert out["hard_blocked"] is False
    assert out["repaired"] is False
