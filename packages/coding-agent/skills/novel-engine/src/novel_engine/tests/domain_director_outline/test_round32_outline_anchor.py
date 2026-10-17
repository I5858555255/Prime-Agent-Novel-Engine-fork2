# -*- coding: utf-8 -*-
"""T5: 大纲锚定修复的离线测试（T1+T2+T3+T4+R1+R2+R3）。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0,
    str(Path(__file__).parent.parent.parent.parent / "src"),
)

from novel_engine.agents.chapter_director import (
    ChapterDirector,
    parse_chapter_tasks,
    OUTLINE_FILENAME,
)
from novel_engine.quality.outline_coverage_gate import (
    validate_outline_coverage,
    extract_task_keywords,
    extract_core_entities,
)


_SAMPLE_OUTLINE = """\
# 测试大纲

## 四、第一卷《昆仑遗子》

#### 阶段一(1-51章)

| 章 | 核心任务 |
|---|---|
| 1 | 神魂跨界，陆烬以婴儿形态降生雾隐村外 |
| 2 | 陈老根拾婴归家，取名陆烬 |
| 3 | 村长王大牛主持村议，决定是否收留弃婴，埋下迷雾禁婴传闻 |
| 4 | 陈老根独自抚养陆烬，交代其孤僻隐忍的性格底色 |
| 5 | 陆烬幼年体弱多病，却对浊气异常敏感 |

#### 阶段二(51-100章)

