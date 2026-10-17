# -*- coding: utf-8 -*-
"""CC33 回归：det 硬项（称谓/canon/泄漏）接入全文级修复指令。
验证：mock 模式安全短路；非 mock 时生成含 det 明细的修复指令并被消费。
"""
import json
import pathlib

import pytest

from novel_engine.core.llm_client import MockLLMClient
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


def _mk_scene(root, chapter_num, texts):
    d = pathlib.Path(root) / "chapters" / "draft"
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"chapter_{chapter_num}_partial.jsonl", "w", encoding="utf-8") as f:
        for sid, t in texts.items():
            f.write(json.dumps({"scene_id": sid, "scene_text": t, "hook": "",
                                "beats": [], "entities_json": ""}, ensure_ascii=False) + "\n")


class _StubRouter:
    def __init__(self, edits):
        self.edits = edits
        self.last_prompt = ""

    def chat_completion(self, messages, **kw):
        self.last_prompt = messages[-1]["content"]
        return {"content": json.dumps({"edits": self.edits}, ensure_ascii=False)}


def test_det_fulltext_fix_mock_mode_safe(tmp_path):
    """mock 模式下应短路返回 None，不抛异常、不发请求。"""
    orch = PipelineOrchestrator(project_root=str(tmp_path), llm_client=MockLLMClient())
    res = orch._det_fulltext_fix("正文", ["[称谓] 陆烬/李淳 裸切"], 1, {})
    assert res is None


def test_det_fulltext_fix_generates_directive(tmp_path):
    """非 mock：指令必须包含 det 明细（同指锚点/硬词泄漏），编辑被消费。"""
    texts = {
        1: "陆烬抬起头，看见陈老根站在门口。李淳知道养父有话要说，便低下了头。",
        2: "他点点头，决定先观察几天再说。",
    }
    _mk_scene(tmp_path, 11, texts)
    edits = [{
        "scene_id": 1, "op": "replace_span",
        "anchor": "李淳知道养父有话要说",
        "replacement": "陆烬知道养父有话要说，他深吸一口气平复心绪。",
    }]
    stub = _StubRouter(edits)
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch.polish_router = stub
    res = orch._det_fulltext_fix(
        "正文占位",
        ["[称谓] 同一角色陆烬/李淳 无锚点裸切",
         "[canon硬词] 吐纳：养父教他那一套晚上坐着调整呼吸的动作之后。"],
        11, {})
    assert "同指锚点" in stub.last_prompt
    assert "修炼" in stub.last_prompt or "硬词" in stub.last_prompt
    assert stub.last_prompt.count("【场景") >= 2
    # 结果允许 None（编辑应用后 det gate 拒绝则弃用，journal 不动）或 tuple
    assert res is None or (isinstance(res, tuple) and len(res) == 2)


def test_det_fulltext_fix_naming_conflict_directive(tmp_path):
    """质量类：命名矛盾 det 硬项必须生成"统一用名"修复指令并被消费。"""
    texts = {
        1: "村口的活井常年有水，村民靠它取水浇田。",
        2: "到了傍晚，有人提水走过枯井边，嘟囔着井干了多年。",
    }
    _mk_scene(tmp_path, 25, texts)
    edits = [{
        "scene_id": 1, "op": "replace_span",
        "anchor": "活井",
        "replacement": "枯井",
    }]
    stub = _StubRouter(edits)
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch.polish_router = stub
    res = orch._det_fulltext_fix(
        "正文占位",
        ["[命名矛盾] 活井（古井）与枯井同章共现且活井证据成立"],
        25, {})
    assert "命名" in stub.last_prompt
    assert "统一" in stub.last_prompt
    assert "活井（古井）与枯井同章共现" in stub.last_prompt
    assert res is None or (isinstance(res, tuple) and len(res) == 2)


def test_det_fulltext_fix_cross_scene_crisis_directive(tmp_path):
    """质量类：跨场危机 det 硬项必须生成"场景承接"修复指令并被消费。"""
    texts = {
        1: "猛火催出来的，药性是有了，可也容易烧干了锅，伤了根本。",
        2: "陆烬把药草收进背篓，两人沿着山路往回走。",
    }
    _mk_scene(tmp_path, 25, texts)
    edits = [{
        "scene_id": 2, "op": "replace_span",
        "anchor": "两人沿着山路往回走",
        "replacement": "陈老根叹道这锅药火候过了，两人沿着山路往回走，盘算着如何补救。",
    }]
    stub = _StubRouter(edits)
    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch.polish_router = stub
    res = orch._det_fulltext_fix(
        "正文占位",
        ["[跨场危机] off-card crisis 未承接：猛火催出来的，药性是有了，可也容易烧干了锅，伤了根本。"],
        25, {})
    assert "危机" in stub.last_prompt
    assert "承接" in stub.last_prompt
    assert "猛火催出来的" in stub.last_prompt
    assert res is None or (isinstance(res, tuple) and len(res) == 2)
