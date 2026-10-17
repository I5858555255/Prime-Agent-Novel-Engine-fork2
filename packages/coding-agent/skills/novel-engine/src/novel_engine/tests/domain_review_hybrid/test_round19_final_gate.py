# -*- coding: utf-8 -*-
"""CC round-19 offline tests:
Q2 权威 scene 读取（去重取最新非空/空记录异常/完成集）；
Q3 purify 行首#指令头剥离（保留章节标题）；
Q1 提交前终检四类谓词 + 半角安全归一化。
"""
import json

import pytest

from novel_engine.pipeline import chapter_journal as cj
from novel_engine.quality import final_precommit_gate as fg
from novel_engine.quality.repetition_detector import purify_novel_for_publish


# ---------------- Q2 单一事实源 ----------------

def _write_partial(root, chapter, records):
    p = cj.journal_path(root, chapter)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_authoritative_last_nonempty_wins(tmp_path):
    recs = [
        {"scene_id": 1, "scene_text": "", "hook": "", "beats": []},
        {"scene_id": 2, "scene_text": "短场", "hook": "", "beats": []},
        {"scene_id": 3, "scene_text": "旧版第三场", "hook": "", "beats": []},
        {"scene_id": 3, "scene_text": "", "hook": "", "beats": []},
        {"scene_id": 3, "scene_text": "新版第三场（权威）", "hook": "h", "beats": ["b1"]},
    ]
    _write_partial(tmp_path, 2, recs)
    raw = cj.load_scenes(tmp_path, 2)
    assert len(raw) == 5  # append-only 审计日志全保留
    auth = cj.load_authoritative_scenes(tmp_path, 2)
    assert [s["scene_id"] for s in auth] == [2, 3]  # scene1 全空被剔除
    assert auth[1]["scene_text"] == "新版第三场（权威）"
    assert cj.get_authoritative_scene_content(raw, 3) == "新版第三场（权威）"
    assert cj.authoritative_completed_scene_ids(tmp_path, 2) == {2, 3}


def test_authoritative_missing_raises(tmp_path):
    _write_partial(tmp_path, 1, [{"scene_id": 4, "scene_text": "", "hook": "", "beats": []}])
    raw = cj.load_scenes(tmp_path, 1)
    with pytest.raises(cj.SceneContentMissingError):
        cj.get_authoritative_scene_content(raw, 4)
    assert cj.load_authoritative_scenes(tmp_path, 1) == []


# ---------------- Q3 指令头剥离 ----------------

def test_purify_strips_instruction_headers_keeps_title():
    s = "# 扩写正文\n\n夜风很冷。\n# 第2章 归家\n正文。\n# 某条内部指令\n尾段。"
    out = purify_novel_for_publish(s, 2)
    assert "扩写正文" not in out
    assert "某条内部指令" not in out
    assert "# 第2章" in out
    assert "夜风很冷" in out and "尾段" in out


def test_purify_strips_plain_hash_variant():
    out = purify_novel_for_publish("# 正文\n\n他睁开眼。", 3)
    assert "#" not in out
    assert "他睁开眼" in out


# ---------------- Q1 终检谓词 ----------------

def test_clean_cjk_chapter_passes():
    text = "# 第1章 序\n\n夜色如铅，沉沉压着。他叹了口气，道：“来了。”\n比例约0.85，价十二文。"
    r = fg.evaluate_final_text(text)
    assert r["clean"] is True
    assert r["violations"] == []


def test_latin_leak_is_immediate():
    r = fg.evaluate_final_text("他做了个 he decision 不对外说。")
    kinds = {v["kind"] for v in r["violations"]}
    assert "latin_leak" in kinds


def test_scaffolding_header_detected():
    r = fg.evaluate_final_text("# 扩写正文\n\n正常正文一句。")
    assert any(v["kind"] == "scaffolding_header" for v in r["violations"])


def test_halfwidth_detection_excludes_numeric_punct():
    hits = fg.find_halfwidth_punctuation("价12.5文;比例0.85;12:30到;他说:好")
    # 数字之间的 . : 不报；中文相邻的 ; : 报
    chars = [h["char"] for h in hits]
    assert "." not in chars and ":" not in chars or all(
        not _between_digits("价12.5文;比例0.85;12:30到;他说:好", h["index"]) for h in hits)
    assert ";" in chars  # “文;比” 中文上下文


def _between_digits(text, idx):
    return idx > 0 and idx + 1 < len(text) and text[idx - 1].isdigit() and text[idx + 1].isdigit()


def test_normalize_halfwidth_safe():
    s = "价12.5文，0.85成，12:30见。他问:你来吗?走。"
    out, n = fg.normalize_halfwidth_punctuation(s)
    assert "12.5" in out and "0.85" in out and "12:30" in out  # 数字标点保留
    assert "：" in out and "？" in out  # 中文上下文转全角
    assert n >= 2


def test_punctuation_runon_detected():
    runon = ("他坐在这张矮凳上把粗布浸进水里攥干再展开动作生涩指节变形拇指与食指费力捻开"
             "水珠顺着布纹往下淌落在木板上洇出深色先触到小脸时擦过脸颊")
    r = fg.evaluate_final_text(runon)
    assert any(v["kind"] == "punctuation" for v in r["violations"])


def test_strip_and_normalize_repair_format_issues():
    bad = "# 扩写正文\n\n他问:你来吗?走了."
    step1 = fg.strip_instruction_headers(bad)
    step2, _ = fg.normalize_halfwidth_punctuation(step1)
    assert "#" not in step2
    assert "：" in step2 and "？" in step2 and "。" in step2
    assert fg.evaluate_final_text(step2)["clean"] is True
