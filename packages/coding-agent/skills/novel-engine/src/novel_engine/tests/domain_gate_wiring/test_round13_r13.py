# -*- coding: utf-8 -*-
"""CC round-13：R1 终稿定稿接线 / R2 阈值40 / R3 候选门禁审计 / R4 must_cover_beats。

纯函数与接线验证，无真实 LLM 调用。
"""
from __future__ import annotations

import json
import pytest
from pathlib import Path

from novel_engine.quality import punctuation_health as ph
from novel_engine.quality.outline_coverage_gate import (
    extract_must_cover_beats,
    check_must_cover_beats,
)


# ============================================================================
# R1：finalize_text_long_sentences 三处接线 & 同字符串保证
# ============================================================================

def test_finalize_text_long_sentences_returns_text_and_stats():
    """定稿函数返回 (text, {detected, resolved, residual})。"""
    text = "的" * 50 + "然后" + "的" * 20 + "停下。"
    fixed, stats = ph.finalize_text_long_sentences(text)
    assert isinstance(fixed, str)
    assert isinstance(stats, dict)
    assert "detected" in stats
    assert "resolved" in stats
    assert "residual" in stats
    # 去标点逐字恒等
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_finalize_preserves_dialogue_untouched():
    """引号内对话不被触碰，仅旁白长句被定稿。"""
    dialogue = '“这是一段很长的对话内容完全没有标点符号需要处理”'
    text = "的" * 50 + dialogue + "然后停下。"
    fixed, stats = ph.finalize_text_long_sentences(text)
    assert dialogue in fixed
    # 旁白长句被检测
    assert stats["detected"] >= 1
    # 去标点恒等
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_finalize_no_comma_period_sequence():
    """定稿后不得产出『，。』相邻（comma-boundary 升级场景）。"""
    # 模拟逗号前紧跟强边界词的真实场景：'，随即' → 应升级为'。'
    text = "的" * 20 + "，随即" + "的" * 30 + "，牢牢钉住。"
    fixed, stats = ph.finalize_text_long_sentences(text)
    assert "，。" not in fixed, f"Found ',。' sequence in: {fixed!r}"
    # 去标点恒等
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_finalize_short_text_unchanged():
    """短文本不应被修改。"""
    text = "他走了。"
    fixed, stats = ph.finalize_text_long_sentences(text)
    assert fixed == text
    assert stats["detected"] == 0
    assert stats["resolved"] == 0


def test_finalize_empty_text():
    """空文本安全返回。"""
    fixed, stats = ph.finalize_text_long_sentences("")
    assert fixed == ""
    assert stats["detected"] == 0
    assert stats["resolved"] == 0


# ============================================================================
# R2：阈值 40 + 41-47 残句只计不切 + 50+ 被切
# ============================================================================

def test_threshold_40_detects_40_char_sentence():
    """正好 40 字的旁白长句应被检测到（阈值含=40）。"""
    text = "的" * 40
    sents = ph.detect_long_sentences(text)
    assert len(sents) == 1
    assert sents[0]["cn_chars"] == 40


def test_threshold_40_does_not_detect_39():
    """39 字旁白不应被检测为超长句。"""
    text = "的" * 39
    sents = ph.detect_long_sentences(text)
    assert len(sents) == 0


def test_41_to_47_residual_only_no_hard_split():
    """41-47 字旁白无强边界 → 只计 residual，不切（软 issue）。"""
    text = "的" * 45  # 41-47 范围
    fixed, stats = ph.split_long_sentences(text)
    assert stats["long_sentences_detected"] == 1
    assert stats["split_count"] == 0  # 无强边界，不切
    assert fixed == text  # 文本不变
    # finalize_text_long_sentences：residual 应为 detected 长句数（非 check_text_punctuation）
    fixed2, stats2 = ph.finalize_text_long_sentences(text)
    assert stats2["detected"] == 1
    assert stats2["resolved"] == 0
    assert stats2["residual"] == 1  # 拆分后仍 ≥40 字的不可切长句计为 residual


def test_finalize_residual_counts_unsplitable_41_47_sentence():
    """构造找不到安全切分点的 41-47 字残句：finalize 应报告 detected>=1, resolved=0, residual=1。"""
    # 50 字含弱边界词"因为"，无强边界 → split 不改文本，detect 仍报 1 个长句
    text = "的" * 48 + "因为" + "的" * 5 + "停。"
    fixed, stats = ph.finalize_text_long_sentences(text)
    # 去标点恒等
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)
    # 无强边界，不切
    assert stats["resolved"] == 0
    # 仍有 ≥40 字长句（残句）
    residual_after = len(ph.detect_long_sentences(fixed, hard=ph.LONG_SENTENCE_HARD))
    assert residual_after >= 1
    assert stats["residual"] == residual_after


