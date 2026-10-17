# -*- coding: utf-8 -*-
"""CC round-22：提交前终检多段标点修复并发化。

判定与旧串行版一致（每段≤2次、去标点逐字相同、复检健康才采纳），这里只验证：
- 合规修复全部采纳并按段索引返回；
- 越界改写（动了文字）与修复失败返回 None 的段被拒绝；
- 多段确实并发执行（墙钟短于串行、出现并发重叠）；
- 越界/非法段索引被忽略。
"""
import threading
import time

from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator


def _valid_fixed(orig: str) -> str:
    """仅插标点：每10字一个逗号，去标点后与原文逐字相同，且复检健康。"""
    chunks = [orig[i:i + 10] for i in range(0, len(orig), 10)]
    return "，".join(chunks) + "。"


class _FakeOrch:
    def __init__(self, fn, delay=0.0):
        self.fn = fn
        self.delay = delay
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def _punct_only_repair(self, orig):
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            if self.delay:
                time.sleep(self.delay)
            return self.fn(orig)
        finally:
            with self._lock:
                self.active -= 1


def _run(fn, paras, idxs, max_workers=4, delay=0.0):
    fake = _FakeOrch(fn, delay=delay)
    out = PipelineOrchestrator._repair_paragraphs_punct_parallel(
        fake, paras, idxs, max_workers=max_workers)
    return out, fake


def test_all_valid_paragraphs_fixed():
    paras = ["村" * 70, "正常的短句。", "话" * 70]
    out, _ = _run(lambda o: _valid_fixed(o), paras, [0, 2])
    assert set(out.keys()) == {0, 2}
    assert out[0] == _valid_fixed("村" * 70)
    assert out[2] == _valid_fixed("话" * 70)


def test_wording_change_rejected():
    # 去标点后多了一个字 -> 不是仅插标点，必须拒绝
    out, _ = _run(lambda o: _valid_fixed(o) + "啊", ["村" * 70], [0])
    assert out == {}


def test_repair_none_rejected():
    out, _ = _run(lambda o: None, ["村" * 70], [0])
    assert out == {}


def test_invalid_indexes_ignored():
    paras = ["村" * 70]
    seen = []
    def fn(o):
        seen.append(o)
        return _valid_fixed(o)
    out, _ = _run(fn, paras, [-1, 99, 0], max_workers=2)
    assert set(out.keys()) == {0}
    assert len(seen) == 1


def test_parallel_overlap_and_speed():
    paras = ["村" * 70 for _ in range(6)]
    t0 = time.time()
    out, fake = _run(lambda o: _valid_fixed(o), paras, list(range(6)),
                     max_workers=4, delay=0.12)
    dt = time.time() - t0
    assert set(out.keys()) == set(range(6))
    # 6 段 * 0.12s，4 并发 -> 约两波；串行至少 0.72s，留宽余量
    assert dt < 0.6, f"not parallel? dt={dt:.2f}"
    assert fake.peak >= 2, f"no concurrency observed peak={fake.peak}"
