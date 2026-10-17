# -*- coding: utf-8 -*-
"""bible 无人值守加固（修复1-5）的 hermetic 测试。

- 覆盖审计：V01/V02 条款全判定化（K=0），V03 未条款化（K>0 → 卡关）；
- F 编号锚点校验：boundary_note 的 F00X 与中纲文本锚点不匹配 → pending_human；
- 新角色检测：未登记专名 → pending_human；C 编号已登记豁免；
- rule_applies 卷范围过滤：R13-R15 仅 V02 生效，V01 章节不误伤；
- 概念解锁规划：unlock_schedule 状态查询。
零 LLM；中纲一律 tmp_path 合成；bible/规则表用仓库真实文件（受控输入）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from novel_engine.scripts.audit_mid_outline import (
    ROOT,
    _coverage_audit,
    _current_volume_forbidden,
    _load_character_registry,
    _load_decision_rules,
    _load_foreshadow_anchors,
    _load_unlock_schedule,
    _schedule_status,
    _unregistered_proper_names,
    decide_entry,
    main,
)

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_RULES = _load_decision_rules()
_AUTHOR_INTENT = (ROOT / "bible" / "author_intent.md").read_text(encoding="utf-8")


# ── 覆盖审计（修复1）───────────────────────────────────────────────

def test_coverage_audit_v01_fully_covered():
    msgs, uncovered = _coverage_audit(_AUTHOR_INTENT, _RULES, "V01")
    assert not uncovered, f"V01 应全判定化，未覆盖: {msgs}"
    assert any("已判定化 4 条" in m for m in msgs)


def test_coverage_audit_v02_fully_covered():
    msgs, uncovered = _coverage_audit(_AUTHOR_INTENT, _RULES, "V02")
    assert not uncovered, f"V02 应全判定化（R13-R15），未覆盖: {msgs}"
    assert any("已判定化 3 条" in m for m in msgs)


def test_coverage_audit_v03_fully_covered():
    """V03 预判例：2 条 forbidden 由 R16/R17 覆盖 → K=0 不卡关。"""
    msgs, uncovered = _coverage_audit(_AUTHOR_INTENT, _RULES, "V03")
    assert not uncovered, f"V03 应全判定化（R16/R17），未覆盖: {msgs}"
    assert any("已判定化 2 条" in m for m in msgs)


def test_coverage_audit_v04_uncovered_blocks():
    """V04 尚未条款化 → K>0（进入该卷批次时卡关，防无人值守漏判）。"""
    _, uncovered = _coverage_audit(_AUTHOR_INTENT, _RULES, "V04")
    assert uncovered


def test_forbidden_scan_strips_positive_examples():
    """条款子串匹配只保留禁止语义段，正例例句不误伤。"""
    forbidden = _current_volume_forbidden(_AUTHOR_INTENT, "V01", 100)
    long_clause = [f for f in forbidden if "修炼样式动作" in f][0]
    positive_sample = "陈老根独自在院中吐纳，陆烬在旁看着"
    from novel_engine.scripts.audit_mid_outline import _forbidden_scan_text
    assert _forbidden_scan_text(long_clause) in long_clause
    assert positive_sample not in _forbidden_scan_text(long_clause)


# ── F 编号锚点校验（修复2）────────────────────────────────────────

def test_f_anchor_mismatch_pending_human(tmp_path):
    """boundary_note 标 F004（线粒体/地球）但中纲实为迷雾异象 → pending_human。"""
    out = tmp_path / "mid_outline_V01_mislabel.json"
    payload = {
        "meta": {"vid": "V01", "range": [60, 60], "status": "draft"},
        "entries": {
            "60": {"chapter": 60, "core_event": "陆烬与沈知微在驿站歇脚，窗外雾起",
                   "characters": ["C001", "C005"], "boundary_note": "接近F004（线粒体）回收窗口"},
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rc = main([str(out), "--no-write"])
    assert rc == 0
    # --no-write 不落盘；直接验证判定函数
    anchors = _load_foreshadow_anchors()
    from novel_engine.scripts.audit_mid_outline import _check_f_anchor
    joined = "陆烬与沈知微在驿站歇脚，窗外雾起 C001、C005"
    issue = _check_f_anchor("接近F004（线粒体）回收窗口", joined, anchors)
    assert issue is not None and "F004" in issue


def test_f_anchor_match_approved(tmp_path):
    """boundary_note 标 F001 且中纲含锚点词（残物铜片）→ 无错位问题。"""
    anchors = _load_foreshadow_anchors()
    from novel_engine.scripts.audit_mid_outline import _check_f_anchor
    joined = "陆烬翻出那枚残物铜片端详 C001"
    assert _check_f_anchor("F001 首件残物埋设", joined, anchors) is None


def test_f_anchor_ch53_78_real_case():
    """ch53-78 实锤案例：boundary_note 标 F004 但文本是迷雾异象 → 必被 F 锚点抓出。"""
    anchors = _load_foreshadow_anchors()
    from novel_engine.scripts.audit_mid_outline import _check_f_anchor
    joined = "迷雾边缘异域尸骨，村民恐慌议论 C001"
    issue = _check_f_anchor("接近F004（线粒体）回收窗口", joined, anchors)
    assert issue is not None and "F004" in issue


# ── 新角色检测（修复5）─────────────────────────────────────────────

def test_new_role_detection():
    registry, registry_ids = _load_character_registry()
    assert _unregistered_proper_names(["C001", "C002"], registry, registry_ids) == []
    assert _unregistered_proper_names(["赵铁柱"], registry, registry_ids) == ["赵铁柱"]
    assert _unregistered_proper_names(["掌柜"], registry, registry_ids) == []       # 通用称谓
    assert _unregistered_proper_names(["青山镇"], registry, registry_ids) == []     # 地名结尾


def test_new_role_blocks_batch(tmp_path):
    out = tmp_path / "mid_outline_V01_newrole.json"
    payload = {
        "meta": {"vid": "V01", "range": [88, 88], "status": "draft"},
        "entries": {
            "88": {"chapter": 88, "core_event": "陆烬在镇上遇见赵铁柱，受托捎带山货",
                   "characters": ["C001", "赵铁柱"], "boundary_note": None},
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rc = main([str(out)])
    assert rc == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["entries"]["88"]["status"] == "pending_human"


# ── rule_applies 卷范围过滤（P4 防误伤）────────────────────────────

def test_r13_v02_only():
    text = "乾武帝暗中资助陆烬，实为赎罪"
    st_v02, _, rid = decide_entry(text, ["C007", "C001"], "", 400, _RULES, vol_id="V02")
    assert st_v02 == "needs_regenerate" and rid == "R13"
    st_v01, _, rid_v01 = decide_entry(text, ["C007", "C001"], "", 300, _RULES, vol_id="V01")
    assert st_v01 == "" and rid_v01 is None  # V01 章节不误伤


def test_r14_r15_v02():
    st, _, rid = decide_entry("七窍碑残页的真正用途是开启鹿台秘境", ["C001"], "", 400, _RULES, vol_id="V02")
    assert st == "needs_regenerate" and rid == "R14"
    st2, _, rid2 = decide_entry("陆烬气息一变，就此突破筑基", ["C001"], "", 400, _RULES, vol_id="V02")
    assert st2 == "needs_regenerate" and rid2 == "R15"


def test_r08_chapter_window_only():
    """R08 残物线索仅 [1,1290] 窗口适用，1291 后不再命中（F001 铺垫窗口结束）。"""
    text = "陆烬在山涧捡到一枚古朴残缺铜片"
    st_in, _, rid = decide_entry(text, ["C001"], "", 1000, _RULES, vol_id="V01")
    assert st_in == "approved" and rid == "R08"
    st_out, _, rid_out = decide_entry(text, ["C001"], "", 1300, _RULES, vol_id="V02")
    assert st_out == "" and rid_out is None


# ── V03 预判例（R16/R17 + R15 窗口扩展）────────────────────────────

def test_r16_soul_workshop_full_exposure_forbidden():
    st, _, rid = decide_entry("摄魂阁的组织全貌终于浮出水面，人造飞升是他们的真实目的",
                              ["C001"], "", 700, _RULES, vol_id="V03")
    assert st == "needs_regenerate" and rid == "R16"
    # V01 不误伤
    st_v01, _, rid_v01 = decide_entry("摄魂阁的组织全貌终于浮出水面，人造飞升是他们的真实目的",
                                      ["C001"], "", 700, _RULES, vol_id="V01")
    assert st_v01 == "" and rid_v01 is None


def test_r16_vague_clue_allowed():
    """V03#1 允许第702/848章模糊线索：'摄魂阁近来有动静'不含揭露措辞 → 放行。"""
    st, _, rid = decide_entry("摄魂阁的人近来在黑市出没，行踪诡秘", ["C001"], "", 700, _RULES, vol_id="V03")
    assert st == "" and rid is None  # 交 concept/forbidden 继续判定


