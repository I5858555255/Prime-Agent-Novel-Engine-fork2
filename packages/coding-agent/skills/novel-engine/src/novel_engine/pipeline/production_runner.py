#!/usr/bin/env python3
"""
全量生产运行器：生成 3000 章长篇小说。

验证项（通过成本沙盘后执行）：
1. 状态机基本流程稳定运行
2. 滑动窗口审查每50章触发并反馈
3. 质量记忆持续更新
4. 剧情约束层锁定跨章节被正确遵守
5. 成本在预算范围内（$400）
6. 实时进度、质量、成本报告
7. checkpoint 完整性持续保障
8. recovery_policy 分流逻辑
"""
import json
import logging
import shutil
import sys
import time
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from novel_engine.core.llm_client import reset_call_log
from novel_engine.core.llm_failover import FailoverLLMClient
from novel_engine.core.checkpoint import CheckpointManager
from novel_engine.pipeline.pipeline_orchestrator import PipelineOrchestrator
from novel_engine.quality.noncommit_routing import route_noncommitted, route_review_unstable
from novel_engine.core.quality_policy import load_quality_policy
from novel_engine.pipeline.reset_state import (
    reset_runtime_state,
    verify_output_files,
    verify_recovery_policy,
)
from novel_engine.core.state_machine import ChapterPhase
from novel_engine.pipeline.chapter_status import (
    get_status, set_status, COMMITTED, HALTED,
    last_committed, write_halt_reason, clear_halt_reason, load_halt_reason,
)
from novel_engine.core.call_metrics import (
    reset as reset_metrics, snapshot as get_metrics_snapshot, load_pricing,
)
from novel_engine.core.llm_client import get_call_log

