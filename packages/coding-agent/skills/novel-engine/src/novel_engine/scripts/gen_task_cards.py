"""gen_task_cards: 阶段3——失败章模板任务书生成器（门禁卡关的自动修复循环）。

复用 batch_audit 的 parse_log/classify：对每类失败章按模板生成"任务书"
（markdown，含根因定位方法/修复步骤/验收门禁），写入
docs/superpowers/task_cards/ 目录（与 backlog 同体系，可提交可追溯）。

失败类别 → 根因模板映射（引用 novel-engine-runner references/root-causes.md
模式编号 + config/planning/decision_rules.json 判例编号）：
  - review_variance     → 评审三票形态 repro（温度/阈值/CC26/CC27 兜底）
  - fix_loop_exhausted  → root-causes 1/2/3 定位法（委托/未入参/staticmethod）
  - hard_gate_leak      → 命中词 → 判例匹配（R01-R11）：判例内=规则层修复；
                          判例外=需人工确认新判例（bible 外新设定，唯一人工点）
  - below_threshold     → 无需任务书（下批自然重跑），跳过

每张卡尾部固定"验收门禁"清单（机器可执行）：
  1. pyflakes 全包 0 undefined name
  2. 全量 pytest 仅允许 7 failed 基线（6 agnes 环境差异 + 1 已知 pre-existing）
  3. 行为改动（改变生成/发布语义）须人工确认；机制改动（接线/修复通道）可自动
  4. 修复后复跑/复现该章，验证失败分类消失或转移

Usage (run from src/):
    python -m novel_engine.scripts.gen_task_cards <run_volume_*.log> [--out DIR] [--no-write]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

_SRC_ROOT = str(Path(__file__).resolve().parents[2])
if _SRC_ROOT not in sys.path:
    sys.path.insert(0, _SRC_ROOT)

from novel_engine.scripts.batch_audit import classify, parse_log  # noqa: E402

_OUT_DEFAULT = (
    Path(__file__).resolve().parents[3] / "docs" / "superpowers" / "task_cards"
)

# 根因模板（内容源: novel-engine-runner references/root-causes.md + decision_rules.json）
_TEMPLATES = {
    "review_variance": {
        "title": "评审方差三票形态核验",
        "root_refs": ["root-causes.md #4 det 硬项只检测不修复（对照）",
                      "runtime_config 评审 temperature=0 / TIGHT_CLUSTER_MAX=7 / CC27 缓坡补票"],
        "steps": [
            "用 repro 单跑（参考 agents 工作区 repro_ch148.py 模板）带全量日志重跑该章，捕获 reviewer 三票明细",
            "判定三票差异来源：同温度不同采样=CC26/CC27 已兜底；温度/提示词差异=记录进 backlog",
            "若复现且非温度因素，按 root-causes 定位法缩小到 review 相位，产出最小复现",
        ],
        "gate_note": "只读诊断，不改生成语义；产出三票明细记录即可，不要求本次修复",
    },
    "fix_loop_exhausted": {
        "title": "修复循环耗尽根因定位",
        "root_refs": ["root-causes.md #1 staticmethod 委托绑定丢失",
                      "root-causes.md #2 提取方法后外部局部变量未入参",
                      "root-causes.md #3 @staticmethod 内引用 self"],
        "steps": [
            "先 pyflakes 全包扫描该改动涉及文件，排除 undefined name（#2/#3 是历史高发）",
            "复现最小场景（合成 fixture，不依赖真实 10 章产物），确认是写作质量还是机制 bug",
            "机制 bug → 按 root-causes 对应模式修复并补回归测试；写作质量 → 记录进 backlog 等人工/下批",
        ],
        "gate_note": "机制改动必须补 hermetic 回归测试；行为改动须人工确认",
    },
    "hard_gate_leak": {
        "title": "硬项泄漏修复（命中词→判例匹配）",
        "root_refs": ["decision_rules.json R01-R11（判例单一权威源）",
                      "root-causes.md #4 det 硬项只检测不修复",
                      "root-causes.md #8 A 路线边界：陆烬修炼样式动作"],
        "steps": [
            "以命中词 {term} 对照 R01-R11 判例：命中 forbidden 类（R01 传授既成/R03 承诺传授/"
            "R04 现场体感）→ 在 director 台词级约束或 scope_gate 注入修复，禁止扩词表",
            "命中 allowed/hint 类 → 确认是误拦截还是密度问题，记录进 backlog",
            "未命中任何判例 → 属于 bible 外新设定，标记需人工确认（唯一人工点，不自动修复）",
            "修复后补该判例的回归测试（构造命中句断言放行/拦截）",
        ],
        "gate_note": "禁止为单次命中无限加词（concept_unlocks/判例条款化纪律）；判例外一律人工",
    },
}

_STATIC_CARD = """
## 验收门禁（必须全部通过才可关闭本卡）
1. pyflakes 全包扫描（含 tests）0 处 undefined name
   `python -m pyflakes $(find novel_engine -name "*.py")`（Windows 用 pyflakes 逐个目录）
