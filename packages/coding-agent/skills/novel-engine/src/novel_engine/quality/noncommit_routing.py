# -*- coding: utf-8 -*-
"""CC round-21 Q2：用尽修复阶梯后仍"不提交"章的无人值守路由（纯规则，零 LLM）。

主判据是 reviewer 分数（软线 soft_publication_line，默认85），长度仅在分数已达标时
才作为是否打 shortfall 标记的次要维度：
- 分数 < 软线：无论长度都不留进 canonical -> 留 gap（隔离+人验+台账+游标继续）。
- 分数 >= 软线却仍未提交：属上游异常/灰带未覆盖，保守维持原 HALT 交人工排查。
"""
from __future__ import annotations


def _as_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def route_noncommitted(score, chars=None, severe_shortfall: bool = False,
                       soft_line: float = 85.0, publication_line: float = 88.0,
                       topup_line: float = 5780.0):
    """返回 (action, reason)。action ∈ {"gap", "halt"}。

    CC round-23（无人值守零整批 HALT）：把原来“软线<=分 但未提交一律 HALT”拆成两段——
    - 分 < 软线：质量不足 -> gap（隔离+台账+游标继续）。
    - 软线 <= 分 < 出版线：灰带，仍不可出版，但不值得为它停掉整批 -> gap（同样隔离+继续），
      绝不写正稿；由人队列/后续重抽处理。
    - 分 >= 出版线却仍未提交：才是真正的上游异常，保守 HALT 留人排查。
    """
    s = _as_float(score)
    if s is None:
        return "halt", "uncommitted_unclassified"
    if s < float(soft_line):
        n = _as_float(chars)
        if severe_shortfall or (n is not None and n < float(topup_line)):
            return "gap", "shortfall_and_quality_below_line"
        return "gap", "quality_below_line"
    if s < float(publication_line):
        return "gap", "gray_band_below_publication"
    return "halt", "uncommitted_unclassified"

def route_review_unstable(median_score, soft_line: float = 85.0):
    """CC round-22：三评极不稳定(极差>15)章的无人值守路由（纯规则，零 LLM）。

    中位分本就 < 软线 -> 该章不可出版，与质量 gap 同处置（隔离+台账+游标继续，不 HALT）。
    中位分 >= 软线（近出版却评审分歧）或分数不可解析 -> 保守 HALT 留人判，
    避免漏掉被单个畸低评分误杀的好章。返回 "gap" 或 "halt"。
    """
    s = _as_float(median_score)
    if s is not None and s < float(soft_line):
        return "gap"
    return "halt"