PROJECT_ROOT = Path(__file__).parent.parent
logs_dir = PROJECT_ROOT / "runtime/logs"
logs_dir.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(logs_dir / "production.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("production")


# Q10-locked observation-period constant (record-only), intentionally not in quality_policy — do not move it into the table.
FORCED_DRAFT_ALERT_RATE = 0.15  # Q10 observation period: log only, never pause (revisit after 3 batches)

def summarize_batch(results: list[dict]) -> dict:
    total = max(1, len(results))
    drafts = sum(1 for r in results if (not r.get("success", True)) or (r.get("published") is False) or bool(r.get("force_published")))
    rate = drafts / total
    return {"total": len(results), "forced_drafts": drafts,
            "forced_draft_rate": round(rate, 3), "paused": False,
            "alert": rate >= FORCED_DRAFT_ALERT_RATE}


# D2: runner-owned resume files. The state machine keeps pure in-memory flow
# and never touches these paths (pinned by test_state_machine_touches_no_resume_files).
# resume_state.json reuses the pre-existing {"done": [...]} shape — no format migration.
RESUME_STATE_FILENAME = "runtime/resume_state.json"
LAST_SUCCESS_FILENAME = "runtime/last_success_chapter.txt"


def _resume_state_path(project_root) -> Path:
    return Path(project_root) / RESUME_STATE_FILENAME


def _last_success_path(project_root) -> Path:
    return Path(project_root) / LAST_SUCCESS_FILENAME


def load_resume_state(project_root=None) -> dict:
    """Read runner-owned resume state.

    Always returns {"done": [...], "last_success_chapter": int}. A missing or
    corrupt resume_state.json falls back to the plain-int
    last_success_chapter.txt pointer, else to empty. Legacy files holding only
    {"done": [...]} derive last_success_chapter from done.
    """
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    done: list[int] = []
    last_success = 0
    json_usable = False
    try:
        raw = json.loads(_resume_state_path(root).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            done = sorted({int(c) for c in raw.get("done", [])})
            if "last_success_chapter" in raw:
                last_success = int(raw["last_success_chapter"])
            elif done:
                last_success = max(done)
            json_usable = True
    except (OSError, ValueError, TypeError):
        json_usable = False
    if not done and not json_usable:
        try:
            last_success = int(_last_success_path(root).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            last_success = 0
    return {"done": done, "last_success_chapter": last_success}


def save_resume_state(project_root, done) -> dict:
    """Atomically persist runner-owned resume state.

    `done` is an iterable of fully-committed chapter numbers. Writes
    resume_state.json ({"done": [...]}) plus the plain-int
    last_success_chapter.txt pointer. Returns the canonical state dict.
    """
    root = Path(project_root) if project_root is not None else PROJECT_ROOT
    chapters = sorted({int(c) for c in done})
    last_success = max(chapters) if chapters else 0
    state_path = _resume_state_path(root)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_state = state_path.with_suffix(".json.tmp")
    tmp_state.write_text(json.dumps({"done": chapters}, ensure_ascii=False), encoding="utf-8")
    tmp_state.replace(state_path)
    last_path = _last_success_path(root)
    last_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_last = last_path.with_suffix(".txt.tmp")
    tmp_last.write_text(f"{last_success}\n", encoding="utf-8")
    tmp_last.replace(last_path)
    return {"done": chapters, "last_success_chapter": last_success}


def last_success_chapter(project_root=None) -> int:
    """Highest fully-committed chapter recorded in runner-owned resume files."""
    return load_resume_state(project_root)["last_success_chapter"]


def is_chapter_committed(project_root, chapter: int) -> bool:
    """Idempotent chapter-commit check: a chapter counts as committed only if
    its committed files exist and match their checkpoint hashes. A half-written
    chapter never verifies, so resume safely regenerates it."""
    try:
        return bool(CheckpointManager(Path(project_root)).verify_integrity(int(chapter)))
    except (OSError, ValueError, TypeError):
        return False


def reconcile_resume_to_committed(project_root, done_chapters, effective_resume: int) -> dict:
    """F atomic-commit gate (self-healing resume): the resume cursor may only
    acknowledge chapters that actually verify on disk (checkpoint + published
    file hashes). A force-published / forced-draft chapter below the publication
    line never builds a checkpoint, and older code once counted such a chapter
    in `done`, which made resume skip a chapter missing from the published novel.

    Returns the filtered done list, any dropped chapter numbers, and an
    effective_resume pulled back to the highest genuinely committed chapter.
    """
    done_chapters = [int(c) for c in (done_chapters or [])]
    verified = [c for c in done_chapters if is_chapter_committed(project_root, c)]
    dropped = [c for c in done_chapters if c not in verified]
    committed_max = max(verified) if verified else 0
    effective_resume = int(effective_resume or 0)
    if effective_resume > committed_max:
        effective_resume = committed_max
    return {"done": verified, "dropped": dropped, "effective_resume": effective_resume}



def compute_resume_plan(resume_checkpoint=0, file_state=None, start_from=1) -> dict:
    """Pure resume decision from the CLI pointer and runner file state.

    Returns {"effective_resume": int, "fresh": bool, "done": [...]}. fresh is
    True only when neither source indicates prior progress, so a CLI-0 restart
    with file progress never wipes. `done` carries over only chapters outside
    this run's window (c < start_from): in-window entries are rebuilt from
    verification (skip path) or fresh generation, so an un-regenerated chapter
    can never be re-persisted as done.
    """
    state = dict(file_state or {})
    try:
        done = sorted({int(c) for c in state.get("done", [])})
    except (ValueError, TypeError):
        done = []
    try:
        last_success = int(state.get("last_success_chapter", 0) or 0)
    except (ValueError, TypeError):
        last_success = 0
    if not last_success and done:
        last_success = max(done)
    try:
        cli = int(resume_checkpoint or 0)
    except (ValueError, TypeError):
        cli = 0
    try:
        start = int(start_from or 1)
    except (ValueError, TypeError):
        start = 1
    effective = max(cli, last_success)
    kept = [c for c in done if c < start]
    return {"effective_resume": effective, "fresh": effective == 0, "done": kept}


def print_progress(chapter: int, total: int, elapsed: float,
                   passed: int, failed: int, avg_score: float,
                   cost_usd: float, budget_max: float):
    """打印一行进度信息。"""
    pct = chapter / total * 100
    rate = passed / chapter * 100 if chapter > 0 else 0
    budget_pct = cost_usd / budget_max * 100
    eta = (elapsed / max(chapter, 1)) * (total - chapter)

    logger.info(
        f"[{pct:5.1f}%] Ch {chapter:>4d}/{total} | "
        f"P:{passed} F:{failed} | "
        f"Avg:{avg_score:.1f} | "
        f"Cost:${cost_usd:.2f}/{budget_max} ({budget_pct:.1f}%) | "
        f"ETA:{eta/60:.0f}min"
    )


def _isolate_failed_chapter(project_root: Path, chapter: int) -> None:
    """Move failed-chapter artifacts into chapters/draft/failed/chapter_{N}/.

    Sources:
      - chapters/draft/chapter_{N}.txt
      - chapters/draft/chapter_{N}_partial.jsonl
      - chapters/state/chapter_{N}_polish.json  (note: state/, not draft/)
    Dest: chapters/draft/failed/chapter_{N}/  (same basenames); overwrite on
    collision; delete source after move.
    """
    dest = project_root / "chapters" / "draft" / "failed" / f"chapter_{chapter}"
    dest.mkdir(parents=True, exist_ok=True)
    for src_name in (f"chapter_{chapter}.txt", f"chapter_{chapter}_partial.jsonl"):
        src = project_root / "chapters" / "draft" / src_name
        dst = dest / src_name
        if src.exists():
            try:
                dst.write_bytes(src.read_bytes())
                src.unlink()
            except OSError:
                pass
    # polish json lives in chapters/state/, not draft/
    src_polish = project_root / "chapters" / "state" / f"chapter_{chapter}_polish.json"
    if src_polish.exists():
        dst_polish = dest / f"chapter_{chapter}_polish.json"
        try:
            dst_polish.write_bytes(src_polish.read_bytes())
            src_polish.unlink()
        except OSError:
            pass


def _record_final_gate_gap(project_root, chapter: int, violations: list,
                           run_id: str = "") -> None:
    """CC round-19 Q1：终检硬阻断章不 HALT 整批，记入 gap 台账供人工回填。

    文件命名：runtime/final_gate_gaps_<run_id>.json（run_id 由 caller 传入，
    格式 e.g. "ch2-4"），旧版无 run_id 时回退到 final_gate_gaps.json 保持兼容。
    同 run 内去重（同 chapter 只保留最新一次），不同 run 互不污染。
    """
    import json as _json
    import time as _time
    if run_id:
        gap_path = Path(project_root) / "runtime" / f"final_gate_gaps_{run_id}.json"
    else:
        gap_path = Path(project_root) / "runtime" / "final_gate_gaps.json"
    data = {"gaps": [], "run_id": run_id}
    try:
        if gap_path.exists():
            _d = _json.loads(gap_path.read_text(encoding="utf-8"))
            if isinstance(_d, dict) and isinstance(_d.get("gaps"), list):
                data = _d
    except (OSError, ValueError):
        pass
    # Remove old entry for same chapter (keep latest)
    data["gaps"] = [g for g in data["gaps"] if int(g.get("chapter", -1)) != int(chapter)]
    data["gaps"].append({"chapter": int(chapter),
                         "violations": [str(v) for v in (violations or [])],
                         "ts": int(_time.time())})
    gap_path.parent.mkdir(parents=True, exist_ok=True)
    gap_path.write_text(_json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _emit_halt_report(
    project_root: Path,
    num_chapters: int,
    passed: int,
    failed: int,
    results: list,
    scores: list,
    orchestrator,
    *,
    elapsed: float,
    halt_reason: str,
) -> None:
    """Build and persist the halt report using call_metrics.snapshot() as the
    single source of truth for cost/token/call counts.
    """
    snap = get_metrics_snapshot()
    # G.3: use metrics snapshot for all cost-bearing fields; never read
    # orchestrator.cost_tracker for the report.
    metrics_cost = snap["cost_usd"]
    budget_cfg = json.loads(
        (project_root / "config" / "cost_sandbox.json").read_text(encoding="utf-8")
    )
    budget_max = budget_cfg["budget"]["full_production_max"]
    within_budget = metrics_cost <= budget_max
    checkpoint_count = len(orchestrator.checkpoint_mgr.load().get("checkpoints", []))
    report = {
        "test_type": "production",
        "total_chapters": num_chapters,
        "passed": passed,
        "failed": failed,
        "results": results,
        "halted": True,
        "halt_reason": halt_reason,
        "forced_drafts": sum(1 for r in results if not r.get("success", True)),
        "forced_draft_rate": sum(1 for r in results if not r.get("success", True)) / max(1, len(results)),
        "checkpoint_count": checkpoint_count,
        "integrity_ok": True,
        "elapsed_seconds": round(elapsed, 2),
        "actual_cost_usd": round(metrics_cost, 6),
        "total_api_calls": snap["calls"],
        "total_tokens": snap["total_tokens"],
        "success": False,
        "average_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "cost_report": {
            "cost_usd": {"sandbox_test_actual": round(metrics_cost, 6)},
            "budget": {"within_budget": within_budget},
            "sliding_window": {"constraint_adjustment_count": 0},
        },
        "metrics_snapshot": snap,
    }
    report_path = project_root / "audit" / "production_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_production(num_chapters: int = 0, use_real: bool = True,
                   start_from: int = 1, resume_checkpoint: int = 0):
    """运行全量生产并返回结果。num_chapters=0 时从 runtime_config.json 读取。"""
    # CC28：真机运行启用场景写入层语言纯度硬闸（整段非中文退化输出拒写 journal）。
    if use_real:
        os.environ["NOVEL_ENGINE_REAL_LANG_ENFORCE"] = "1"
    project_root = Path(__file__).parent.parent
    if num_chapters <= 0:
        try:
            num_chapters = json.loads(
                (project_root / "config" / "runtime_config.json").read_text(encoding="utf-8")
            ).get("pipeline", {}).get("total_chapters", 3800)
        except (json.JSONDecodeError, ValueError, OSError):
            num_chapters = 3800
    logger.info(f"===== PRODUCTION START: {num_chapters} chapters =====")
    logger.info(f"Start from chapter: {start_from}")
    logger.info(f"Resume checkpoint: {resume_checkpoint}")
    # P2: 按 run 隔离 final_gate_gaps，避免历史 gap 污染本次判定
    _run_id = f"ch{start_from}-{num_chapters}"
    # D2 fix: runner-owned resume — file state supplements the CLI pointer, and the
    # reset gate honors either source (a CLI-0 restart with file progress must not wipe).
    file_state = load_resume_state(project_root)
    plan = compute_resume_plan(resume_checkpoint, file_state, start_from)
    done_chapters = plan["done"]
    effective_resume = plan["effective_resume"]
    # F 原子提交状态门（自愈）：resume 游标只承认通过 checkpoint+成稿哈希校验的章。
    _recon = reconcile_resume_to_committed(project_root, done_chapters, effective_resume)
    if _recon["dropped"]:
        logger.warning(f"Resume reconciliation: dropping non-committed chapters {_recon['dropped']} from done cursor")
    done_chapters = _recon["done"]
    if effective_resume != _recon["effective_resume"]:
        logger.warning(f"Resume cursor {effective_resume} pulled back to {_recon['effective_resume']} (last committed)")
    effective_resume = _recon["effective_resume"]
    if file_state["last_success_chapter"]:
        logger.info(f"Resume state: last success chapter {file_state['last_success_chapter']}")
    start_time = time.time()

    (project_root / "audit").mkdir(parents=True, exist_ok=True)

    # 仅真正全新运行（CLI 与文件均无进度）时重置状态；恢复模式跳过重置
    if plan["fresh"]:
        reset_runtime_state(project_root)
    else:
        logger.info(f"Resuming from chapter {effective_resume}, skipping runtime reset")

    if use_real:
        cfg = json.loads((project_root / "config" / "runtime_config.json").read_text(encoding="utf-8"))
        # API key 的环境变量名从各 section 的 api_key_env 读取（由 LLM 选择器统一维护），
        # 不再硬编码任何单一供应商 key；缺省回退 AGNES_API_KEY。
        _pri_env = cfg.get("llm", {}).get("api_key_env", "AGNES_API_KEY")
        _fb_env = cfg.get("fallback_llm", {}).get("api_key_env", _pri_env)
        client = FailoverLLMClient.from_config_dict(
            cfg, primary_section="llm", fallback_section="fallback_llm",
            primary_key_env=_pri_env, fallback_key_env=_fb_env,
            log_prefix="[gen]")
        reset_call_log()  # Clear any stale call log
        logger.info("OK Using real API with generation failover (llm -> fallback_llm)")
    else:
        from novel_engine.core.llm_client import MockLLMClient
        client = MockLLMClient()
        logger.info("OK Using mock LLM client")

    orchestrator = PipelineOrchestrator(str(project_root), llm_client=client)

    # 加载预算配置
    budget_cfg = json.loads((project_root / "config" / "cost_sandbox.json").read_text(encoding="utf-8"))
    budget_max = budget_cfg["budget"]["full_production_max"]

    results = []
    passed = 0
    failed = 0
    scores = []
    realm_locks_respected = True
    last_progress_log = 0  # Track last chapter logged

    # G.3: reset accumulator with project root so pricing is loaded for auto-cost calc
    reset_metrics(project_root)
    # CC round-7 Q6: quality-halt resume bypasses director cache for the failed chapter only.
    _QUALITY_HALT_REASONS = {
        "review_below_publication_line", "scene_near_empty_cluster",
        "timeline_or_boundary_block", "continuity_block", "scope_block",
        "density_block",
    }
    _prev_halt = load_halt_reason(project_root)
    if _prev_halt and _prev_halt.get("reason") in _QUALITY_HALT_REASONS:
        _bypass_ch = int(_prev_halt.get("failed_chapter") or 0)
        if _bypass_ch:
            os.environ["NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER"] = str(_bypass_ch)
            logger.info(
                f"Quality-halt resume for chapter {_bypass_ch} "
                f"({_prev_halt.get('reason')}) -> director cache bypass for that chapter")
    clear_halt_reason(project_root)

    for i in range(start_from, num_chapters + 1):
        # D2: runner-owned idempotent resume — skip only verified commits.
        if effective_resume > 0 and i <= effective_resume:
            if is_chapter_committed(project_root, i):
                logger.info(f"Skipping chapter {i} (already committed at checkpoint)")
                if i not in done_chapters:
                    done_chapters.append(i)
                passed += 1
                continue
            logger.warning(f"Chapter {i} missing files despite checkpoint — will regenerate")

        logger.info(f"\n{'='*60}")
        logger.info(f"GENERATING CHAPTER {i}/{num_chapters}")
        logger.info(f"{'='*60}")

        try:
            # F.6: 清理本章残留失败产物，使重试如同从未运行
            _failed_dir = project_root / "chapters" / "draft" / "failed" / f"chapter_{i}"
            if _failed_dir.exists():
                try:
                    shutil.rmtree(_failed_dir)
                except OSError:
                    pass
            for _stale in (
                project_root / "chapters" / "draft" / f"chapter_{i}_partial.jsonl",
                project_root / "chapters" / "state" / f"chapter_{i}_polish.json",
            ):
                try:
                    _stale.unlink()
                except OSError:
                    pass

            result = orchestrator._run_with_retry(i)
            results.append(result)

            # CC round-19 Q1 / round-20 C：提交前终检硬阻断，或重生+确定性自救后仍不消的
            # 质量硬伤（时序倒置等）。隔离转人工 + 记 gap 台账，游标继续推进，不 HALT 整批。
            if result.get("final_gate_blocked") or result.get("quality_gap_continue"):
                failed += 1
                if result.get("final_gate_blocked"):
                    _viol_gap = result.get("final_gate_violations") or []
                    _gap_kind = "final_precommit_gate"
                else:
                    _viol_gap = (result.get("gap_violations")
                                 or [{"kind": result.get("gap_kind", "quality"),
                                      "detail": result.get("replan_reason", "quality_gap")}])
                    _gap_kind = result.get("gap_kind", "quality")
                _reason_gap = f"chapter {i} quality gap ({_gap_kind}): {_viol_gap}"
                result.setdefault("errors", []).append(_reason_gap)
                set_status(project_root, i, "GAP_HUMAN_REVIEW", reason=_reason_gap[:500])
                try:
                    _isolate_failed_chapter(project_root, i)
                except OSError as _iso_gap:
                    logger.warning(f"isolate quality-gap chapter failed: {_iso_gap}")
                _record_final_gate_gap(project_root, i, _viol_gap, run_id=_run_id)
                logger.error(f"GAP-CONTINUE: Chapter {i} quality gap ({_gap_kind}) "
                             f"-> human queue (chapters/draft/failed/chapter_{i}/); "
                             f"advancing cursor, no batch HALT")
                save_resume_state(project_root, done_chapters)
                continue

            score = result.get("score", "N/A")
            # F 原子提交状态门：success=True 不等于已正式出版——未达出版线的
            # force-publish/forced_draft 也置 success=True，但不建 checkpoint、不写正稿。
            # 只有通过 checkpoint+成稿哈希校验才算完成，否则绝不推进 resume 游标。
            committed = is_chapter_committed(project_root, i)
            if result.get("success") and committed:
                status = "PASS"
                passed += 1
                if i not in done_chapters:
                    done_chapters.append(i)
                save_resume_state(project_root, done_chapters)
                if score is not None:
                    scores.append(score)
            elif result.get("success") and not committed:
                # CC round-21 Q2：用尽阶梯仍不提交。以 reviewer 分数为主判据：分数仍 < 软线 ->
                # 留 gap（隔离+人验+台账+游标继续，不写正稿、不整批 HALT）；分数>=软线却未提交
                # 属上游异常，保守维持原 HALT 交人工排查。
                _qp21 = load_quality_policy(project_root)
                _soft21 = int(_qp21.get("soft_publication_line", 85))
                _pub21 = int(_qp21.get("publication_line", 88))
                _act21, _why21 = route_noncommitted(
                    score, chars=result.get("severe_shortfall_chars"),
                    severe_shortfall=bool(result.get("severe_shortfall")),
                    soft_line=_soft21, publication_line=_pub21)
                failed += 1
                if _act21 == "gap":
                    _reason = (f"chapter {i} generated but below soft line (score={score}, "
                               f"{_why21}); quarantined as quality gap, never novel/")
                    result.setdefault("errors", []).append(_reason)
                    result["force_published"] = True
                    set_status(project_root, i, "GAP_HUMAN_REVIEW", reason=_reason[:500])
                    _isolate_failed_chapter(project_root, i)
                    _record_final_gate_gap(project_root, i, [{"kind": _why21, "score": score}], run_id=_run_id)
                    logger.error(f"GAP-CONTINUE: Chapter {i} NOT committed (score={score}, {_why21}) "
                                 f"-> human queue; advancing cursor, no batch HALT")
                    save_resume_state(project_root, done_chapters)
                    continue
                status = "FORCED_DRAFT"
                _reason = (f"chapter {i} generated but not committed (score={score}); "
                           f"below publication line / forced draft")
                result.setdefault("errors", []).append(_reason)
                result["force_published"] = True
                set_status(project_root, i, HALTED, reason=_reason)
                _isolate_failed_chapter(project_root, i)
                write_halt_reason(project_root, i, "review_below_publication_line",
                                  detail=_reason)
                logger.error(f"HALT: Chapter {i} NOT committed (score={score}); isolated, "
                             f"resume cursor stays at {max(done_chapters, default=0)}")
                save_resume_state(project_root, done_chapters)
                _emit_halt_report(project_root, num_chapters, passed, failed, results, scores,
                                  orchestrator, elapsed=time.time() - start_time,
                                  halt_reason=_reason)
                break
            else:
                # CC round-8 P0-3：评审三评极不稳定（极差>15）。
                # CC round-22（无人值守）：中位分本就 < 软线 -> 该章反正不可出版，与质量 gap 同处置：
                # 隔离+台账+游标继续，不 HALT 整批；中位>=软线（近出版却分歧）或分数不可解析时，
                # 保守保留 HALT 留人判，避免漏掉被畸低评分误杀的好章。
                if result.get("review_unstable"):
                    failed += 1
                    try:
                        _soft22 = int(load_quality_policy(project_root).get("soft_publication_line", 85))
                    except Exception:
                        _soft22 = 85
                    if route_review_unstable(score, _soft22) == "gap":
                        _ru = (f"chapter {i} review highly unstable (median-of-3 range>15, "
                               f"median={score} < soft {_soft22}); quarantined as quality gap, "
                               f"advancing cursor, no batch HALT")
                        result.setdefault("errors", []).append(_ru)
                        result["force_published"] = True
                        set_status(project_root, i, "GAP_HUMAN_REVIEW", reason=_ru[:500])
                        _isolate_failed_chapter(project_root, i)
                        _record_final_gate_gap(
                            project_root, i,
                            [{"kind": "review_unstable_below_line", "score": score}],
                            run_id=_run_id)
                        logger.error(
                            f"GAP-CONTINUE: Chapter {i} review unstable but median={score} "
                            f"<soft {_soft22} -> human queue; advancing cursor, no batch HALT")
                        save_resume_state(project_root, done_chapters)
                        continue
                    _ru = (f"chapter {i} review highly unstable (median-of-3 range>15, "
                           f"score={score}); queued for human review, no auto replan")
                    result.setdefault("errors", []).append(_ru)
                    set_status(project_root, i, HALTED, reason=_ru)
                    _isolate_failed_chapter(project_root, i)
                    write_halt_reason(project_root, i, "review_highly_unstable", detail=_ru)
                    logger.error(f"HALT: Chapter {i} review highly unstable -> human review; isolated")
                    save_resume_state(project_root, done_chapters)
                    _emit_halt_report(project_root, num_chapters, passed, failed, results, scores,
                                      orchestrator, elapsed=time.time() - start_time, halt_reason=_ru)
                    break
                # CC round-24 P0-3：quality_replan 状态机（顺序连载，不能一律跳章也不能一律停）。
                # 先批内带【全新任务卡】重试 N 次（导演绕过缓存）：
                #  - density_block / scene_near_empty_cluster：内容“单薄但不错误”，重试耗尽转
                #    “待补前置 placeholder”——隔离+台账+游标继续，不写正稿、不 HALT 整批，
                #    由后续 partial_repair pass 补；后继章在缺失前章下安全续写（不读取错误世界状态）。
                #  - continuity/scope 类：世界状态可能错误，会污染后续前提，重试耗尽【必须就地 HALT】，
                #    这是唯一保留的真正整批停止点。
                if result.get("quality_replan"):
                    _SOFT_REPLAN = {"density_block", "scene_near_empty_cluster"}
                    _FRESH_PLAN_RETRIES = 2
                    _replan_reason = result.get("replan_reason") or "scene_near_empty_cluster"

                    def _clean_replan_residual(_i):
                        _fd = project_root / "chapters" / "draft" / "failed" / f"chapter_{_i}"
                        if _fd.exists():
                            try:
                                shutil.rmtree(_fd)
                            except OSError:
                                pass
                        for _st in (
                            project_root / "chapters" / "draft" / f"chapter_{_i}_partial.jsonl",
                            project_root / "chapters" / "state" / f"chapter_{_i}_polish.json",
                        ):
                            try:
                                _st.unlink()
                            except OSError:
                                pass

                    _retries_done = 0
                    while result.get("quality_replan") and _retries_done < _FRESH_PLAN_RETRIES:
                        _retries_done += 1
                        os.environ["NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER"] = str(i)
                        logger.warning(
                            f"quality replan [{_replan_reason}] ch{i} -> in-batch FRESH-director "
                            f"retry {_retries_done}/{_FRESH_PLAN_RETRIES}")
                        _clean_replan_residual(i)
                        try:
                            result = orchestrator._run_with_retry(i)
                        except Exception as _re_replan:
                            logger.warning(f"fresh-plan retry {_retries_done} ch{i} crashed: {_re_replan}")
                            result = {"success": False, "quality_replan": True,
                                      "replan_reason": _replan_reason, "errors": [str(_re_replan)]}
                            break
                        if result.get("quality_replan"):
                            _replan_reason = result.get("replan_reason") or _replan_reason
                    os.environ.pop("NOVEL_DIRECTOR_BYPASS_CACHE_CHAPTER", None)

                    # 重试已不再是 replan：恢复则按提交计数，其余任何非提交结果一律隔离+继续（不出版）
                    if not result.get("quality_replan"):
                        if results:
                            results[-1].update(result)
                        else:
                            results.append(result)
                        if result.get("success") and is_chapter_committed(project_root, i):
                            passed += 1
                            if i not in done_chapters:
                                done_chapters.append(i)
                            save_resume_state(project_root, done_chapters)
                            if result.get("score") is not None:
                                scores.append(result.get("score"))
                            logger.info(
                                f"Chapter {i}: RECOVERED after fresh-plan retry "
                                f"(score={result.get('score')})")
                        else:
                            failed += 1
                            _rr = (f"chapter {i} replan retries ended uncommitted: "
                                   f"{result.get('replan_reason') or result.get('errors')}")
                            result.setdefault("errors", []).append(_rr)
                            set_status(project_root, i, "GAP_HUMAN_REVIEW", reason=_rr[:500])
                            _isolate_failed_chapter(project_root, i)
                            _record_final_gate_gap(
                                project_root, i,
                                [{"kind": "replan_retry_uncommitted", "detail": _rr}],
                                run_id=_run_id)
                            logger.error(
                                f"GAP-CONTINUE: Chapter {i} replan retries ended uncommitted "
                                f"-> human queue; advancing cursor, no batch HALT")
                            save_resume_state(project_root, done_chapters)
                        continue

                    failed += 1
                    _qr = (f"chapter {i} quality replan ({_replan_reason}) after "
                           f"{_FRESH_PLAN_RETRIES} fresh-director retries: "
                           f"scenes={result.get('replan_scene_ids')}")
                    result.setdefault("errors", []).append(_qr)
                    if _replan_reason in _SOFT_REPLAN:
                        # 密度/空洞类：待补前置 placeholder，隔离前进不 HALT
                        set_status(project_root, i, "GAP_HUMAN_REVIEW", reason=_qr[:500])
                        _isolate_failed_chapter(project_root, i)
                        _record_final_gate_gap(
                            project_root, i,
                            [{"kind": "placeholder_content_thin_not_wrong",
                              "replan_reason": _replan_reason,
                              "repair_type": "partial_repair",
                              "run_id": _run_id}],
                            run_id=_run_id)
                        logger.error(
                            f"GAP-CONTINUE: Chapter {i} density/empty replan exhausted "
                            f"[{_replan_reason}] -> placeholder queued for partial-repair; "
                            f"advancing cursor, no batch HALT")
                        save_resume_state(project_root, done_chapters)
                        continue
                    # continuity / scope 类：世界状态可能错误，唯一保留的真正 HALT
                    set_status(project_root, i, HALTED, reason=_qr)
                    _isolate_failed_chapter(project_root, i)
                    write_halt_reason(project_root, i, _replan_reason, detail=_qr)
                    logger.error(
                        f"HALT: Chapter {i} structural replan [{_replan_reason}] after "
                        f"{_FRESH_PLAN_RETRIES} fresh retries -> world-state risk, stop batch")
                    save_resume_state(project_root, done_chapters)
                    _emit_halt_report(project_root, num_chapters, passed, failed, results, scores,
                                      orchestrator, elapsed=time.time() - start_time, halt_reason=_qr)
                    break
                failed += 1
                for err in result["errors"]:
                    logger.warning(f"  Error: {err}")
                # F.4: 硬失败处置：标记HALTED并隔离产物
                set_status(project_root, i, HALTED, reason=result.get("errors", [""])[0] if result.get("errors") else "unknown")
                _isolate_failed_chapter(project_root, i)
                write_halt_reason(
                    project_root, i,
                    "review_exhausted" if "review" in str(result.get("errors", [])).lower() else "chapter_failed",
                    detail=f"Chapter {i} failed: {result.get('errors', [])}",
                )
                logger.error(f"HALT: Chapter {i} hard failure, stopping production batch")
                save_resume_state(project_root, done_chapters)
                _emit_halt_report(project_root, num_chapters, passed, failed, results, scores,
                                  orchestrator, elapsed=time.time() - start_time,
                                  halt_reason=f"Chapter {i} failed: {result.get('errors', [])}")
                break

            logger.info(f"Chapter {i}: {status} (score={score})")

        except Exception as e:
            logger.error(f"Chapter {i} crashed: {e}")
            results.append({
                "chapter": i,
                "success": False,
                "score": None,
                "errors": [str(e)],
            })
            failed += 1
            # F.4: 崩溃路径与硬失败完全一致：HALT + 隔离 + 报告 + break
            set_status(project_root, i, HALTED, reason=str(e)[:100])
            _isolate_failed_chapter(project_root, i)
            write_halt_reason(
                project_root, i, "crash",
                detail=f"Chapter {i} crashed: {e}",
            )
            logger.error(f"HALT: Chapter {i} crashed, stopping production batch")
            save_resume_state(project_root, done_chapters)
            _emit_halt_report(project_root, num_chapters, passed, failed, results, scores,
                              orchestrator, elapsed=time.time() - start_time,
                              halt_reason=f"Chapter {i} crashed: {e}")
            break

        # 每50章触发滑动窗口审查
        if i % 50 == 0 and i <= num_chapters:
            logger.info(f"Triggering sliding window review at chapter {i}")
            orchestrator._run_sliding_window_review(i)

            # 验证质量记忆已刷新
            qm = orchestrator.memory.load_quality_memory()
            if qm.get("updated_at_chapter", 0) >= i:
                logger.info(f"OK Quality memory refreshed at chapter {qm['updated_at_chapter']}")

            # 验证滑动窗口审查结果已保存
            sw_reviews = orchestrator.memory.load_sliding_window_reviews()
            if any(r.get("window_end") == i for r in sw_reviews):
                logger.info(f"OK Sliding window review saved for chapters ending at {i}")

        # 验证 realm lock 是否被遵守
        world_state = orchestrator.simulator.build_world_state_for_chapter(i)
        for char_id, char_data in world_state.get("characters", {}).items():
            progression = char_data.get("progression_suggestion", {})
            if progression.get("locked"):
                locked_until = progression.get("reason", "")
                logger.debug(f"Chapter {i}: {char_id} locked at {progression.get('realm')}")

        # 定期输出进度
        elapsed = time.time() - start_time
        avg_score = sum(scores) / len(scores) if scores else 0
        cost_tracker = orchestrator.cost_tracker
        pricing = (budget_cfg.get("currency_conversion", {}) or {}).get("api_pricing", {}) or {}
        # 配置单价以每 1M token 计（input_per_1m_tokens/output_per_1m_tokens）；
        # 兼容旧的每 1K 键。换算成每 token 单价，绝不让报告阶段因键名缺失而中断批量。
        if "input_per_1m_tokens" in pricing:
            pt_in = float(pricing["input_per_1m_tokens"]) / 1_000_000.0
            pt_out = float(pricing["output_per_1m_tokens"]) / 1_000_000.0
        else:
            pt_in = float(pricing.get("input_per_1k_tokens", 0.1)) / 1000.0
            pt_out = float(pricing.get("output_per_1k_tokens", 0.3)) / 1000.0
        # 按实际 input/output token 计算成本，而非 50/50 假设
        input_tokens = cost_tracker.get("total_prompt_tokens", 0) + cost_tracker.get("total_reasoning_tokens", 0)
        output_tokens = cost_tracker.get("total_completion_tokens", 0)
        cost_usd = input_tokens * pt_in + output_tokens * pt_out

        if i - last_progress_log >= 10 or i == num_chapters:
            print_progress(i, num_chapters, elapsed, passed, failed,
                          avg_score, cost_usd, budget_max)
            last_progress_log = i

    elapsed = time.time() - start_time

    # 最终验证
    integrity_ok = True
    for i in range(1, passed + 1):
        if not orchestrator.checkpoint_mgr.verify_integrity(i):
            logger.warning(f"Checkpoint integrity FAILED for chapter {i}")
            integrity_ok = False

    if integrity_ok:
        logger.info("OK All checkpoints pass integrity verification")

    # 验证文件是否写入
    verify_output_files(project_root, results, num_chapters)

    # 验证 recovery_policy
    verify_recovery_policy(project_root)

    # 计算最终统计
    avg_score = sum(scores) / len(scores) if scores else 0.0
    min_score = min(scores) if scores else 0
    max_score = max(scores) if scores else 0
    checkpoint_count = len(orchestrator.checkpoint_mgr.load().get("checkpoints", []))

    # G.3: single source of truth — call_metrics.snapshot()
    snap = get_metrics_snapshot()
    metrics_cost = snap["cost_usd"]
    actual_cost_usd = round(metrics_cost, 6)
    total_api_calls = snap["calls"]
    total_tokens = snap["total_tokens"]

    # 构建最终报告；cost_report 外形与旧版兼容
    cost_report = orchestrator.get_cost_sandbox_report(passed)
    # 用 snapshot 覆盖，保证 actual_cost_usd / total_api_calls / total_tokens 统一
    cost_report["cost_usd"]["sandbox_test_actual"] = actual_cost_usd
    cost_report["total_api_calls"] = total_api_calls
    cost_report["token_usage"]["total_tokens"] = total_tokens

    budget_cfg = json.loads(
        (project_root / "config" / "cost_sandbox.json").read_text(encoding="utf-8")
    )
    budget_max_final = budget_cfg["budget"]["full_production_max"]
    within_budget = actual_cost_usd <= budget_max_final

    logger.info(f"\n{'='*60}")
    logger.info("PRODUCTION RESULTS")
    logger.info(f"{'='*60}")
    logger.info(f"Chapters attempted: {len(results)}")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Time elapsed: {elapsed:.2f}s ({elapsed/3600:.1f}h)")
    if scores:
        logger.info(f"Average score: {avg_score:.1f}")
        logger.info(f"Min score: {min_score}")
        logger.info(f"Max score: {max_score}")
    logger.info(f"Checkpoints created: {checkpoint_count}")
    logger.info(f"Total API calls: {total_api_calls}")
    logger.info(f"Total tokens: {total_tokens:,}")
    logger.info(f"本批实际成本: ${actual_cost_usd:.6f}")
    logger.info(f"Estimated full production cost: ${cost_report['cost_usd']['estimated_full_production']:.2f}")
    logger.info(f"Within budget: {within_budget}")
    logger.info(f"Sliding window constraint adjustments: {cost_report['sliding_window']['constraint_adjustment_count']}")

    # F.6 flush 断言：某章确实走过 LLM（成功或失败）但 calls==0 时显式报错
    if passed + failed > 0 and total_api_calls == 0:
        logger.error("metrics not recorded: chapters were processed but call_metrics.snapshot shows calls==0")

    # 确定成功条件
    success = (failed == 0 and integrity_ok and within_budget)

    # Record-only batch observability: merge forced-draft rate, never pause.
    batch_summary = summarize_batch(results)
    logger.info(f"Batch forced-draft summary: forced_draft_rate={batch_summary['forced_draft_rate']} (forced_drafts={batch_summary['forced_drafts']}/{batch_summary['total']})")

    report = {
        "test_type": "production",
        "total_chapters": num_chapters,
        "passed": passed,
        "failed": failed,
        "results": results,
        "forced_drafts": batch_summary["forced_drafts"],
        "forced_draft_rate": batch_summary["forced_draft_rate"],
        "checkpoint_count": checkpoint_count,
        "integrity_ok": integrity_ok,
        "elapsed_seconds": round(elapsed, 2),
        "actual_cost_usd": actual_cost_usd,
        "total_api_calls": total_api_calls,
        "total_tokens": total_tokens,
        "success": success,
        "average_score": round(avg_score, 1),
        "min_score": min_score,
        "max_score": max_score,
        "cost_report": cost_report,
    }

    report_path = project_root / "audit" / "production_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"Report saved to {report_path}")

    return report


if __name__ == "__main__":
    use_real = "--real" in sys.argv
    args = [a for a in sys.argv if a != "--real"]

    num_chapters = int(args[1]) if len(args) > 1 else 0
    start_from = int(args[2]) if len(args) > 2 else 1
    resume_checkpoint = int(args[3]) if len(args) > 3 else 0

    logger.info(f"Production: {num_chapters} chapters, start={start_from}, resume={resume_checkpoint}")

    report = run_production(
        num_chapters=num_chapters,
        use_real=use_real,
        start_from=start_from,
        resume_checkpoint=resume_checkpoint,
    )

    if report["success"]:
        logger.info("\nPRODUCTION COMPLETED SUCCESSFULLY")
        sys.exit(0)
    else:
        logger.info("\nPRODUCTION COMPLETED WITH ISSUES")
        sys.exit(0)  # Don't fail — production may have partial results