2. 全量测试：`python -X utf8 -m pytest novel_engine/tests -q`
   失败仅允许 7 项基线：6 个 agnes 环境差异（test_factory_config /
   test_llm_client_streaming / test_novel_engine / test_switch_llm /
   test_round27_phase_models 两例）+ 1 个已知 pre-existing
   （test_same_location_cluster_shared_objects_not_flagged）
3. 行为改动（改变生成/发布语义）须人工确认；机制改动（接线/修复通道/规则注入）可自动
4. 修复后复跑/复现目标章：该失败分类应消失或转移（用 batch_audit 复核）
5. 结论写回 backlog 唯一正本（packages/coding-agent/skills/novel-engine/docs/superpowers/quality-backlog.md）
"""


def build_card(category: str, rec: dict, term: str | None, log_name: str) -> str:
    tpl = _TEMPLATES[category]
    ch = rec["chapter"]
    score = rec.get("score")
    note = rec.get("note", "")
    lines = [
        f"# 任务卡 ch{ch}: {tpl['title']}",
        "",
        f"- 批次日志: `{log_name}`",
        f"- 分类: {category}  得分: {score if score is not None else 'None'}",
        f"- 失败信号: {note[:150]}",
        f"- 命中词: {term if term else '—'}",
        "",
        "## 根因模板引用",
    ]
    for r in tpl["root_refs"]:
        lines.append(f"- {r}")
    lines += ["", "## 修复步骤"]
    for i, s in enumerate(tpl["steps"], 1):
        lines.append(f"{i}. {s.format(term=term or '(无)')}")
    lines += ["", "## 门禁注记"]
    lines.append(tpl["gate_note"])
    lines += [_STATIC_CARD]
    lines += ["", f"- 生成时间: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}"]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Task card generator (stage 3).")
    parser.add_argument("log", help="run_volume_*.log path")
    parser.add_argument("--out", default=str(_OUT_DEFAULT), help="task_cards output dir")
    parser.add_argument("--no-write", action="store_true", help="只打印不落盘")
    args = parser.parse_args(argv)

    log_path = Path(args.log)
    if not log_path.exists():
        print(f"[error] log not found: {log_path}")
        return 2
    recs = parse_log(log_path)
    if not recs:
        print(f"[error] no chapter END lines parsed from {log_path.name}")
        return 2

    out_dir = Path(args.out)
    cards: list[tuple[str, str]] = []
    for rec in recs:
        cat, _, term = classify(rec)
        if cat not in _TEMPLATES:
            continue  # pass_* / below_threshold 不生成卡
        card = build_card(cat, rec, term, log_path.name)
        name = f"task_card_ch{rec['chapter']:03d}_{cat}.md"
        cards.append((name, card))
        print(f"[card] {name}")

    if not cards:
        print("[info] 该批次无失败章需任务书")
        return 0

    if not args.no_write:
        out_dir.mkdir(parents=True, exist_ok=True)
        for name, card in cards:
            (out_dir / name).write_text(card, encoding="utf-8")
        idx = out_dir / "index.md"
        idx_lines = [
            "# 任务卡索引（自动生成）",
            "",
            f"- 来源批次: `{log_path.name}`  生成时间: "
            f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}",
            "",
        ]
        for name, _ in cards:
            idx_lines.append(f"- [{name}]({name})")
        idx.write_text("\n".join(idx_lines) + "\n", encoding="utf-8")
        print(f"[write] {len(cards)} 张任务卡已写入 {out_dir}")
    return 1  # 有失败章需处理


if __name__ == "__main__":
    raise SystemExit(main())
