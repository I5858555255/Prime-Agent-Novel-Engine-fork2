# -*- coding: utf-8 -*-
"""batch_audit 失败分类器的 hermetic 测试。

用构造的日志文本验证：parse_log 解析、classify 七类分类、
hard gate 术语提取（含内层括号截断 bug 回归）、route 处置路由、
统一待处理清单（review_queue.json，含 signal 分类标签）、
pending_ratio 卷级趋势。零 LLM；backlog/queue 写入用 tmp_path 隔离。
"""
from __future__ import annotations

import json

from novel_engine.scripts.batch_audit import (
    classify,
    main,
    parse_log,
    route,
    volume_of,
)

_SAMPLE = """\
2026-10-11T01:01:08+0800 [INFO] === START chapter 1 ===
2026-10-11T01:14:01+0800 [INFO] === END chapter 1 [COMMITTED] in 772.0s (score=97.5) note=hard gate blocked but force publish 97.5: det=[] high=True ===
2026-10-11T01:14:01+0800 [INFO] === START chapter 2 ===
2026-10-11T01:19:31+0800 [INFO] === END chapter 2 [COMMITTED] in 329.6s (score=75.0) note=best 75.0 < 88 (hard gate []) ===
2026-10-11T01:19:31+0800 [INFO] === START chapter 3 ===
2026-10-11T01:19:31+0800 [INFO] === END chapter 3 [COMMITTED] in 100.0s (score=60.0) note=best 60.0 < 88 (hard gate []) ===
2026-10-11T01:19:31+0800 [INFO] === START chapter 4 ===
2026-10-11T01:19:31+0800 [INFO] === END chapter 4 [FAILED] in 200.0s (score=39.3) note=score=39.3 三评极差>15，评审极不稳定，强制人工复核（不自动重采） ===
2026-10-11T01:19:31+0800 [ERROR] chapter 4 errors: []
2026-10-11T01:19:31+0800 [INFO] === START chapter 5 ===
2026-10-11T01:19:31+0800 [INFO] === END chapter 5 [FAILED] in 300.0s (score=None) note= ===
2026-10-11T01:19:31+0800 [ERROR] chapter 5 errors: ['Chapter 5 hard-failed after retries']
2026-10-11T01:19:31+0800 [INFO] === START chapter 6 ===
2026-10-11T01:19:31+0800 [INFO] === END chapter 6 [FAILED] in 400.0s (score=79.19999999999999) note=best 79.19999999999999 < 88 (hard gate ['[canon硬词] 调息：他盯着陆烬，目光复杂："等你长大些，我会教你一套调息的法子。']) ===
2026-10-11T01:19:31+0800 [ERROR] chapter 6 errors: []
2026-10-11T01:19:31+0800 [INFO] === START chapter 7 ===
2026-10-11T01:19:31+0800 [INFO] === END chapter 7 [FAILED] in 400.0s (score=87.3) note=best 87.3 < 88 (hard gate []) ===
2026-10-11T01:19:31+0800 [ERROR] chapter 7 errors: []
"""


def test_parse_log(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    assert len(recs) == 7
    assert recs[0]["status"] == "COMMITTED" and recs[0]["score"] == 97.5
    assert recs[3]["errors"] == ["[]"]
    assert recs[4]["errors"] == ["['Chapter 5 hard-failed after retries']"]


def test_classify_green(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    cat, reason, term = classify(recs[0])
    assert cat == "pass_green" and term is None


def test_classify_ok_and_low(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    assert classify(recs[1])[0] == "pass_ok"    # 75.0
    assert classify(recs[2])[0] == "pass_low"   # 60.0


def test_classify_review_variance(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    assert classify(recs[3])[0] == "review_variance"


def test_classify_fix_loop_exhausted(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    assert classify(recs[4])[0] == "fix_loop_exhausted"


def test_classify_hard_gate_leak_extracts_term(tmp_path):
    """硬项泄漏：能从 '[canon硬词] 调息：...' 提取术语 '调息'（内层括号截断回归）。"""
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    cat, reason, term = classify(recs[5])
    assert cat == "hard_gate_leak"
    assert term == "调息", f"expected term 调息, got {term}"


def test_classify_below_threshold(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    cat, reason, term = classify(recs[6])
    assert cat == "below_threshold"


def test_route_mentions_decision_rules(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(_SAMPLE, encoding="utf-8")
    recs = parse_log(log)
    cat, _, term = classify(recs[5])
    r = route(cat, recs[5], term)
    assert "调息" in r and "R01" in r


def test_volume_of():
    """卷判定：ch1→V01、ch400→V02、ch700→V03、ch1000→V04、ch3500→V09+。"""
    assert volume_of(1) == "V01"
    assert volume_of(400) == "V02"
    assert volume_of(700) == "V03"
    assert volume_of(1000) == "V04"
    assert volume_of(3500) in ("V09", "V10", "V10+")


def test_review_queue_and_pending_ratio(tmp_path):
    """端到端：合成 7 章日志 → review_queue.json 生成，signal 分类正确，卷级比率正确。"""
    out = tmp_path / "run_volume_test.log"
    out.write_text(_SAMPLE, encoding="utf-8")
    rc = main([str(out), "--backlog", str(tmp_path / "q.md"), "--queue-dir", str(tmp_path)])
    assert rc == 1  # 有 FAILED
    queue_path = tmp_path / "review_queue_run_volume_test.json"
    assert queue_path.exists(), "review_queue.json 应生成"
    data = json.loads(queue_path.read_text(encoding="utf-8"))
    entries = data["entries"]
    signals = [e["signal"] for e in entries]
    # ch4 评审方差、ch5 修复循环耗尽、ch6 硬项泄漏、ch7 未达标；无 gray_band（ch2=75 不足 85）
    assert "review_variance" in signals
    assert "fix_loop_exhausted" in signals
    assert "hard_gate_leak" in signals
    assert "below_threshold" in signals
    assert "gray_band_spot_check" not in signals
    for e in entries:
        assert e["signal"] in ("review_variance", "fix_loop_exhausted",
                               "hard_gate_leak", "below_threshold", "gray_band_spot_check")
        assert "route" in e and "reason" in e and "chapter" in e
    meta = data["meta"]
    assert meta["n"] == 7 and meta["pending"] == 4
    assert meta["volume"] == "V01"  # 7 章都在 ch1-7 → V01


def test_gray_band_spot_check(tmp_path):
    """85-88 档 pass_ok 章应进抽查清单（gray_band_spot_check）。"""
    log_text = (
        "2026-10-11T01:01:08+0800 [INFO] === START chapter 10 ===\n"
        "2026-10-11T01:14:01+0800 [INFO] === END chapter 10 [COMMITTED] in 772.0s (score=86.5) note=best 86.5 < 88 (hard gate []) ===\n"
        "2026-10-11T01:14:01+0800 [INFO] === START chapter 11 ===\n"
        "2026-10-11T01:19:31+0800 [INFO] === END chapter 11 [COMMITTED] in 329.6s (score=80.0) note=best 80.0 < 88 (hard gate []) ===\n"
    )
    out = tmp_path / "run_volume_test2.log"
    out.write_text(log_text, encoding="utf-8")
    rc = main([str(out), "--backlog", str(tmp_path / "q2.md"), "--queue-dir", str(tmp_path)])
    assert rc == 0
    data = json.loads((tmp_path / "review_queue_run_volume_test2.json").read_text(encoding="utf-8"))
    signals = [e["signal"] for e in data["entries"]]
    assert signals == ["gray_band_spot_check"]  # ch10 86.5 抽查；ch11 80.0 不抽查
    assert data["entries"][0]["chapter"] == 10