def test_50_plus_with_strong_boundary_gets_split():
    """50+ 字旁白含强边界 → 应被安全切分。"""
    text = "的" * 50 + "然后" + "的" * 20 + "停下。"
    fixed, stats = ph.split_long_sentences(text)
    assert stats["long_sentences_detected"] == 1
    assert stats["split_count"] >= 1
    assert "。\n" in fixed
    # 去标点逐字恒等
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


def test_long_sentence_41_to_47_no_fragment():
    """41-47 字残句不能被切成碎片（碎片长度 < 10）。"""
    # 构造一段 45 字但含弱边界的旁白（弱边界不应触发切分）
    text = "的" * 45 + "因为" + "的" * 5 + "停。"
    fixed, stats = ph.split_long_sentences(text)
    # 弱边界词不触发句号拆分
    assert "。\n" not in fixed or stats["split_count"] == 0
    # 去标点恒等保证无碎片
    assert ph.strip_all_punctuation(fixed) == ph.strip_all_punctuation(text)


# ============================================================================
# R3：candidate_gate_decisions 字段正确性 + 高分 blocked 原因取自自身
# ============================================================================

class MockOrchestrator:
    """最小模拟 orchestrator，用于测试 _write_candidate_gate_decision。"""

    def __init__(self, root: Path):
        self.root = root

    def _write_candidate_gate_decision(self, chapter_num: int, candidate_id: str,
                                        score: float, adopted: bool, reason: str,
                                        higher_blocked_score: float = 0.0,
                                        blocking_high_reasons: list = None,
                                        blocking_det_hard: list = None) -> None:
        """从 pipeline_orchestrator 复用的同名字段签名。"""
        audit_dir = self.root / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        path = audit_dir / "candidate_gate_decisions.jsonl"
        entry = {
            "chapter": chapter_num,
            "candidate_id": candidate_id,
            "score": round(score, 2),
            "adopted": adopted,
            "reason": reason,
            "higher_blocked_score": higher_blocked_score,
            "blocking_high_reasons": (blocking_high_reasons or [])[:5],
            "blocking_det_hard": (blocking_det_hard or [])[:5],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def test_gate_decision_writes_valid_jsonl(tmp_path: Path):
    """_write_candidate_gate_decision 写出的 JSONL 每行可解析且有全部字段。"""
    orch = MockOrchestrator(tmp_path)
    orch._write_candidate_gate_decision(
        chapter_num=3,
        candidate_id="green",
        score=75.2,
        adopted=True,
        reason="gate_green_adopted",
        higher_blocked_score=81.45,
        blocking_high_reasons=["style/HIGH: 单句约40字", "pacing/HIGH: scene4 慢"],
        blocking_det_hard=["long_sentence: 11 over 40"])
    path = tmp_path / "audit" / "candidate_gate_decisions.jsonl"
    assert path.exists()
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["chapter"] == 3
    assert entry["candidate_id"] == "green"
    assert entry["score"] == 75.2
    assert entry["adopted"] is True
    assert entry["reason"] == "gate_green_adopted"
    assert entry["higher_blocked_score"] == 81.45
    assert entry["blocking_high_reasons"] == ["style/HIGH: 单句约40字", "pacing/HIGH: scene4 慢"]
    assert entry["blocking_det_hard"] == ["long_sentence: 11 over 40"]


def test_gate_decision_higher_blocked_reasons_come_from_best(tmp_path: Path):
    """R3 核心：blocking_high_reasons 记录的是高分候选（best）自身的 blocking 原因，
    而非最终采纳的 green 候选的原因。"""
    orch = MockOrchestrator(tmp_path)
    # best（81.45分）自身带 style HIGH 问题；green（75.2分）自身无问题
    orch._write_candidate_gate_decision(
        chapter_num=3,
        candidate_id="green",
        score=75.2,
        adopted=True,
        reason="gate_green_adopted",
        higher_blocked_score=81.45,
        blocking_high_reasons=["style/HIGH: 高分稿的单句长问题"],  # 这是 best 的 blocking 原因
        blocking_det_hard=["long_sentence: 11 over 40"])  # 这是 best 的 det hard
    path = tmp_path / "audit" / "candidate_gate_decisions.jsonl"
    entry = json.loads(path.read_text(encoding="utf-8").strip().split("\n")[0])
    # 确认写入了高分候选的原因，不是 green 候选的
    assert entry["blocking_high_reasons"][0] == "style/HIGH: 高分稿的单句长问题"
    assert entry["blocking_det_hard"][0] == "long_sentence: 11 over 40"
    # green 候选自身无 blocking 原因
    assert entry["higher_blocked_score"] == 81.45


def test_gate_decision_multiple_entries_per_chapter(tmp_path: Path):
    """同一章可有多条候选审计记录（best + green）。"""
    orch = MockOrchestrator(tmp_path)
    orch._write_candidate_gate_decision(
        chapter_num=3, candidate_id="best", score=81.45,
        adopted=False, reason="higher_blocked")
    orch._write_candidate_gate_decision(
        chapter_num=3, candidate_id="green", score=75.2,
        adopted=True, reason="gate_green_adopted",
        higher_blocked_score=81.45, blocking_high_reasons=["style/HIGH: 单句长"])
    path = tmp_path / "audit" / "candidate_gate_decisions.jsonl"
    lines = path.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    entries = [json.loads(l) for l in lines]
    assert entries[0]["candidate_id"] == "best"
    assert entries[1]["candidate_id"] == "green"
    assert entries[1]["blocking_high_reasons"] == ["style/HIGH: 单句长"]


# ============================================================================
# R4：must_cover_beats 纯函数测试
# ============================================================================

def test_extract_must_cover_beats_from_blueprints():
    """从 scene_blueprints 提取 goal/conflict 作为 beat。"""
    card = {
        "scene_blueprints": [
            {"scene_num": 1, "goal": "陆烬在迷雾边缘醒来，适应婴儿身体",
             "conflict": "婴儿啼哭引来陈老根注意"},
            {"scene_num": 4, "goal": "村议以留婴不留祸为由通过收养决议",
             "conflict": "王大牛拍板决定收留弃婴"},
        ],
        "chapter_events": [
            {"scene_num": 4, "one_line_summary": "陈老根年轻时独自进禁区边缘，带回迷雾禁婴传闻"},
        ],
    }
    beats = extract_must_cover_beats(card)
    assert len(beats) == 5  # scene1 goal+conflict + scene4 goal+conflict + chapter_event
    # scene4 的 goal 和 conflict 应存在
    scene4_beats = [b for b in beats if b.get("scene_num") == 4]
    assert len(scene4_beats) >= 2
    assert any("留婴" in b.get("beat_text", "") for b in scene4_beats)
    assert any("王大牛" in b.get("beat_text", "") for b in scene4_beats)


def test_extract_must_cover_beats_uses_explicit_field():
    """若 task_card 已有显式 must_cover_beats，直接返回不使用蓝图推导。"""
    explicit = [
        {"scene_num": 4, "beat_text": "村议三要素全部落地", "category": "goal"},
    ]
    card = {
        "must_cover_beats": explicit,
        "scene_blueprints": [{"scene_num": 1, "goal": "ignored"}],
    }
    beats = extract_must_cover_beats(card)
    assert len(beats) == 1
    assert beats[0]["beat_text"] == "村议三要素全部落地"


def test_extract_must_cover_beats_empty_card():
    """空任务卡返回空列表。"""
    assert extract_must_cover_beats({}) == []
    assert extract_must_cover_beats(None) == []


def test_check_must_cover_beats_all_hit():
    """所有 beat 关键词均在文本中出现 → passed=True。"""
    beats = [
        {"scene_num": 4, "beat_text": "留婴不留祸通过收养", "category": "goal"},
        {"scene_num": 4, "beat_text": "王大牛拍板", "category": "conflict"},
    ]
    novel = "村里商议留婴不留祸，最终以收养为由通过。王大牛拍板同意。"
    passed, missing = check_must_cover_beats(novel, beats)
    assert passed is True
    assert missing == []


def test_check_must_cover_beats_partial_miss():
    """部分 beat 未覆盖 → passed=False，missing 列出未命中项。"""
    beats = [
        {"scene_num": 4, "beat_text": "留婴不留祸通过收养", "category": "goal"},
        {"scene_num": 4, "beat_text": "王大牛拍板", "category": "conflict"},
    ]
    novel = "村里商议留婴不留祸，最终以收养为由通过。无人表态决定。"
    passed, missing = check_must_cover_beats(novel, beats)
    assert passed is False
    assert len(missing) == 1
    assert "王大牛" in missing[0]


def test_check_must_cover_beats_empty_beats():
    """无 beats 时直接通过。"""
    passed, missing = check_must_cover_beats("任意文本", [])
    assert passed is True
    assert missing == []


def test_check_must_cover_beats_fallback_without_jieba():
    """jieba 不可用时回退正则分词仍工作。"""
    beats = [
        {"scene_num": 3, "beat_text": "外部施压事件发生", "category": "event"},
    ]
    novel = "外部施压事件发生了。"
    passed, missing = check_must_cover_beats(novel, beats)
    assert passed is True
