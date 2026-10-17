"""SLO 回归门：把 Q1(达标) 与 Q2(稳定) 变成可度量契约。"""
from pathlib import Path
import json


def _load_thresholds():
    from novel_engine.core.quality_policy import load_quality_policy
    defaults = {"min_avg": 88.0, "min_chapter": 82.0,
                "min_pacing": 7.5, "min_innovation": 7.0}
    try:
        cfg = json.loads((Path(__file__).parent.parent / "config" /
                          "runtime_config.json").read_text(encoding="utf-8"))
        q = cfg.get("quality", {})
        # 单一策略源：min_chapter_score 优先读 quality_policy（默认缺省时沿用旧值 82.0，同行为）
        policy = load_quality_policy(Path(__file__).parent.parent)
        return {
            "min_avg": q.get("min_avg_score", defaults["min_avg"]),
            "min_chapter": policy.get("min_chapter_score",
                                      q.get("min_chapter_score", defaults["min_chapter"])),
            "min_pacing": q.get("min_pacing", defaults["min_pacing"]),
            "min_innovation": q.get("min_innovation", defaults["min_innovation"]),
        }
    except Exception:
        return defaults


def evaluate_slo(report, max_fail_rate=0.01, min_avg=None, min_chapter=None,
                 min_pacing=None, min_innovation=None):
    t = _load_thresholds()
    min_avg = min_avg if min_avg is not None else t["min_avg"]
    min_chapter = min_chapter if min_chapter is not None else t["min_chapter"]
    min_pacing = min_pacing if min_pacing is not None else t["min_pacing"]
    min_innovation = min_innovation if min_innovation is not None else t["min_innovation"]

    passed = report.get("passed", 0)
    failed = report.get("failed", 0)
    total = passed + failed
    fail_rate = (failed / total) if total else 1.0
    scores = [r.get("score") for r in report.get("results", [])
              if isinstance(r.get("score"), (int, float))]
    avg = sum(scores) / len(scores) if scores else 0.0
    min_s = min(scores) if scores else 0.0
    dims = report.get("quality", {}).get("dimension_averages", {})
    meets_stability = fail_rate <= max_fail_rate
    meets_quality = (avg >= min_avg and min_s >= min_chapter
                     and dims.get("pacing", 0) >= min_pacing
                     and dims.get("innovation", 0) >= min_innovation)
    verdict = "PASS" if (meets_stability and meets_quality) else "FAIL"
    return {"fail_rate": fail_rate, "avg_score": avg, "min_score": min_s,
            "meets_stability": meets_stability, "meets_quality": meets_quality,
            "verdict": verdict}
