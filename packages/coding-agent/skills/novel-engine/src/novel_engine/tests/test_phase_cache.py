# -*- coding: utf-8 -*-
"""CC P0: content-addressed phase cache determinism + invalidation."""
from novel_engine.pipeline.phase_cache import PhaseCache, content_key


def test_content_key_deterministic_and_order_insensitive():
    a = content_key({"b": 1, "a": [1, 2]}, version="review_v1", model="agnes")
    b = content_key({"a": [1, 2], "b": 1}, version="review_v1", model="agnes")
    assert a == b and len(a) == 64


def test_content_key_invalidation_propagation():
    base = {"novel_text": "同一段正文", "task_card": {"scene_blueprints": [1]}}
    h0 = content_key(base, version="review_v1", model="m")
    # upstream content changed -> hash changes (one scene regenerated -> dependent miss)
    h1 = content_key({**base, "novel_text": "同一段正文＋定点重生后的差异"}, version="review_v1", model="m")
    # prompt / model version bumps also invalidate
    h2 = content_key(base, version="review_v2", model="m")
    h3 = content_key(base, version="review_v1", model="other-model")
    assert len({h0, h1, h2, h3}) == 4


def test_set_get_roundtrip_and_deepcopy(tmp_path):
    pc = PhaseCache(tmp_path)
    h = content_key({"x": 1})
    assert pc.get(1, "review", h) is None
    payload = {"score": 90, "issues": []}
    assert pc.set(1, "review", h, payload) is True
    got = pc.get(1, "review", h)
    assert got == payload
    got["score"] = 1  # mutating returned copy must not corrupt stored file
    assert pc.get(1, "review", h)["score"] == 90


def test_namespaced_by_chapter_and_phase_and_hash(tmp_path):
    pc = PhaseCache(tmp_path)
    pc.set(1, "director", content_key({"c": 1}), {"v": "ch1"})
    pc.set(2, "director", content_key({"c": 2}), {"v": "ch2"})
    assert pc.get(1, "director", content_key({"c": 1})) == {"v": "ch1"}
    assert pc.get(2, "director", content_key({"c": 2})) == {"v": "ch2"}
    assert pc.get(1, "review", content_key({"c": 1})) is None  # phase differs -> miss


def test_cached_call_hit_does_not_recompute(tmp_path):
    pc = PhaseCache(tmp_path)
    h = content_key({"k": 1})
    calls = {"n": 0}

    def producer():
        calls["n"] += 1
        return {"normalized_score": 88}

    v1, hit1 = pc.cached_call(3, "review", h, producer)
    v2, hit2 = pc.cached_call(3, "review", h, producer)
    assert (hit1, hit2) == (False, True)
    assert calls["n"] == 1 and v1 == v2 == {"normalized_score": 88}
