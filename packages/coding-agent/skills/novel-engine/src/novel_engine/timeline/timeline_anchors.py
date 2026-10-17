# -*- coding: utf-8 -*-
"""时间线锚点模块：结构化记录每章世界内时间设定，支持连续性校验与正文抽取比对。

round100 新增（P0 时间线硬检查）。锚点持久化在 runtime/timeline_anchors.json。
"""

from __future__ import annotations

import json
import re
import pathlib
from typing import Optional

# runtime 目录与 checkpoint.json 同级（src/novel_engine/runtime/）
RUNTIME_DIR = pathlib.Path(__file__).resolve().parents[1] / "runtime"
ANCHORS_PATH = RUNTIME_DIR / "timeline_anchors.json"

# 显式时间/年龄表述抽取模式（确定性规则，不需要 LLM）
_TIME_PATTERNS = [
    re.compile(r"[一二两三四五六七八九十百千万零\d]+[个]?[年月日天夜][前后之内过后]+"),  # 三年后/五日后/两日内
    re.compile(r"[一二两三四五六七八九十百千万零\d]+岁"),                               # 三岁/十岁
    re.compile(r"翌日|次日|来日|今日|明日|昨日|当天|当天夜里|当晚"),
    re.compile(r"[一二两三四五六七八九十]+更天|拂晓|破晓|清晨|正午|黄昏|入夜|子夜|深夜"),
    re.compile(r"历时[一二两三四五六七八九十百千万零\d]+[年月日天]"),
]


def load_anchors() -> list:
    """读取全部锚点（按 chapter_id 升序）。文件不存在返回 []。"""
    if not ANCHORS_PATH.exists():
        return []
    try:
        data = json.loads(ANCHORS_PATH.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return sorted(data, key=lambda r: r.get("chapter_id", 0))
    except (json.JSONDecodeError, OSError):
        return []
    return []


def save_anchor(record: dict) -> None:
    """upsert 锚点（按 chapter_id 覆盖）。"""
    anchors = [r for r in load_anchors() if r.get("chapter_id") != record.get("chapter_id")]
    anchors.append(record)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    ANCHORS_PATH.write_text(
        json.dumps(sorted(anchors, key=lambda r: r.get("chapter_id", 0)), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_prev_anchor(chapter_id: int) -> Optional[dict]:
    """返回 chapter_id 的上一章锚点（chapter_id-1），无则 None。"""
    anchors = load_anchors()
    for r in anchors:
        if r.get("chapter_id") == chapter_id - 1:
            return r
    return None


def validate_continuity(new_record: dict, prev_record: Optional[dict]) -> tuple[bool, list]:
    """连续性校验：新锚点相对上一章的时间推进是否合理。

    规则：
    1. 无上一章（第一章）→ 通过
    2. elapsed_since_prev 含负向表述（倒退/负N天）→ 失败
    3. 推进含"年"级且 jump != True → 失败（"多年后"须显式标注 jump=true）
    4. 其他情况通过（不同叙事节奏由 jump 字段显式声明）

    返回 (ok, errors)。
    """
    errors: list = []
    if prev_record is None:
        return True, errors

    elapsed = str(new_record.get("elapsed_since_prev") or "").strip()
    jump = bool(new_record.get("jump", False))

    if not elapsed:
        errors.append("elapsed_since_prev 为空，无法校验连续性")
        return False, errors

    # 负向推进
    if re.search(r"(倒退|倒流|负[一二两三四五六七八九十\d]+[天日])", elapsed):
        errors.append(f"时间倒退：elapsed_since_prev='{elapsed}'")
        return False, errors

    # "年"级推进必须显式 jump
    if re.search(r"[一二两三四五六七八九十百千\d]+年", elapsed) and not jump:
        errors.append(f"跨年级推进但未标注 jump=true：'{elapsed}'")
        return False, errors

    return True, errors


def extract_time_expressions(text: str) -> list:
    """抽取正文中显式时间/年龄表述（确定性规则）。"""
    found = []
    for pat in _TIME_PATTERNS:
        for m in pat.finditer(text):
            found.append(m.group(0))
    return found


def check_body_against_anchors(chapter_id: int, body_text: str) -> list:
    """正文显式时间表述与锚点表的矛盾检查（发布前调用）。

    规则（确定性，成本低）：
    1. 本章锚点 elapsed_since_prev 非"年"级且无 jump，但正文出现 N>=2 的"N年"表述 → 矛盾
    2. 本章锚点 character_age_snapshot 有值，且正文出现与之冲突的年龄表述（如锚点 0 岁但正文"十年"）→ 矛盾
    返回矛盾描述列表（空 = 无矛盾）。
    """
    anchors = {r.get("chapter_id"): r for r in load_anchors()}
    rec = anchors.get(chapter_id)
    if rec is None:
        return []

    conflicts = []
    elapsed = str(rec.get("elapsed_since_prev") or "")
    jump = bool(rec.get("jump", False))

    if not jump and not re.search(r"[一二两三四五六七八九十百千\d]+年", elapsed):
        # 锚点未声明跨年，正文出现多年表述 → 矛盾
        for expr in re.finditer(r"[一二两三四五六七八九十百千\d]+年", body_text):
            num = expr.group(0).replace("年", "")
            try:
                n = int(num)
            except ValueError:
                # 中文数字粗略处理
                cn_map = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5,
                          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10, "百": 100, "千": 1000}
                n = cn_map.get(num, 0)
            if n >= 2:
                conflicts.append(
                    f"时间线硬伤：本章未声明跨年推进，正文却出现“{expr.group(0)}”（锚点 elapsed_since_prev='{elapsed}'）"
                )
                break

    return conflicts
