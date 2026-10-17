# -*- coding: utf-8 -*-
"""CC round-13 密度门：语义锚点存在性匹配 + 校准回归案例（合格/假阳性救回/真空洞被拦）。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_engine.quality import density_gate as dg


@pytest.fixture()
def root_arc(tmp_path: Path):
    cfg = {
        "coverage_threshold": 0.6,
        "event_hit_min": 2,
        "event_hit_ratio": 0.5,
        "event_max_terms": 4,
        "function_chars": "了的是在他她它一有不和人与又就都很像被把让给而却但也还着过个",
        "interaction_clusters": {"争执": ["争执", "争论", "争吵", "吵", "议论", "道", "说", "劝"]},
        "interaction_keywords": ["对话", "冲突", "争执"],
        "placeholder_tokens": ["村民A", "村民B", "甲", "乙", "A", "B"],
        "meta_stopwords": ["通过", "描述", "暗示", "铺垫", "为", "向", "朝", "对", "被", "把", "将"],
        "abstract_verbs": ["被遗弃", "被放置", "遗弃", "放置", "远离", "决定", "试图"],
        "event_object_cap": 3,
        "event_action_cap": 2,
        "protagonist_aliases": {"陆烬": ["陆烬", "婴儿", "婴孩", "孩子", "他"]},
        "generic_synonyms": {
            "婴儿": ["婴儿", "婴孩", "孩子", "男婴"],
            "黑影": ["黑影", "影子", "老者", "老人"],
            "啼哭": ["啼哭", "哭", "抽噎", "哼声"],
            "醒来": ["醒来", "醒转", "醒"],
            "抱起": ["抱起", "抱住", "抱回", "裹进", "裹住", "抱"],
        },
        "interiority_markers": ["他想", "前世"],
    }
    syn_dir = tmp_path / "config" / "synonym_map"
    syn_dir.mkdir(parents=True)
    (syn_dir / "infant.json").write_text(json.dumps({
        "synonyms": {
            "灼烧": ["灼烧", "烫", "灼痛", "烧"],
            "苔藓": ["苔藓", "青苔", "苔"],
            "火把": ["火把", "灯", "火", "油灯"],
            "迷雾": ["迷雾", "浓雾", "雾", "雾气"],
            "村民": ["村民", "村人", "李嫂", "陈五", "老赵", "众人", "人群"],
            "肢体": ["肢体", "胳膊", "手臂", "四肢"],
        }
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path, "infant", cfg


def _bp(events, interactions=None, reveals=None):
    return {
        "scene_num": 1,
        "concrete_events": events,
        "named_interactions": interactions or [],
        "info_reveal_points": reveals or [],
    }


# ── 纯函数 ──────────────────────────────────────────────────────────────
def test_extract_content_terms_keeps_concrete_drops_meta(root_arc):
    _, _, cfg = root_arc
    terms, actions = dg.extract_content_terms("通过感官描述他被遗弃在荒野，被放置在苔藓上", cfg)
    # 抽象/元措辞被剔除，具体名词保留
    assert "荒野" in terms
    assert "苔藓" in terms
    assert "通过" not in terms and "描述" not in terms
    assert "遗弃" not in terms and "放置" not in terms
    assert len(terms) <= cfg["event_max_terms"]


def test_anchor_terms_prefer_explicit_then_quote(root_arc):
    # 显式 anchor_terms 优先
    assert dg.extract_anchor_terms({"content": "x", "anchor_terms": ["灼烧", "禁区"]}) == ["灼烧", "禁区"]
    # 无显式时从引号专名回退
    anchors = dg.extract_anchor_terms({"content": "描述‘灼烧感’暗示禁区边缘"})
    assert any("灼烧" in a for a in anchors)


def test_event_covered_by_synonym_not_bigram(root_arc):
    _, _, cfg = root_arc
    syn = {"苔藓": ["苔藓", "青苔"]}
    ev = {"event": "婴儿被放在荒野苔藓上", "observable_action": "躺着啼哭"}
    # 同义改写：写“青苔”“荒野”，不抄蓝图“苔藓”整句
    ok, d = dg.event_covered(ev, "那婴孩蜷在荒野的青苔里低声啼哭", cfg, syn)
    assert ok and d["total"] >= 2


def test_event_not_covered_when_vacuum(root_arc):
    _, _, cfg = root_arc
    ev = {"event": "村民举火把靠近婴儿", "observable_action": "伸手指点"}
    ok, d = dg.event_covered(ev, "只有雾气弥漫，寒意层层翻涌，四下空无一人", cfg, {})
    assert not ok


def test_interaction_placeholder_dialogue_fallback(root_arc):
    _, _, cfg = root_arc
    it = {"characters": ["村民A", "村民B"], "interaction_type": "争执",
          "brief": "是否收留弃婴的口角"}
    prose = "李嫂沉声道：“这娃不能留。”陈五急了，众人你一句我一句吵个不停。"
    ok, d = dg.interaction_covered(it, prose, cfg, {})
    assert ok  # 占位名 -> 引号对话 + 人群/自造配角 + 争论词
    # 真空洞（无人物无对话）不判覆盖
    ok2, _ = dg.interaction_covered(it, "雾气翻涌，死寂无声，什么都没有发生。", cfg, {})
    assert not ok2


def test_reveal_anchor_hit_variant(root_arc):
    rv = {"type": "伏笔", "content": "通过感官描述‘灼烧感’，暗示禁区", "anchor_terms": ["灼烧"]}
    ok, d = dg.reveal_covered(rv, "那雾气顺着毛孔烫进来，像针在扎。", {"灼烧": ["灼烧", "烫"]})
    assert ok and d["hit"] == ["灼烧"]


# ── 场景级校准三案例（永久回归）─────────────────────────────────────────
def _ch1_bp():
    return _bp(
        events=[
            {"event": "陆烬在迷雾边缘醒来，适应婴儿身体", "observable_action": "啼哭、挣扎、控制肢体"},
            {"event": "陈老根现身抱起婴儿", "observable_action": "黑影出现在雾中，裹进外袍抱回村"},
        ],
        interactions=[{"characters": ["两名村民"], "interaction_type": "争执",
                        "brief": "村民争论婴儿吉凶去留"}],
        reveals=[{"type": "伏笔埋设", "content": "通过感官描述空气中的灼烧感，暗示禁区边缘",
                  "anchor_terms": ["灼烧", "迷雾", "禁区"]}],
    )


def test_director_normalizes_placeholder_names():
    """导演卡片出口：村民A/B 等占位名归一为明确身份指代，canonical 名保留。"""
    from novel_engine.agents.chapter_director import _normalize_placeholder_characters as norm
    card = {"scene_blueprints": [{"named_interactions": [
        {"characters": ["村民A", "村民B"], "interaction_type": "争执"},
        {"characters": ["陆烬（婴儿）", "村民A"], "interaction_type": "接触"},
    ]}]}
    its = norm(card)["scene_blueprints"][0]["named_interactions"]
    assert its[0]["characters"] == ["两名村民"]
    assert its[1]["characters"][0] == "陆烬（婴儿）"
    assert "村民A" not in its[1]["characters"]


def test_calibration_valid_chapter_passes(root_arc):
    """合格正样本：出版向具体叙事、事件演足但非逐字抄写 -> 必须放行。"""
    root, arc, cfg = root_arc
    prose = (
        "他睁开眼，入目是死寂的白。绿白色的雾缠上来，顺着毛孔烫进来，把骨髓都烫开了，"
        "他想，这具身体太小，竟是个婴儿，只能发出一声啼哭，四肢胡乱挣扎。\n"
        "李嫂沉声道：“这娃来路不正，留不得。”陈五红着脸吵：“好歹是条命！”众人议论纷纷。\n"
        "浓雾里一道黑影逼近，陈老根脱下外袍，把蜷在苔藓洼处的婴儿裹进怀里，转身抱回村子。"
    )
    res = dg.evaluate_scene_semantic(_ch1_bp(), prose, cfg, root=root, arc=arc)
    assert res["ratio"] >= 0.6, res


def test_calibration_false_positive_rescued(root_arc):
    """假阳性样本：抽象蓝图措辞 vs 正文同义改写（不出现‘灼烧感/禁区/苏醒’等原词）-> 仍应放行。"""
    root, arc, cfg = root_arc
    prose = (
        "浓得化不开的雾贴着地皮流动，针一样的烫意钻进七窍，那婴孩醒转过来，扭动细小的胳膊，"
        "发出微弱的抽噎。黑影般的老人叹了口气，脱下袍子把赤条条的孩子裹住，抱在温热的怀里，"
        "村人举着昏黄的灯围在远处低声争执，谁也不敢靠近那片青苔洼。"
    )
    res = dg.evaluate_scene_semantic(_ch1_bp(), prose, cfg, root=root, arc=arc)
    assert res["ratio"] >= 0.6, res["missing"]


def test_calibration_true_vacuum_blocked(root_arc):
    """真空洞反例：纯氛围/心理，规定事件/互动/伏笔全未演 -> 必须拦。"""
    root, arc, cfg = root_arc
    prose = ("四周只有浓得化不开的雾，灰白色层层翻涌，冷意弥漫，一切寂静无声。"
             "他感到莫名的恍惚与孤独，意识随雾起起伏伏，什么都没有发生，时间仿佛凝固。")
    res = dg.evaluate_scene_semantic(_ch1_bp(), prose, cfg, root=root, arc=arc)
    assert res["ratio"] < 0.6, res["rows"]
