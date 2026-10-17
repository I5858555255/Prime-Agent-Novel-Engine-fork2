# -*- coding: utf-8 -*-
"""A类机制（2026-10-13）：review 极低分（<fix_threshold）自动升级整章重写。

覆盖三条路径：
1. verdict="fail"（score<floor）→ 路由进 fix 分支（不再直接挂人工）；
2. fix loop 内 patch/literary 无改善且分数低于 floor → 低分整章重写升级；
3. _low_score_rewrite 方法本体：回滚世界状态+整章重写+重评审，达标（>=floor）
   即返回 True，把稿子拉回"可修复"区间。

回归样例：ch292(50.5)/295(54.2)/299(34.2)/304(59.9) 曾因无此路径全部 NEEDS HUMAN。
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import json
import os

from novel_engine.pipeline.gates import BestCandidateState

_SEP = "\n\n※\n\n"


def _make_task_card():
    return {
        "chapter_num": 4,
        "core_goal": "陈老根抚养陆烬，村人疏远；井边浊气初现",
        "conflicts": {"internal": "接纳 vs 避讳", "external": "村民排斥"},
        "emotion_curve": {"start": "压抑", "middle": "异样", "climax": "察觉", "end": "钩子"},
        "scene_blueprints": [
            {"scene_num": 1, "location": "茅屋", "characters": ["陈老根", "陆烬"],
             "goal": "夜啼安抚", "conflict": "物资匮乏", "emotion": "疲惫",
             "word_count_target": 1800},
            {"scene_num": 2, "location": "村口井边", "characters": ["陈老根", "陆烬", "赵老四"],
             "goal": "打水遇村民", "conflict": "刻意疏远", "emotion": "警觉",
             "word_count_target": 2000},
            {"scene_num": 3, "location": "院中", "characters": ["陈老根", "陆烬"],
             "goal": "观察陆烬", "conflict": "异常沉静", "emotion": "疑惑",
             "word_count_target": 1800},
            {"scene_num": 4, "location": "屋内", "characters": ["陈老根", "陆烬"],
             "goal": "决定抚养", "conflict": "面对未来", "emotion": "决心",
             "word_count_target": 1600},
        ],
        "foreshadow_actions": [],
        "chapter_events": [
            {"scene_num": None, "one_line_summary": "陈老根独自在家给陆烬换洗喂食"},
            {"scene_num": None, "one_line_summary": "陈老根去村口打水，偶遇赵老四等村民"},
            {"scene_num": None, "one_line_summary": "陈老根在院中观察陆烬"},
            {"scene_num": None, "one_line_summary": "陈老根在屋内看着熟睡的陆烬，决定默默抚养"},
        ],
        "chapter_hook": "远处传来禁忌区的动静",
        "end_state": {"narrative_position": "陈老根吹灯叹息", "location": "屋内",
                       "completed_actions": ["打水"], "pending_actions": ["面对禁忌区"],
                       "time_marker": "夜"},
    }


def _setup_e2e_orch(tmp_path):
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    from novel_engine.pipeline.chapter_journal import append_scene

    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "runtime_config.json").write_text(
        json.dumps({
            "llm": {"use_mock": True},
            "review_llm": {"use_mock": True},
            "fallback_llm": {"use_mock": True},
            "chapter_target_chars": 7500,
        }), encoding="utf-8")
    (tmp_path / "config" / "llm_providers.json").write_text(
        json.dumps({
            "active_profile": "test",
            "profiles": {"test": {
                "base_url": "https://test.example.com/v1",
                "api_key_env": "TEST_KEY_LSR",
                "timeout_s": 60, "max_retries": 1,
                "default_extra_body": {},
                "phases": {
                    "scenes": {"models": ["test-model"], "response_format": None},
                    "polish": {"models": ["test-model"], "response_format": None, "concurrency": 4},
                    "planning": {"models": ["test-model"], "response_format": None},
                    "review": {"models": ["test-model"], "response_format": None},
                }
            }}
        }, ensure_ascii=False), encoding="utf-8")
    os.environ["TEST_KEY_LSR"] = "sk-test-lsr"

    (tmp_path / "config" / "forbidden.json").write_text("{}", encoding="utf-8")
    (tmp_path / "config" / "quality_policy.json").write_text(
        json.dumps({"publication_line": 88, "soft_publication_line": 85,
                     "fix_threshold": 60, "min_ratio": 0.85, "max_ratio": 1.2,
                     "tolerance_chars": 500,
                     "low_score_rewrite": {"enabled": True, "max_rounds": 2, "floor": 60}}),
        encoding="utf-8")

    orch = PipelineOrchestrator(project_root=str(tmp_path))
    orch._scene_regen_used = {}

    card = _make_task_card()
    initial_journal = {
        1: "夜色如墨，陈老根抱着陆烬在屋里安抚夜啼。物资匮乏，陈老根无奈。",
        2: "天刚蒙蒙亮，薄雾还笼着村口那口水井的青石井沿。赵老四远远避开陈老根。",
        3: "午后日光斜斜照进小院，陈老根观察陆烬。婴儿异常沉静，极少哭闹。",
        4: "夜里，陈老根决定抚养陆烬，面对未来。",
    }
    for sn, txt in initial_journal.items():
        append_scene(tmp_path, 4, {"scene_id": sn, "scene_text": txt, "hook": "", "beats": []})

    orch._frozen_task_cards = {4: card}
    orch._frozen_synopsis = {4: {"synopsis": "陈老根抚养陆烬，井边浊气初现"}}

    (tmp_path / "chapters" / "novel").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "draft").mkdir(parents=True, exist_ok=True)
    (tmp_path / "chapters" / "draft" / "failed").mkdir(parents=True, exist_ok=True)

    return orch, card


def _mock_write_from_journal(orch):
    """Bypass LLM scene generation: build chapter text from journal."""
    from novel_engine.pipeline.chapter_journal import load_authoritative_scenes

    def _fast_write(task_card, synopsis):
        try:
            scenes = load_authoritative_scenes(orch.root, 4)
        except Exception:
            scenes = []
        if not scenes:
            return "默认正文。"
        parts = []
        for d in sorted(scenes, key=lambda x: int(x.get("scene_id", 0))):
            parts.append(d.get("scene_text", ""))
        return _SEP.join(parts)

    orch._stage_write = _fast_write  # type: ignore[assignment]
    orch._ensure_chinese = lambda n: n  # type: ignore[assignment]

    def _fake_det_gate(novel, task_card, **kw):
        return {"passed": True, "issues": [], "soft_issues": []}
    orch._deterministic_quality_gate = _fake_det_gate  # type: ignore[assignment]


class _SeqReview:
    """序列化 stage_review：按调用次数依次返回 (score, verdict)。"""

    def __init__(self, seq):
        self.seq = list(seq)
        self.calls = 0

    def __call__(self, chapter_num, task_card, synopsis, novel, world_state=None):
        score, verdict = self.seq[min(self.calls, len(self.seq) - 1)]
        self.calls += 1
        return {
            "review": {
                "chapter_num": chapter_num,
                "scores": {"plot_consistency": 20, "character_consistency": 18,
                           "foreshadow_execution": 18, "style_match": 12,
                           "pacing": 10, "innovation": 14},
                "total_score": score,
                "verdict": verdict,
                "issues": [],
                "fix_scope": "",
            },
            "score": float(score),
            "verdict": verdict,
            "review_unstable": False,
        }


class _WriterMock:
    """writer mock：generate_full_chapter 返回固定长文本（低分重写路径）。"""

    LONG = "晨雾笼罩古井，雾气弥漫寒意阵阵，夜风穿过雾气带来寒光。陈老根抱着陆烬回家。" * 40

    def __init__(self):
        self.full_chapter_calls = 0
        self.polish_calls = 0

    def generate_full_chapter(self, task_card, synopsis, pacing_constraints=None, **kw):
        self.full_chapter_calls += 1
        return self.LONG

    def polish_chapter(self, novel, task_card, llm_client=None):
        self.polish_calls += 1
        return novel

    def generate_scene(self, *a, **k):
        out = MagicMock()
        out.scene_text = "场景正文内容。"
        out.beats = []
        out.hook = ""
        return out

    def focused_rewrite_scene(self, *a, **k):
        return None

    def beat_split_rewrite_scene(self, *a, **k):
        return None

    def polish_scenes(self, *a, **k):
        return a[0], {}


# ═══ T1：_low_score_rewrite 方法本体 ═══

def test_t1_low_score_rewrite_pulls_back_to_fix_band(tmp_path):
    """低分重写：回滚+整章重写+重评审；新分>=floor(60) 即 True，current_novel 更新。"""
    orch, card = _setup_e2e_orch(tmp_path)
    writer = _WriterMock()
    orch.writer = writer
    _mock_write_from_journal(orch)

    seq = _SeqReview([(70.0, "fix")])
    orch._stage_review = seq  # type: ignore[assignment]

    def _identity_wc(novel, lo, hi):
        return novel
    with patch.object(orch, "_enforce_word_count", side_effect=_identity_wc):
        ok = orch._low_score_rewrite(4, card, {"synopsis": "test"}, {})

    assert ok is True, "70>=floor(60) 应返回 True"
    assert writer.full_chapter_calls == 1, "整章重写应调用 generate_full_chapter 一次"
    assert orch.current_novel == writer.LONG, "current_novel 应为重写后文本"
    assert orch._low_score_rewritten_score == 70.0


def test_t2_low_score_rewrite_stays_false_when_still_low(tmp_path):
    """重写后仍低于 floor → False（不采纳劣稿，走原终态路由）。"""
    orch, card = _setup_e2e_orch(tmp_path)
    orch.writer = _WriterMock()
    _mock_write_from_journal(orch)

    seq = _SeqReview([(34.0, "fail")])
    orch._stage_review = seq  # type: ignore[assignment]

    def _identity_wc(novel, lo, hi):
        return novel
    with patch.object(orch, "_enforce_word_count", side_effect=_identity_wc):
        ok = orch._low_score_rewrite(4, card, {"synopsis": "test"}, {})

    assert ok is False, "重写后 34<60 应返回 False"


# ═══ T3：_dispatch_remediation 低分升级 ═══

def test_t3_dispatch_escalates_low_score_to_rewrite(tmp_path):
    """patch/literary 无改善（_cc25_fixed=False）且分数低于 floor → 低分整章重写升级，
    返回 None（继续 fix loop），且 _low_score_rewrites_used 递增。"""
    orch, card = _setup_e2e_orch(tmp_path)
    writer = _WriterMock()
    orch.writer = writer
    _mock_write_from_journal(orch)

    seq = _SeqReview([(92.0, "pass")])
    orch._stage_review = seq  # type: ignore[assignment]

    best = BestCandidateState(
        best_score=50.5, best_novel="旧稿。", best_draft="旧稿。",
        best_journal_snapshot=[],
    )
    staged = {
        "review": {"issues": [], "scores": {"plot_consistency": 10}},
        "score": 50.5,
        "verdict": "fail",
    }

    def _identity_wc(novel, lo, hi):
        return novel
    with patch.object(orch, "_enforce_word_count", side_effect=_identity_wc):
        out = orch._dispatch_remediation(
            4, card, {"synopsis": "test"}, "旧稿。", "旧稿。", best, 1, card, staged, {})

    assert out[4] is None, "低分升级成功后应返回 None 让 fix loop 继续"
    assert getattr(orch, "_low_score_rewrites_used", 0) == 1, "重写计数应递增"
    assert writer.full_chapter_calls == 1, "应触发整章重写"


def test_t4_dispatch_does_not_escalate_when_score_above_floor(tmp_path):
    """分数高于 floor（如 70）→ 不触发低分重写（保持原 fix loop 行为）。"""
    orch, card = _setup_e2e_orch(tmp_path)
    orch.writer = _WriterMock()
    _mock_write_from_journal(orch)

    seq = _SeqReview([(70.0, "fix")])
    orch._stage_review = seq  # type: ignore[assignment]

    best = BestCandidateState(
        best_score=70.0, best_novel="旧稿。", best_draft="旧稿。",
        best_journal_snapshot=[],
    )
    staged = {
        "review": {"issues": [], "scores": {"plot_consistency": 10}},
        "score": 70.0,
        "verdict": "fix",
    }

    def _identity_wc(novel, lo, hi):
        return novel
    with patch.object(orch, "_enforce_word_count", side_effect=_identity_wc):
        out = orch._dispatch_remediation(
            4, card, {"synopsis": "test"}, "旧稿。", "旧稿。", best, 1, card, staged, {})

    # 70>=60 不触发低分升级：返回 rewrite_disabled（整章 rewrite 默认关闭）结束 fix loop
    assert out[4] == "rewrite_disabled", f"70 分不应触发低分重写，got stop={out[4]}"
    assert getattr(orch, "_low_score_rewrites_used", 0) == 0, "不应消耗低分重写预算"


# ═══ T5：E2E——低分章不再直接 NEEDS HUMAN，重写后达标发布 ═══

def test_t5_e2e_low_score_recovers_via_rewrite(tmp_path):
    """完整流水线：首评 34/fail → 低分重写 → 92/pass → success=True 且不挂人工。

    这正是 ch292/295/299/304 的回归场景（此前全部 NEEDS HUMAN FAILED）。
    """
    orch, card = _setup_e2e_orch(tmp_path)
    orch.writer = _WriterMock()
    _mock_write_from_journal(orch)

    # 序列：初始评审 34/fail；fix loop 第1轮 _decide 50/fail（patch 无改善）；
    # 低分重写内部评审 92/pass；fix loop 第2轮 _decide 92/pass → 发布。
    seq = _SeqReview([(34.0, "fail"), (50.0, "fail"), (92.0, "pass"), (92.0, "pass")])
    orch._stage_review = seq  # type: ignore[assignment]

    def _identity_wc(novel, lo, hi):
        return novel
    with patch.object(orch, "_enforce_word_count", side_effect=_identity_wc):
        result = orch.generate_single_chapter(4)

    assert result.get("success") is True, f"低分重写后应成功，got: {result}"
    assert seq.calls >= 3, f"应至少经历 3 次评审（含低分重写后复评），got {seq.calls}"
    assert orch.writer.full_chapter_calls >= 1, "低分重写应触发整章重新生成"


def test_t6_e2e_disabled_keeps_legacy_fail_path(tmp_path):
    """low_score_rewrite.enabled=false → verdict=fail 保持原行为（不进修复路径）。"""
    orch, card = _setup_e2e_orch(tmp_path)
    # 关掉低分重写：写入 runtime_config.json 的 quality_policy 键（真实加载源）
    cfg_path = tmp_path / "config" / "runtime_config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("quality_policy", {})["low_score_rewrite"] = {
        "enabled": False, "max_rounds": 2, "floor": 60}
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    if hasattr(orch, "_cached_policy"):
        del orch._cached_policy  # 重置 policy 缓存
    orch.writer = _WriterMock()
    _mock_write_from_journal(orch)

    seq = _SeqReview([(34.0, "fail"), (34.0, "fail")])
    orch._stage_review = seq  # type: ignore[assignment]

    def _identity_wc(novel, lo, hi):
        return novel
    with patch.object(orch, "_enforce_word_count", side_effect=_identity_wc):
        result = orch.generate_single_chapter(4)

    # 未启用时：writer 不应触发整章重写（低分重写路径不消费）
    assert orch.writer.full_chapter_calls == 0, "disabled 时不触发低分整章重写"
    assert result.get("success") is False, "disabled 时低分章仍按原路径失败"
