"""汇总多轮报告，输出双 SLO 判定。"""
import json, glob, sys
from pathlib import Path
from novel_engine.tests.slo_gate import evaluate_slo

def main():
    reports = []
    for f in glob.glob("novel_engine/audit/mini_test_report_*.json"):
        reports.append(json.load(open(f, encoding="utf-8")))
    if not reports:
        reports.append(json.load(open("novel_engine/audit/mini_test_report.json", encoding="utf-8")))
    merged = {"passed": 0, "failed": 0, "results": [], "quality": {"dimension_averages": {}}}
    for r in reports:
        merged["passed"] += r.get("passed", 0)
        merged["failed"] += r.get("failed", 0)
        merged["results"] += r.get("results", [])
    res = evaluate_slo(merged)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    sys.exit(0 if res["verdict"] == "PASS" else 1)

if __name__ == "__main__":
    main()
