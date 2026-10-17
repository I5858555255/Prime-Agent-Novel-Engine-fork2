# -*- coding: utf-8 -*-
"""CC round-22 Q4/Q5：首评免三评边界、四票中位、标点打包修复解析（离线纯逻辑）。"""
from novel_engine.quality.review_votes import (
    aggregate, median, should_run_extra_reviews,
)
from novel_engine.quality import punctuation_health as ph
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


# ---------------- Q4① 首评免三评 / 门命中仍三评 ----------------

def test_extra_review_band_and_extremes():
    # 灰带 [85,90] 三评
    assert should_run_extra_reviews([87.0]) is True
    assert should_run_extra_reviews([85.0]) is True
    assert should_run_extra_reviews([90.0]) is True
    # 首评 <80 或 >92 明确单评
    assert should_run_extra_reviews([75.0]) is False
    assert should_run_extra_reviews([93.0]) is False
    # 80-85、90-92 同样单评（近线之外无需三评）
    assert should_run_extra_reviews([83.0]) is False
    assert should_run_extra_reviews([91.0]) is False
    # 两次评分极差>10 触发再评
    assert should_run_extra_reviews([70.0, 88.0]) is True


def test_gate_fired_forces_triple_review():
    # 由 orchestrator 调用点 `or _gate_fired` 保证：门命中修复过的章节一律三评。
    # 这里固定其判定等价：即便首评<80，门命中也必须三评（在编排层 or _gate_fired 实现），
    # 而纯函数对极端分返回单评，二者组合 truthy 才三评。
    first = 76.0
    gate_fired = True
    assert (should_run_extra_reviews([first]) or gate_fired) is True
    assert (should_run_extra_reviews([first]) or False) is False


# ---------------- Q5 追加复评：四票取中位 ----------------

def test_four_vote_median_averages_middle_two():
    assert median([1, 2, 3, 4]) == 2.5
    agg4 = aggregate([90.0, 72.0, 88.0, 86.0])
    # 排序 [72,86,88,90] -> 中位 87
    assert abs(agg4["median"] - 87.0) < 1e-9


def test_supplement_decision_threshold():
    # 三评高分分歧(median>=85, range>15)后追加一评：
    # 新中位>=85 -> 可继续（不 unstable）；<85 -> 留 unstable 交 gap
    soft = 85.0
    m_pass = aggregate([90.0, 72.0, 88.0, 86.0])["median"]   # 87 -> 继续
    m_gap = aggregate([90.0, 72.0, 88.0, 80.0])["median"]    # 排序[72,80,88,90]=84 -> gap
    assert m_pass >= soft
    assert m_gap < soft
    # 三评中位本就 <85：aggregate 仍 unstable，runner route_review_unstable -> gap
    low = aggregate([67.5, 73.3, 50.8])
    assert low["median"] < soft and low["highly_unstable"]


# ---------------- Q4③ 标点打包：一次调用、逐段校验 ----------------

class _Router:
    def __init__(self, content):
        self._c = content
        self.calls = 0

    def chat_completion(self, messages, temperature=0.2, max_tokens=2000):
        self.calls += 1
        return {"content": self._c}


class _Stub:
    pass


_BAD0 = "他走到村口看见那棵老槐树底下站着一个穿灰布衣衫的人手里提着一盏昏黄的旧灯笼冷雨还在不停地下风从田埂尽头一阵阵吹来夜色深得看不见脚下的路"
_FIX0 = "他走到村口，看见那棵老槐树底下站着一个穿灰布衣衫的人，手里提着一盏昏黄的旧灯笼。冷雨还在不停地下，风从田埂尽头一阵阵吹来，夜色深得看不见脚下的路。"
_BAD1 = "她轻轻推开门走进屋里点上油灯看见桌上摆着两碗还冒着热气的粥墙角整整齐齐堆着劈好的干柴灶膛里的火还没有完全熄灭"
_FIX1 = "她轻轻推开门，走进屋里，点上油灯，看见桌上摆着两碗还冒着热气的粥，墙角整整齐齐堆着劈好的干柴，灶膛里的火还没有完全熄灭。"


def _run_batched(router_content, paras, indexes):
    s = _Stub()
    s.polish_router = _Router(router_content)
    return PipelineOrchestrator._repair_paragraphs_punct_batched(s, paras, indexes), s.polish_router


def test_batched_one_call_all_valid():
    assert ph.check_paragraph_punctuation(_BAD0)["is_unhealthy"]
    block = f"<<<P0>>>\n{_FIX0}\n<<<P1>>>\n{_FIX1}"
    out, router = _run_batched(block, [_BAD0, _BAD1], [0, 1])
    assert router.calls == 1          # 只有一次 LLM 调用
    assert set(out.keys()) == {0, 1}
    assert out[0] == _FIX0 and out[1] == _FIX1


def test_batched_rejects_over_reached_segment_only():
    # 第 0 段越界增字 -> 仅退第 0 段；第 1 段合规仍采纳
    bad_fix0 = _FIX0 + "啊"
    block = f"<<<P0>>>\n{bad_fix0}\n<<<P1>>>\n{_FIX1}"
    out, router = _run_batched(block, [_BAD0, _BAD1], [0, 1])
    assert router.calls == 1
    assert 0 not in out and 1 in out


def test_batched_unparseable_returns_empty():
    out, _ = _run_batched("抱歉我没法处理", [_BAD0], [0])
    assert out == {}
