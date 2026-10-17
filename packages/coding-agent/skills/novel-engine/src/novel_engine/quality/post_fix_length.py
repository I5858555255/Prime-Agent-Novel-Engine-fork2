# -*- coding: utf-8 -*-
"""CC round-18 P0-3：reviewer E-loop 修订后的二次长度判定（纯规则）。

fix 循环“先删后补”可能让成稿缩水下穿软下限（真机 ch2 从 6086 掉到 4675）。
提交前再判一次：
- ok               : final >= soft_floor
- needs_topup      : soft_floor*topup_ratio <= final < soft_floor（缺口≤15%，可自动有界回补）
- severe_shortfall : final < soft_floor*topup_ratio（缺口大，转人工、游标继续）
"""
from __future__ import annotations


def post_fix_length_decision(current_len: int, soft_floor: int = 6800,
                             topup_ratio: float = 0.85) -> dict:
    topup_line = int(soft_floor * topup_ratio)
    if current_len >= soft_floor:
        return {"status": "ok", "current": current_len, "soft_floor": soft_floor,
                "topup_line": topup_line, "gap": 0}
    if current_len >= topup_line:
        return {"status": "needs_topup", "current": current_len, "soft_floor": soft_floor,
                "topup_line": topup_line, "gap": soft_floor - current_len}
    return {"status": "severe_shortfall", "current": current_len, "soft_floor": soft_floor,
            "topup_line": topup_line, "gap": soft_floor - current_len}