| 章 | 核心任务 |
|---|---|
| 51 | 大旱降临雾隐村，粮食危机初现 |
| 52 | 村民对迷雾禁区的恐惧与旱灾恐慌交织 |
"""


def test_parse_chapter_tasks_basic():
    result = parse_chapter_tasks(_SAMPLE_OUTLINE)
    assert result[1] == "神魂跨界，陆烬以婴儿形态降生雾隐村外"
    assert result[2] == "陈老根拾婴归家，取名陆烬"
    assert "村议" in result[3]
    assert "收留弃婴" in result[3]
    assert "王大牛" in result[3]
    assert result[4] == "陈老根独自抚养陆烬，交代其孤僻隐忍的性格底色"
    assert result[51] == "大旱降临雾隐村，粮食危机初现"
    assert result[52] == "村民对迷雾禁区的恐惧与旱灾恐慌交织"


def test_parse_chapter_tasks_duplicates_later_wins():
    content = ("| 章 | 核心任务 |\n"
               "|---|---|\n"
               "| 3 | 旧版任务A |\n"
               "\n"
               "| 章 | 核心任务 |\n"
               "|---|---|\n"
               "| 3 | 新版任务B |\n")
    result = parse_chapter_tasks(content)
    assert result[3] == "新版任务B"


def test_parse_chapter_tasks_empty_input():
    assert parse_chapter_tasks("no tables here") == {}


def test_parse_chapter_tasks_whitespace_tolerance():
    content = ("|  章  |  核心任务  |\n"
               "|---|---|\n"
               "|  8  |  多空测试  |  ")
    result = parse_chapter_tasks(content)
    assert result[8] == "多空测试"


def test_parse_chapter_tasks_malformed_no_leading_pipe():
    """R2: 无前导竖线的行也能解析。"""
    content = ("| 章 | 核心任务 |\n"
               "|---|---|\n"
               "| 1 | 正常行 |\n"
               "2 | 无前导竖线行 |\n"
               "| 3 | 又恢复正常 |\n")
    result = parse_chapter_tasks(content)
    assert result[1] == "正常行"
    assert result[2] == "无前导竖线行"
    assert result[3] == "又恢复正常"


def test_parse_chapter_tasks_fullwidth_pipe():
    """R2: 全角竖线也兼容。"""
    fw = chr(0xFF5C)
    content = ("| 章 " + fw + " 核心任务 |\n"
               "|---|---|\n"
               "| 5 " + fw + " 全角竖线测试 " + fw)
    result = parse_chapter_tasks(content)
    assert result[5] == "全角竖线测试"


def test_parse_chapter_tasks_no_trailing_pipe():
    """R2: 缺尾部竖线的行也能解析。"""
    content = ("| 章 | 核心任务 |\n"
               "|---|---|\n"
               "| 9 | 无尾竖线")
    result = parse_chapter_tasks(content)
    assert result[9] == "无尾竖线"


def test_parse_chapter_tasks_real_outline_1_300_gap_zero():
    """R2: 真实大纲第一卷 1..300 解析缺口为 0。"""
    real_path = Path(__file__).parent.parent.parent / "docs" / OUTLINE_FILENAME
    if not real_path.exists():
        pytest.skip(f"outline not found at {real_path}")
    tasks = parse_chapter_tasks(real_path.read_text(encoding="utf-8"))
    missing = [i for i in range(1, 301) if i not in tasks]
    assert len(missing) == 0, f"1..300 仍有缺口: {missing[:10]}"


def test_coverage_pass_when_keywords_present():
    task = "村长王大牛主持村议决定是否收留弃婴"
    card = {
        "core_goal": "村长王大牛召集村民讨论是否收留弃婴",
        "chapter_events": [{"event_type": "议事", "one_line_summary": "村议讨论弃婴"}],
        "scene_blueprints": [
            {"scene_num": 1, "goal": "村长主持村议", "conflict": "是否收留"},
        ],
    }
    passed, issues = validate_outline_coverage(card, task)
    assert passed is True
    assert issues == []


def test_coverage_fail_when_missing_key_elements():
    task = "村长王大牛主持村议，决定是否收留弃婴，埋下迷雾禁婴传闻"
    card = {
        "core_goal": "陆烬夜间观察古玉荧光反应",
        "chapter_events": [{"event_type": "探索", "one_line_summary": "独自探听流言"}],
        "scene_blueprints": [
            {"scene_num": 1, "goal": "探听流言", "conflict": "村民讳莫如深"},
        ],
    }
    passed, issues = validate_outline_coverage(card, task)
    assert passed is False
    assert len(issues) > 0
    assert any("村长" in i or "村议" in i for i in issues)


def test_coverage_pass_with_no_task():
    passed, issues = validate_outline_coverage({}, "")
    assert passed is True


def test_coverage_partial_match():
    task = "村长王大牛主持村议决定是否收留弃婴埋下迷雾禁婴传闻"
    card = {
        "core_goal": "讨论弃婴问题",
        "chapter_events": [],
        "scene_blueprints": [],
    }
    passed, issues = validate_outline_coverage(card, task)
    assert passed is False


def test_extract_task_keywords_filters_stops():
    task = "村长王大牛主持村议，决定是否收留弃婴"
    keywords = extract_task_keywords(task)
    assert "村长" in keywords
    assert "王大牛" in keywords
    assert "村议" in keywords
    assert "收留" in keywords
    assert "弃婴" in keywords
    assert "是否" not in keywords
    assert "决定" not in keywords


def test_core_entities_extraction():
    """R3: 核心实体提取过滤功能词。"""
    task = "村长王大牛主持村议，决定是否收留弃婴"
    entities = extract_core_entities(task)
    assert "村长" in entities
    assert "王大牛" in entities
    assert "村议" in entities
    assert "是否" not in entities


def test_core_entity_mandatory_fail():
    """R3: 塞满边缘词但无核心实体 → fail。"""
    task = "村长王大牛主持村议决定是否收留弃婴"
    card = {
        "core_goal": "陈老根清晨在屋内整理行装准备外出采药",
        "chapter_events": [{"event_type": "日常", "one_line_summary": "晨间 activities"}],
        "scene_blueprints": [{"scene_num": 1, "goal": "整理行装", "conflict": "时间紧迫"}],
    }
    passed, issues = validate_outline_coverage(card, task)
    assert passed is False
    assert any("core_entity" in i for i in issues)


def test_synonym_expression_pass():
    """R3: 同义/具象表述承载核心实体 → pass。"""
    task = "村长王大牛主持村议，决定是否收留弃婴，埋下迷雾禁婴传闻"
    card = {
        "core_goal": "老村长召集村民在老槐树下议事，就是否收养弃婴表决，禁忌说法传开",
        "chapter_events": [{"event_type": "议事", "one_line_summary": "村议讨论弃婴去留"}],
        "scene_blueprints": [
            {"scene_num": 1, "goal": "村长召集村议", "conflict": "恐惧 vs 怜悯"},
            {"scene_num": 2, "goal": "表决收留弃婴", "conflict": "争议决定由陈老根收养"},
        ],
    }
    passed, issues = validate_outline_coverage(card, task)
    assert passed is True


def test_existing_bad_card_still_fails():
    """R3: 既有 BAD 用例仍判 fail。"""
    task = "村长王大牛主持村议，决定是否收留弃婴，埋下迷雾禁婴传闻"
    card = {
        "core_goal": "陆烬夜间观察古玉荧光反应",
        "chapter_events": [{"event_type": "探索", "one_line_summary": "独自探听流言"}],
        "scene_blueprints": [
            {"scene_num": 1, "goal": "探听流言", "conflict": "村民讳莫如深"},
        ],
    }
    passed, issues = validate_outline_coverage(card, task)
    assert passed is False


def test_existing_good_card_still_passes():
    """R3: 既有 GOOD 用例仍判 pass。"""
    task = "村长王大牛主持村议决定是否收留弃婴"
    card = {
        "core_goal": "村长王大牛召集村民讨论是否收留弃婴",
        "chapter_events": [{"event_type": "议事", "one_line_summary": "村议讨论弃婴"}],
        "scene_blueprints": [
            {"scene_num": 1, "goal": "村长主持村议", "conflict": "是否收留"},
        ],
    }
    passed, issues = validate_outline_coverage(card, task)
    assert passed is True


def test_outline_path_resolution_exists(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "planning").mkdir()
    (tmp_path / "docs" / OUTLINE_FILENAME).write_text(_SAMPLE_OUTLINE, encoding="utf-8")
    (tmp_path / "config" / "runtime_config.json").write_text(
        '{"llm":{"use_mock":true}}', encoding="utf-8")
    d = ChapterDirector(tmp_path)
    assert d._outline_loaded is True
    assert "V01" in d._outline_sections


def test_outline_path_resolution_planning_fallback(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "planning").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "planning" / OUTLINE_FILENAME).write_text(
        _SAMPLE_OUTLINE, encoding="utf-8")
    (tmp_path / "config" / "runtime_config.json").write_text(
        '{"llm":{"use_mock":true}}', encoding="utf-8")
    d = ChapterDirector(tmp_path)
    assert d._outline_loaded is True
    assert "V01" in d._outline_sections


def test_outline_missing_logs_error(caplog, tmp_path):
    import logging
    (tmp_path / "config").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "planning").mkdir()
    (tmp_path / "config" / "runtime_config.json").write_text(
        '{"llm":{"use_mock":true}}', encoding="utf-8")
    with caplog.at_level(logging.ERROR):
        d = ChapterDirector(tmp_path)
    assert d._outline_loaded is False
    assert any("outline file not found" in r.message for r in caplog.records)


def test_chapter_outline_task_injected(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "planning").mkdir()
    (tmp_path / "docs" / OUTLINE_FILENAME).write_text(_SAMPLE_OUTLINE, encoding="utf-8")
    (tmp_path / "config" / "runtime_config.json").write_text(
        '{"llm":{"use_mock":true}}', encoding="utf-8")
    d = ChapterDirector(tmp_path)
    ctx, _ = d._build_shared_context(3)
    assert ctx["chapter_outline_task"] == "村长王大牛主持村议，决定是否收留弃婴，埋下迷雾禁婴传闻"
    assert "陈老根独自抚养" in ctx.get("adjacent_tasks", "")


def test_milestone_chapter_outline_injected(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "planning").mkdir()
    (tmp_path / "docs" / OUTLINE_FILENAME).write_text(_SAMPLE_OUTLINE, encoding="utf-8")
    (tmp_path / "config" / "runtime_config.json").write_text(
        '{"llm":{"use_mock":true}}', encoding="utf-8")
    d = ChapterDirector(tmp_path)
    ctx, _ = d._build_shared_context(1)
    assert "陆烬以婴儿形态降生" in ctx["chapter_outline_task"]


def test_offline_demo_ch3_ch4_tasks():
    real_outline_path = Path(__file__).parent.parent.parent / "docs" / OUTLINE_FILENAME
    if not real_outline_path.exists():
        pytest.skip(f"outline not found at {real_outline_path}")
    content = real_outline_path.read_text(encoding="utf-8")
    tasks = parse_chapter_tasks(content)
    ch3_task = tasks.get(3, "")
    assert "村议" in ch3_task or "收留" in ch3_task or "王大牛" in ch3_task, \
        f"ch3 task should mention village meeting: {ch3_task[:80]}"
    ch4_task = tasks.get(4, "")
    assert "抚养" in ch4_task or "性格" in ch4_task or "陈老根" in ch4_task, \
        f"ch4 task should mention raising: {ch4_task[:80]}"


def test_offline_demo_coverage_ch3_fail_on_bad_card():
    real_outline_path = Path(__file__).parent.parent.parent / "docs" / OUTLINE_FILENAME
    if not real_outline_path.exists():
        pytest.skip(f"outline not found at {real_outline_path}")
    content = real_outline_path.read_text(encoding="utf-8")
    tasks = parse_chapter_tasks(content)
    ch3_task = tasks.get(3, "")

    bad_card = {
        "core_goal": "陆烬探听村中关于迷雾禁婴的流言",
        "chapter_events": [{"event_type": "探索", "one_line_summary": "独自探听村中流言"}],
        "scene_blueprints": [
            {"scene_num": 1, "goal": "探听流言", "conflict": "村民讳莫如深"},
            {"scene_num": 2, "goal": "解释来历", "conflict": "无人相信"},
        ],
    }
    passed, issues = validate_outline_coverage(bad_card, ch3_task)
    assert passed is False

    good_card = {
        "core_goal": "村长王大牛主持村议，就是否收留弃婴进行表决，传出迷雾禁婴的传闻",
        "chapter_events": [
            {"event_type": "议事", "one_line_summary": "王大牛召集村民讨论弃婴去留"}
        ],
        "scene_blueprints": [
            {"scene_num": 1, "goal": "村长召集村议", "conflict": "村民恐惧 vs 怜悯"},
            {"scene_num": 2, "goal": "表决收留弃婴", "conflict": "争议决定由陈老根收养"},
        ],
    }
    passed2, issues2 = validate_outline_coverage(good_card, ch3_task)
    assert passed2 is True, f"good card should pass: {issues2}"


def test_outline_coverage_decision_regens_on_fail():
    """R1: coverage fail → 产生 feedback 并重生，不放行。"""
    task = "村长王大牛主持村议决定是否收留弃婴"
    bad_card = {
        "core_goal": "陆烬夜间观察古玉荧光",
        "chapter_events": [],
        "scene_blueprints": [],
    }
    passed, issues = validate_outline_coverage(bad_card, task)
    assert passed is False
    assert len(issues) > 0
    fb = "[大纲任务未覆盖] 缺失要素：%s" % "；".join(issues[:2])
    assert "村长" in fb or "村议" in fb or "王大牛" in fb


def test_outline_coverage_decision_finalizes_on_pass():
    """R1: coverage pass → 放行，无 feedback。"""
    task = "村长王大牛主持村议决定是否收留弃婴"
    good_card = {
        "core_goal": "村长王大牛召集村民讨论是否收留弃婴",
        "scene_blueprints": [{"scene_num": 1, "goal": "村长主持村议", "conflict": "是否收留"}],
    }
    passed, issues = validate_outline_coverage(good_card, task)
    assert passed is True
    assert issues == []


def test_outline_coverage_no_nameerror():
    """R1: 覆盖门代码路径不引用未定义变量 prior_events_block。

    升级为真正 import 并调用 recent_event_block，同时确认 orchestrator 可正常 import。
    R1-scope: 断言 recent_event_block 不在 _stage_directing 的局部变量表中，
    避免函数内局部 import 遮蔽模块级导入导致 UnboundLocalError。
    """
    from novel_engine.pipeline.event_ledger import recent_event_block, end_state_anchor_block
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        result = recent_event_block(tmp, 1)
        assert isinstance(result, str)
    from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
    assert hasattr(PipelineOrchestrator, "_stage_directing")
    # R1-scope fix: recent_event_block 不应是 _stage_directing 的局部变量
    assert "recent_event_block" not in PipelineOrchestrator._stage_directing.__code__.co_varnames