def test_r17_soul_secret_fully_known_forbidden():
    st, _, rid = decide_entry("摄魂阁长老已完全掌握陆烬两界神魂的秘密",
                              ["C001"], "", 700, _RULES, vol_id="V03")
    assert st == "needs_regenerate" and rid == "R17"


def test_r17_vague_awareness_allowed():
    """V03#2 允许'察觉异样'：'长老觉得陆烬神魂有些异样' 无完全掌握措辞 → 放行。"""
    st, _, rid = decide_entry("长老觉得陆烬神魂有些异样，多看了两眼",
                              ["C001"], "", 700, _RULES, vol_id="V03")
    assert st == "" and rid is None


def test_r15_extended_to_v03_until_987():
    """R15 窗口扩展：ch700（V03 内）筑基突破仍拦；ch988（V03 高潮）放行。"""
    text = "陆烬气息一变，就此突破筑基"
    st_700, _, rid = decide_entry(text, ["C001"], "", 700, _RULES, vol_id="V03")
    assert st_700 == "needs_regenerate" and rid == "R15"
    st_988, _, rid_988 = decide_entry(text, ["C001"], "", 988, _RULES, vol_id="V03")
    assert st_988 == "" and rid_988 is None


# ── 概念解锁规划（修复4）───────────────────────────────────────────

def test_schedule_status():
    schedule = _load_unlock_schedule()
    assert _schedule_status(schedule, "V01") == "covered"
    assert _schedule_status(schedule, "V02") == "planned"
    assert _schedule_status(schedule, "V03") == "planned"
    assert _schedule_status(schedule, "V05") is None  # 未规划 → audit 输出提醒


# ── V02 端到端（R13 命中 → needs_regenerate）───────────────────────

def test_v02_batch_audit_end_to_end(tmp_path):
    out = tmp_path / "mid_outline_V02_test.json"
    payload = {
        "meta": {"vid": "V02", "range": [320, 320], "status": "draft"},
        "entries": {
            "320": {"chapter": 320, "core_event": "乾武帝暗中资助陆烬，实为赎罪",
                    "characters": ["C001", "C007"], "boundary_note": None},
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rc = main([str(out), "--no-write"])
    assert rc == 1  # R13 命中 → needs_regenerate
    # 覆盖审计 V02 K=0 不卡关；R13 判定在 decide_entry 层已验证
    msgs, uncovered = _coverage_audit(_AUTHOR_INTENT, _RULES, "V02")
    assert not uncovered
