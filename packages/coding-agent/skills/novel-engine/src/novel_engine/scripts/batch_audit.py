"""batch_audit: 批次失败分类器 + 自动验收报告（阶段2）。

解析 run_volume_*.log 的每章 END/ERROR 行，自动分类：
  - 达标发布（score>=88，含 force publish）
  - 合格发布（73<=score<88）
  - 低分发布（score<73）
  - 评审方差类（三评极差>15）
  - 修复循环耗尽类（hard-failed after retries）
  - 硬项泄漏类（hard gate 非空，提取 canon/称谓等命中词）
  - 未达标类（FAILED 但 score 高、无硬项）
并为每类给出处置路由；汇总统计后自动追加"批次验收报告"到 backlog 唯一正本
（docs/superpowers/quality-backlog.md），可 --no-write 只打印。

额外产物（统一待处理清单 + 趋势指标，bible 无人值守加固）：
  - 写 runtime/review_queue_<log>.json：本批"需要人工看一眼"的条目统一队列，
    每条带 signal 分类标签（review_variance/fix_loop_exhausted/hard_gate_leak/
    below_threshold = 隔离稿信号；gray_band_spot_check = 85-88 档抽查）；
  - 报告与 backlog 追加 pending_ratio（卷级）：pending（失败章+抽查章）/ 总章数，
    按卷切层，趋势异常时优先检查现有规则 pattern 扩充而非新开规则。

Usage (run from src/):
    python -m novel_engine.scripts.batch_audit <run_volume_*.log> [--no-write]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

_SRC_ROOT = str(Path(__file__).resolve().parents[2])
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

ROOT = Path(__file__).resolve().parents[1]
BACKLOG_DEFAULT = (
    Path(__file__).resolve().parents[3] / "docs" / "superpowers" / "quality-backlog.md"
)

PASS_BAND_LOW = 73.0
PASS_BAND_HIGH = 88.0
GRAY_BAND_LOW = 85.0  # 85-88 档为抽查档

_END_RE = re.compile(
    r"=== END chapter (\d+) \[(\w+)\] in ([\d.]+)s \(score=([\d.]+|None)\) note=(.*)"
)
_ERR_RE = re.compile(r"\[ERROR\] chapter (\d+) errors: (.*)")
_SUMMARY_RE = re.compile(r"SUMMARY: (\d+)/(\d+) chapters succeeded, (\d+) failed")

_CANON_PREFIX = re.compile(r"\[canon硬词\]\s*([^：:，,\s]{1,12})")
_CANON_FALLBACK = re.compile(r"\[([^\[\]]+)\]")

_VOL_RE = re.compile(r"第[一二三四五六七八九十]+阶段[：:].*?第?(\d+)-(\d+)章?")
_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

_volume_ranges: list[tuple[str, int, int]] | None = None


def _load_volume_ranges() -> list[tuple[str, int, int]]:
    """从 author_intent.md 解析卷范围 → [(vid, lo, hi), ...]，避免硬编码漂移。"""
    global _volume_ranges
    if _volume_ranges is not None:
        return _volume_ranges
    _volume_ranges = []
    p = ROOT / "bible" / "author_intent.md"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"## 第([一二三四五六七八九十]+)阶段[：:].*?第?(\d+)-(\d+)章?", line)
            if not m:
                continue
            vid = f"V{_CN_NUM.get(m.group(1), 0):02d}"
            _volume_ranges.append((vid, int(m.group(2)), int(m.group(3))))
    _volume_ranges.sort(key=lambda t: t[1])
    return _volume_ranges


def volume_of(chapter: int) -> str:
    for vid, lo, hi in _load_volume_ranges():
        if lo <= chapter <= hi:
            return vid
    return "V10+"


def parse_log(log_path: Path) -> list[dict]:
    """解析批次日志 → 每章记录：{chapter, status, score, note, errors, elapsed}。"""
    chapters: dict[int, dict] = {}
    for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = _END_RE.search(line)
        if m:
            ch = int(m.group(1))
            chapters[ch] = {
                "chapter": ch,
                "status": m.group(2),
                "elapsed": float(m.group(3)),
                "score": (float(m.group(4)) if m.group(4) != "None" else None),
                "note": m.group(5),
                "errors": [],
            }
            continue
        m = _ERR_RE.search(line)
        if m:
            ch = int(m.group(1))
            errs = m.group(2)
            if ch in chapters:
                chapters[ch]["errors"].append(errs)
            else:
                chapters.setdefault(ch, {"chapter": ch, "status": "UNKNOWN",
                                         "score": None, "note": "", "errors": [errs],
                                         "elapsed": 0.0})
    return [chapters[k] for k in sorted(chapters)]


def classify(rec: dict) -> tuple[str, str, str | None]:
    """返回 (category, reason, hard_term|None)。

    category: pass_green / pass_ok / pass_low / review_variance /
              fix_loop_exhausted / hard_gate_leak / below_threshold
    """
    status = rec["status"]
    score = rec["score"]
    note = rec.get("note", "")
    errors = rec.get("errors", [])
    if status == "COMMITTED":
        if score is not None and score >= PASS_BAND_HIGH:
            if "force publish" in note:
                return "pass_green", note, None
            return "pass_green", note, None
        if score is not None and score >= PASS_BAND_LOW:
            return "pass_ok", note, None
        return "pass_low", note, None

    # FAILED 分类
    if "三评极差" in note:
        return "review_variance", note, None
    if any("hard-failed after retries" in e for e in errors):
        return "fix_loop_exhausted", "; ".join(errors), None
    m = re.search(r"hard gate \[(.*)\]", note)
    if m and m.group(1).strip() and m.group(1).strip() != "[]":
        gate_txt = m.group(1)
        cm = _CANON_PREFIX.search(gate_txt)
        term = cm.group(1) if cm else gate_txt[:40]
        return "hard_gate_leak", f"hard gate: {gate_txt[:120]}", term
    if score is not None and score >= PASS_BAND_HIGH - 2:
        return "below_threshold", f"score={score} 未达 {PASS_BAND_HIGH} 线，无硬项", None
    return "below_threshold", f"score={score} note={note[:80]}", None


def route(category: str, rec: dict, hard_term: str | None) -> str:
    """每类的自动处置路由（机器可执行，写进报告）。"""
    ch = rec["chapter"]
    if category == "pass_green":
        return f"ch{ch} 达标发布，无需处理"
    if category == "pass_ok":
        return f"ch{ch} 合格发布（73-88 档，按既定政策算最终内容）"
    if category == "pass_low":
        return f"ch{ch} 低分发布（<73），记录观察"
    if category == "review_variance":
        return f"ch{ch} 评审方差类：下批自然重跑；若复现，repro 单跑查三票形态（CC26/CC27 已兜底）"
    if category == "fix_loop_exhausted":
        return f"ch{ch} 修复循环耗尽：下批自然重跑；若复现，定位写作质量 vs 机制 bug，机制信号入 backlog 等任务书"
    if category == "hard_gate_leak":
        hint = ""
        if hard_term:
            hint = f"；命中词 [{hard_term}] 对照 decision_rules R01-R11 判例匹配处置"
        return f"ch{ch} 硬项泄漏：提取命中词自动入库，{hint}"
    return f"ch{ch} 未达标：下批自然重跑（87.x 档差线未达标）"


def _fmt_score(rec: dict) -> str:
    s = rec["score"]
    return f"{s:.2f}" if s is not None else "None"


def write_backlog(backlog: Path, report: str) -> None:
    backlog.parent.mkdir(parents=True, exist_ok=True)
    with backlog.open("a", encoding="utf-8") as f:
        f.write("\n" + report)
    print(f"[write] 验收报告已追加到 {backlog}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Batch failure classifier + acceptance report.")
    parser.add_argument("log", help="run_volume_*.log path")
    parser.add_argument("--no-write", action="store_true", help="只打印不写 backlog")
    parser.add_argument("--backlog", default=str(BACKLOG_DEFAULT), help="backlog 唯一正本路径")
    parser.add_argument("--queue-dir", default=str(ROOT / "runtime"),
                        help="统一待处理清单输出目录（默认 novel_engine/runtime）")
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"[error] log not found: {log_path}")
        return 2
    recs = parse_log(log_path)
    if not recs:
        print(f"[error] no chapter END lines parsed from {log_path.name}")
        return 2

    cats = {c: [] for c in ("pass_green", "pass_ok", "pass_low", "review_variance",
                            "fix_loop_exhausted", "hard_gate_leak", "below_threshold")}
    details: list[tuple[str, dict, str, str | None]] = []
    for rec in recs:
        cat, reason, term = classify(rec)
        cats[cat].append(rec["chapter"])
        details.append((cat, rec, reason, term))

    n = len(recs)
    n_fail = sum(1 for c in recs if c["status"] == "FAILED")
    n_ok = n - n_fail
    scores = [r["score"] for r in recs if r["score"] is not None]
    avg = sum(scores) / len(scores) if scores else 0.0
    passed_rate = n_ok / n * 100 if n else 0.0
    green = len(cats["pass_green"])
    green_rate = green / n * 100 if n else 0.0
    elapsed_total = sum(r["elapsed"] for r in recs)

    # ── 统一待处理清单（review_queue.json）+ pending_ratio 卷级趋势 ──
    queue: list[dict] = []
    for cat2, rec, reason, term in details:
        ch = rec["chapter"]
        if cat2 in ("review_variance", "fix_loop_exhausted", "hard_gate_leak", "below_threshold"):
            # 隔离稿信号（FAILED）
            queue.append({
                "chapter": ch, "signal": cat2, "score": _fmt_score(rec),
                "reason": reason[:110], "route": route(cat2, rec, term),
            })
        elif cat2 == "pass_ok" and rec["score"] is not None and rec["score"] >= GRAY_BAND_LOW:
            # 85-88 档抽查（gray-band spot check）
            queue.append({
                "chapter": ch, "signal": "gray_band_spot_check", "score": _fmt_score(rec),
                "reason": "85-88 档合格发布，按政策人工 spot-check 抽查一次",
                "route": route(cat2, rec, term),
            })
    pending = len(queue)
    pending_ratio = pending / n * 100 if n else 0.0
    vols = sorted({volume_of(r["chapter"]) for r in recs})
    vol_str = ",".join(vols)

    lines = [
        "## 批次验收报告（自动生成）",
        f"- 日志: `{log_path.name}` 章节数={n} 成功={n_ok} 失败={n_fail} "
        f"通过率={passed_rate:.1f}% 达标率(>=88)={green_rate:.1f}% 平均分={avg:.2f} "
        f"总耗时={elapsed_total:.0f}s",
        f"- 达标发布 {green} 章: {sorted(cats['pass_green'])}",
        f"- 合格发布 {len(cats['pass_ok'])} 章: {sorted(cats['pass_ok'])}",
        f"- 低分发布 {len(cats['pass_low'])} 章: {sorted(cats['pass_low'])}",
        f"- 待处理队列: pending={pending} 章（失败 {n_fail} + 抽查 {pending - n_fail}）"
        f" 比率={pending_ratio:.1f}%  卷={vol_str}",
        "- 失败分类:",
    ]
    for cat, name in (("review_variance", "评审方差"),
                      ("fix_loop_exhausted", "修复循环耗尽"),
                      ("hard_gate_leak", "硬项泄漏"),
                      ("below_threshold", "未达标/其他")):
        chs = cats[cat]
        if not chs:
            continue
        lines.append(f"  - {name} {len(chs)} 章: {sorted(chs)}")
        for cat2, rec, reason, term in details:
            if cat2 == cat:
                lines.append(f"      * ch{rec['chapter']}: score={_fmt_score(rec)} | {reason[:110]}")
    lines.append("- 处置路由（自动）:")
    for cat2, rec, reason, term in details:
        if cat2 not in ("pass_green", "pass_ok", "pass_low"):
            lines.append(f"  - {route(cat2, rec, term)}")
    gray = [e["chapter"] for e in queue if e["signal"] == "gray_band_spot_check"]
    if gray:
        lines.append(f"- 抽查清单（85-88 档）: {sorted(gray)}")
    lines.append(f"- 记录时间: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}")
    report = "\n".join(lines)

    print(f"=== 批次验收报告: {log_path.name} ===")
    print(f"章节数: {n}  成功: {n_ok}  失败: {n_fail}  通过率: {passed_rate:.1f}%")
    print(f"达标(>=88): {green} ({green_rate:.1f}%)  平均分: {avg:.2f}")
    print(f"待处理队列: pending={pending} 比率={pending_ratio:.1f}%  卷={vol_str}")
    for cat, name in (("review_variance", "评审方差"), ("fix_loop_exhausted", "修复循环耗尽"),
                      ("hard_gate_leak", "硬项泄漏"), ("below_threshold", "未达标/其他")):
        if cats[cat]:
            print(f"  {name}: {sorted(cats[cat])}")
    for cat2, rec, reason, term in details:
        if cat2 not in ("pass_green", "pass_ok", "pass_low"):
            print(f"  -> {route(cat2, rec, term)}")
    if gray:
        print(f"  抽查清单(85-88档): {sorted(gray)}")

    if not args.no_write:
        write_backlog(Path(args.backlog), report)
        _write_review_queue(Path(args.queue_dir), log_path, queue, {
            "batch": log_path.name, "generated_at": datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            "volume": vol_str, "n": n, "pending": pending, "pending_ratio": round(pending_ratio, 1),
        })
    return 0 if not n_fail else 1


def _write_review_queue(out_dir: Path, log_path: Path, queue: list[dict], meta: dict) -> None:
    """统一待处理清单：<out_dir>/review_queue_<log>.json，每条带 signal 分类标签。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"review_queue_{log_path.stem}.json"
    payload = {"meta": meta, "entries": queue}
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    print(f"[write] 统一待处理清单已写入 {out}")


if __name__ == "__main__":
    raise SystemExit(main())
