# -*- coding: utf-8 -*-
"""audit_mid_outline 判定规则库（decision_rules.json，bible 条款化）的 hermetic 测试。

- decide_entry 对 R01-R11 关键判例句子的判定（ch60/172/182/40/42 等实证样例）；
- main() 端到端：draft 中纲 JSON 经 audit 后写回逐条 entry.status。
零 LLM；不写真实 config/planning 文件（用 tmp_path）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from novel_engine.scripts.audit_mid_outline import (
    _load_decision_rules,
    decide_entry,
    main,
)

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

_RULES = _load_decision_rules()


def _decide(event: str, ch: int = 25) -> tuple[str, str, str | None]:
    return decide_entry(event, ["C001", "C002"], "", ch, _RULES)


def test_r01_teach_phrase_forbidden():
    """ch60 实证：'您教我认字，教我那些呼吸的法子' → needs_regenerate。"""
    st, _, rid = _decide("陆烬回忆道：您教我认字，教我那些呼吸的法子")
    assert st == "needs_regenerate" and rid == "R01"


def test_r02_cognition_phrase_allowed():
    """ch60 实证：'陆烬知道根伯会些调息的法子' → approved。"""
    st, _, rid = _decide("陆烬知道根伯会些调息的法子")
    assert st == "approved" and rid == "R02"


def test_r03_promise_teach_forbidden():
    """ch229 实证：'等你长大些，我会教你一套调息的法子' → needs_regenerate。"""
    st, _, rid = _decide("陈老根说：等你长大些，我会教你一套调息的法子")
    assert st == "needs_regenerate" and rid == "R03"


def test_r04_live_sensation_forbidden():
    """ch6 实证：'陆烬学着陈老根的样子吐纳，气感在丹田流转' → needs_regenerate。"""
    st, _, rid = _decide("陆烬学着陈老根的样子吐纳，气感在丹田流转")
    assert st == "needs_regenerate" and rid == "R04"


def test_r07_rumor_allowed():
    """ch53/209 实证：村民传闻禁区旧事（不提机制）→ approved。"""
    st, _, rid = _decide("村民传闻：禁区以前不是这样，这些年越来越邪门")
    assert st == "approved" and rid == "R07"


def test_r08_relic_clue_allowed():
    """ch40/54 实证：捡到残物铜片（F001 超长引线）→ approved。"""
    st, _, rid = _decide("陆烬在山涧捡到一枚古朴残缺铜片，藏进怀里")
    assert st == "approved" and rid == "R08"


def test_r10_recognize_hint():
    """ch42 实证：陈老根认出文字相似 → hint（approved 但附规则 note，后续可收紧）。"""
    st, why, rid = _decide("陈老根看到残石文字与呼吸法残篇相似，神色凝重")
    assert st == "approved" and rid == "R10"
    assert "R10" in why


def test_unmatched_falls_through():
    """未命中规则 → 返回空状态（交 concept/forbidden 继续判定）。"""
    st, _, rid = _decide("陆烬帮陈老根修补屋顶，村里猎户送来山货")
    assert st == "" and rid is None


def test_audit_main_writes_entry_status(tmp_path):
    """端到端：draft 中纲经 audit 后写回逐条 entry.status（approved/needs_regenerate）。"""
    out = tmp_path / "mid_outline_V01_test.json"
    payload = {
        "meta": {"vid": "V01", "range": [1, 30], "status": "draft"},
        "entries": {
            "1": {"chapter": 1, "core_event": "陆烬知道根伯会些调息的法子",
                  "characters": ["C001", "C002"], "boundary_note": None},
            "2": {"chapter": 2, "core_event": "陈老根说：等你长大些，我会教你一套调息的法子",
                  "characters": ["C002"], "boundary_note": None},
            "3": {"chapter": 3, "core_event": "村民议论禁区异象越来越邪门",
                  "characters": ["C001"], "boundary_note": None},
            "4": {"chapter": 4, "core_event": "陆烬在山涧捡到一枚古朴残缺铜片",
                  "characters": ["C001", "C002"], "boundary_note": None},
        },
    }
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    rc = main([str(out)])
    assert rc == 1, f"audit should exit 1 (1 needs_regenerate present), got {rc}"
    data = json.loads(out.read_text(encoding="utf-8"))
    entries = data["entries"]
    assert entries["1"]["status"] == "approved"      # R02 认知句
    assert entries["2"]["status"] == "needs_regenerate"  # R03 承诺传授
    assert entries["3"]["status"] == "approved"      # R07 传闻
    assert entries["4"]["status"] == "approved"      # R08 残物
    assert data["meta"].get("audit", {}).get("last_run") is True
